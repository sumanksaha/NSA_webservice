/**
 * Version Control UI (Phase 9).
 *
 * Drives the version-history page (app/version_control/templates/...):
 *  - loads the version list per doc type from /api/version-control/history/<id>
 *  - compares two versions (difflib-backed server diff) side by side
 *  - restores a document to a previous version (append-only snapshot)
 *  - creates a named branch/draft from any version
 *
 * CSRF: base.html patches window.fetch to attach the X-CSRFToken header, so
 * POSTs below do not need to set it manually.
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    var config = window.VC_CONFIG || {};
    var targetId =
        config.caseId !== null && config.caseId !== undefined
            ? config.caseId
            : config.adjudicationId;

    // The case_file/adjudication disambiguator: both tables autoincrement
    // from 1, so the API needs an explicit kind when the IDs could collide.
    var kindParam = config.caseType ? "?kind=" + encodeURIComponent(config.caseType) : "";
    var API_BASE = "/api/version-control/";

    var currentDocType = "petition";
    var versions = []; // summaries for the current doc type

    // --- Cached DOM ---
    var versionList = document.getElementById("vcVersionList");
    var versionCount = document.getElementById("vcVersionCount");
    var compareFrom = document.getElementById("vcCompareFrom");
    var compareTo = document.getElementById("vcCompareTo");
    var compareBtn = document.getElementById("vcCompareBtn");
    var diffSummary = document.getElementById("vcDiffSummary");
    var diffOutput = document.getElementById("vcDiffOutput");
    var branchModal = document.getElementById("vcBranchModal");
    var branchNameInput = document.getElementById("vcBranchName");
    var branchVersionNo = document.getElementById("vcBranchVersionNo");
    var branchConfirm = document.getElementById("vcBranchConfirm");
    var branchCancel = document.getElementById("vcBranchCancel");

    // -----------------------------------------------------------------------
    // Rendering helpers
    // -----------------------------------------------------------------------

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatDate(iso) {
        if (!iso) return "—";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleString();
    }

    function setStatus(message) {
        versionList.innerHTML = '<p class="vc-empty">' + escapeHtml(message) + "</p>";
        if (versionCount) versionCount.textContent = "";
    }

    // -----------------------------------------------------------------------
    // Version list
    // -----------------------------------------------------------------------

    function renderVersions() {
        if (!versions.length) {
            setStatus("No saved versions for this document type yet.");
            if (compareFrom) {
                compareFrom.innerHTML = '<option value="">—</option>';
                compareTo.innerHTML = '<option value="">—</option>';
            }
            return;
        }

        var rows = versions
            .map(function (v) {
                var createdBy = v.created_by
                    ? escapeHtml(v.created_by.username || "user#" + v.created_by.id)
                    : "—";
                var summary = escapeHtml(v.change_summary || "—");
                return (
                    "<tr>" +
                    '<td class="vc-version-no">v' +
                    v.version_number +
                    "</td>" +
                    '<td class="vc-meta">' +
                    formatDate(v.created_at) +
                    "</td>" +
                    '<td class="vc-meta">' +
                    createdBy +
                    "</td>" +
                    '<td class="vc-summary">' +
                    summary +
                    "</td>" +
                    '<td class="vc-actions">' +
                    '<button type="button" class="vc-btn" data-restore="' +
                    v.id +
                    '" data-version="' +
                    v.version_number +
                    '">Restore</button>' +
                    '<button type="button" class="vc-btn" data-branch="' +
                    v.id +
                    '" data-version="' +
                    v.version_number +
                    '">Branch</button>' +
                    "</td>" +
                    "</tr>"
                );
            })
            .join("");

        versionList.innerHTML =
            '<table class="vc-table">' +
            "<thead><tr><th>Version</th><th>Created</th><th>By</th><th>Summary</th><th>Actions</th></tr></thead>" +
            "<tbody>" +
            rows +
            "</tbody>" +
            "</table>";
        if (versionCount)
            versionCount.textContent =
                versions.length + (versions.length === 1 ? " version" : " versions");

        // Populate compare selects (newest first as returned by the API)
        var options = versions
            .map(function (v) {
                return (
                    '<option value="' + v.version_number + '">v' + v.version_number + "</option>"
                );
            })
            .join("");
        compareFrom.innerHTML = options;
        compareTo.innerHTML = options;
        if (versions.length >= 2) {
            compareTo.value = versions[0].version_number; // newest
            compareFrom.value = versions[versions.length - 1].version_number; // oldest
        }
    }

    function loadHistory() {
        setStatus("Loading version history…");
        fetch(API_BASE + "history/" + targetId + kindParam)
            .then(function (resp) {
                if (!resp.ok) throw new Error("history request failed: " + resp.status);
                return resp.json();
            })
            .then(function (data) {
                versions = data[currentDocType] || [];
                renderVersions();
            })
            .catch(function (err) {
                console.error("Version history load error:", err);
                setStatus("Could not load version history.");
            });
    }

    // -----------------------------------------------------------------------
    // Compare
    // -----------------------------------------------------------------------

    function renderDiff(data) {
        var diff = data.diff || {};
        var insertions = diff.insertions || [];
        var deletions = diff.deletions || [];
        var wordDiff = diff.word_count_diff || 0;

        if (!diff.content_changed) {
            diffSummary.innerHTML = "";
            diffOutput.innerHTML =
                '<span class="vc-diff-empty">The two versions are identical.</span>';
            return;
        }

        diffSummary.innerHTML =
            '<span class="vc-stat vc-stat-add"><i class="fa-solid fa-plus"></i> +' +
            insertions.length +
            " words</span>" +
            '<span class="vc-stat vc-stat-del"><i class="fa-solid fa-minus"></i> -' +
            deletions.length +
            " words</span>" +
            '<span class="vc-stat vc-stat-sim">Similarity ' +
            Math.round((diff.similarity || 0) * 100) +
            "%</span>" +
            '<span class="vc-stat vc-stat-sim">Word-count Δ ' +
            (wordDiff > 0 ? "+" : "") +
            wordDiff +
            "</span>";

        var html = "";
        if (deletions.length) {
            html +=
                '<div class="vc-stat vc-stat-del"><strong>Removed:</strong> ' +
                escapeHtml(deletions.join(" ")) +
                "</div>";
        }
        if (insertions.length) {
            html +=
                '<div class="vc-stat vc-stat-add"><strong>Added:</strong> ' +
                escapeHtml(insertions.join(" ")) +
                "</div>";
        }
        if (!html) {
            html = '<span class="vc-diff-empty">Content changed but the diff is empty.</span>';
        }
        diffOutput.innerHTML = html;
    }

    function runCompare() {
        if (!compareFrom.value || !compareTo.value) {
            diffSummary.innerHTML = "";
            diffOutput.innerHTML =
                '<span class="vc-diff-empty">Select both versions to compare.</span>';
            return;
        }
        var url =
            API_BASE +
            "compare/" +
            targetId +
            "/" +
            currentDocType +
            "/" +
            compareFrom.value +
            "/" +
            compareTo.value +
            kindParam;
        diffSummary.innerHTML = '<span class="vc-stat vc-stat-sim">Comparing…</span>';
        diffOutput.innerHTML = "";

        fetch(url)
            .then(function (resp) {
                if (!resp.ok) throw new Error("compare request failed: " + resp.status);
                return resp.json();
            })
            .then(renderDiff)
            .catch(function (err) {
                console.error("Compare error:", err);
                diffSummary.innerHTML = "";
                diffOutput.innerHTML =
                    '<span class="vc-diff-empty">Could not compare versions.</span>';
            });
    }

    // -----------------------------------------------------------------------
    // Restore
    // -----------------------------------------------------------------------

    function restoreVersion(versionId, versionNumber) {
        var ok = window.confirm(
            "Restore this document to v" +
                versionNumber +
                "?\n\nThis makes the snapshot the current document and records a new history entry."
        );
        if (!ok) return;

        fetch(
            API_BASE + "restore/" + targetId + "/" + currentDocType + "/" + versionId + kindParam,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ change_summary: "Restored to version " + versionNumber }),
            }
        )
            .then(function (resp) {
                if (!resp.ok) throw new Error("restore failed: " + resp.status);
                return resp.json();
            })
            .then(function () {
                window.alert("Document restored to v" + versionNumber + ".");
                loadHistory();
            })
            .catch(function (err) {
                console.error("Restore error:", err);
                window.alert("Could not restore the document.");
            });
    }

    // -----------------------------------------------------------------------
    // Branch
    // -----------------------------------------------------------------------

    var branchTargetVersion = null;

    function openBranchModal(versionNumber) {
        branchTargetVersion = versionNumber;
        branchVersionNo.textContent = "v" + versionNumber;
        branchNameInput.value = "";
        branchModal.classList.add("open");
        branchNameInput.focus();
    }

    function closeBranchModal() {
        branchModal.classList.remove("open");
        branchTargetVersion = null;
    }

    function createBranch() {
        var name = branchNameInput.value.trim();
        if (!name) {
            branchNameInput.focus();
            return;
        }
        var body = {
            doc_type: currentDocType,
            from_version: branchTargetVersion,
            branch_name: name,
            change_summary: "Starting new branch from version " + branchTargetVersion,
        };
        if (config.caseId !== null && config.caseId !== undefined) {
            body.case_id = config.caseId;
        } else {
            body.adjudication_id = config.adjudicationId;
        }

        fetch(API_BASE + "branch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (resp) {
                if (!resp.ok) throw new Error("branch failed: " + resp.status);
                return resp.json();
            })
            .then(function (data) {
                closeBranchModal();
                window.alert(
                    "Branch '" +
                        (data.branch && data.branch.branch_name ? data.branch.branch_name : name) +
                        "' created."
                );
                loadHistory();
            })
            .catch(function (err) {
                console.error("Branch error:", err);
                window.alert("Could not create the branch.");
            });
    }

    // -----------------------------------------------------------------------
    // Wire up events
    // -----------------------------------------------------------------------

    document.querySelectorAll(".vc-tab").forEach(function (tab) {
        tab.addEventListener("click", function () {
            document.querySelectorAll(".vc-tab").forEach(function (t) {
                t.classList.toggle("active", t === tab);
            });
            currentDocType = tab.getAttribute("data-doc-type");
            diffSummary.innerHTML = "";
            diffOutput.innerHTML =
                '<span class="vc-diff-empty">Select two versions above to see what changed.</span>';
            loadHistory();
        });
    });

    if (compareBtn) compareBtn.addEventListener("click", runCompare);

    versionList.addEventListener("click", function (event) {
        var restoreBtn = event.target.closest("[data-restore]");
        if (restoreBtn) {
            restoreVersion(
                restoreBtn.getAttribute("data-restore"),
                restoreBtn.getAttribute("data-version")
            );
            return;
        }
        var branchBtn = event.target.closest("[data-branch]");
        if (branchBtn) {
            openBranchModal(branchBtn.getAttribute("data-version"));
        }
    });

    if (branchConfirm) branchConfirm.addEventListener("click", createBranch);
    if (branchCancel) branchCancel.addEventListener("click", closeBranchModal);
    branchModal.addEventListener("click", function (event) {
        if (event.target === branchModal) closeBranchModal();
    });
    branchNameInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            createBranch();
        }
        if (event.key === "Escape") closeBranchModal();
    });

    // --- Initial load ---
    loadHistory();
});
