/**
 * Version Control UI (Phase 9).
 * Migrated from version_control.js — compiled to app/static/js/version_control/version_control.js.
 */
import type {
    VersionSummary,
    VersionDiffResponse,
    VersionBranchResponse,
} from "../types/api.js";

document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    var config = (window.VC_CONFIG || {}) as { caseId: number | null; adjudicationId: number | null; caseType?: string };
    var targetId: number | null =
        config.caseId !== null && config.caseId !== undefined
            ? config.caseId
            : config.adjudicationId;

    var kindParam = config.caseType ? "?kind=" + encodeURIComponent(config.caseType) : "";
    var API_BASE = "/api/version-control/";

    var currentDocType = "petition";
    var versions: VersionSummary[] = [];

    // --- Cached DOM ---

    var versionList = document.getElementById("vcVersionList")!;
    var versionCount = document.getElementById("vcVersionCount");
    var compareFrom = document.getElementById("vcCompareFrom") as HTMLSelectElement | null;
    var compareTo = document.getElementById("vcCompareTo") as HTMLSelectElement | null;
    var compareBtn = document.getElementById("vcCompareBtn");
    var diffSummary = document.getElementById("vcDiffSummary");
    var diffOutput = document.getElementById("vcDiffOutput");
    var branchModal = document.getElementById("vcBranchModal")!;
    var branchNameInput = document.getElementById("vcBranchName") as HTMLInputElement;
    var branchVersionNo = document.getElementById("vcBranchVersionNo")!;
    var branchConfirm = document.getElementById("vcBranchConfirm");
    var branchCancel = document.getElementById("vcBranchCancel");

    // -----------------------------------------------------------------------
    // Rendering helpers
    // -----------------------------------------------------------------------

    function escapeHtml(value: string | number | null | undefined): string {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function setHTML(el: HTMLElement, html: string): void {
        el.replaceChildren();
        if (html) {
            var doc = new DOMParser().parseFromString(html, "text/html");
            while (doc.body.firstChild) {
                el.append(doc.body.firstChild);
            }
        }
    }

    function formatDate(iso: string): string {
        if (!iso) return "\u2014";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleString();
    }

    function setStatus(message: string): void {
        setHTML(versionList, '<p class="vc-empty">' + escapeHtml(message) + "</p>");
        if (versionCount) versionCount.textContent = "";
    }

    // -----------------------------------------------------------------------
    // Version list
    // -----------------------------------------------------------------------

    function renderVersions(): void {
        if (!versions.length) {
            setStatus("No saved versions for this document type yet.");
            if (compareFrom) {
                setHTML(compareFrom, '<option value="">\u2014</option>');
                setHTML(compareTo!, '<option value="">\u2014</option>');
            }
            return;
        }

        var rows = versions
            .map(function (v: VersionSummary) {
                var createdBy = v.created_by
                    ? escapeHtml(v.created_by.username || "user#" + v.created_by.id)
                    : "\u2014";
                var summary = escapeHtml(v.change_summary || "\u2014");
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

        setHTML(
            versionList,
            '<table class="vc-table">' +
                "<thead><tr><th>Version</th><th>Created</th><th>By</th><th>Summary</th><th>Actions</th></tr></thead>" +
                "<tbody>" +
                rows +
                "</tbody>" +
                "</table>",
        );
        if (versionCount)
            versionCount.textContent =
                versions.length + (versions.length === 1 ? " version" : " versions");

        var options = versions
            .map(function (v: VersionSummary) {
                return (
                    '<option value="' + v.version_number + '">v' + v.version_number + "</option>"
                );
            })
            .join("");
        setHTML(compareFrom!, options);
        setHTML(compareTo!, options);
        if (versions.length >= 2) {
            compareTo!.value = String(versions[0].version_number);
            compareFrom!.value = String(versions[versions.length - 1].version_number);
        }
    }

    function loadHistory(): void {
        setStatus("Loading version history\u2026");
        fetch(API_BASE + "history/" + targetId + kindParam)
            .then(function (resp: Response) {
                if (!resp.ok) throw new Error("history request failed: " + resp.status);
                return resp.json() as Promise<Record<string, VersionSummary[]>>;
            })
            .then(function (data: Record<string, VersionSummary[]>) {
                versions = data[currentDocType] || [];
                renderVersions();
            })
            .catch(function (err: Error) {
                console.error("Version history load error:", err);
                setStatus("Could not load version history.");
            });
    }

    // -----------------------------------------------------------------------
    // Compare
    // -----------------------------------------------------------------------

    function renderDiff(data: VersionDiffResponse): void {
        var diff = data.diff || {};
        var insertions = diff.insertions || [];
        var deletions = diff.deletions || [];
        var wordDiff = diff.word_count_diff || 0;

        if (!diff.content_changed) {
            setHTML(diffSummary!, "");
            setHTML(
                diffOutput!,
                '<span class="vc-diff-empty">The two versions are identical.</span>',
            );
            return;
        }

        setHTML(
            diffSummary!,
            '<span class="vc-stat vc-stat-add"><i class="fa-solid fa-plus"></i> +' +
                insertions.length +
                " words</span>" +
                '<span class="vc-stat vc-stat-del"><i class="fa-solid fa-minus"></i> -' +
                deletions.length +
                " words</span>" +
                '<span class="vc-stat vc-stat-sim">Similarity ' +
                Math.round((diff.similarity || 0) * 100) +
                "%</span>" +
                '<span class="vc-stat vc-stat-sim">Word-count \u0394 ' +
                (wordDiff > 0 ? "+" : "") +
                wordDiff +
                "</span>",
        );

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
        setHTML(diffOutput!, html);
    }

    function runCompare(): void {
        if (!compareFrom!.value || !compareTo!.value) {
            setHTML(diffSummary!, "");
            setHTML(
                diffOutput!,
                '<span class="vc-diff-empty">Select both versions to compare.</span>',
            );
            return;
        }
        var url =
            API_BASE +
            "compare/" +
            targetId +
            "/" +
            currentDocType +
            "/" +
            compareFrom!.value +
            "/" +
            compareTo!.value +
            kindParam;
        setHTML(diffSummary!, '<span class="vc-stat vc-stat-sim">Comparing\u2026</span>');
        setHTML(diffOutput!, "");

        fetch(url)
            .then(function (resp: Response) {
                if (!resp.ok) throw new Error("compare request failed: " + resp.status);
                return resp.json() as Promise<VersionDiffResponse>;
            })
            .then(renderDiff)
            .catch(function (err: Error) {
                console.error("Compare error:", err);
                setHTML(diffSummary!, "");
                setHTML(
                    diffOutput!,
                    '<span class="vc-diff-empty">Could not compare versions.</span>',
                );
            });
    }

    // -----------------------------------------------------------------------
    // Restore
    // -----------------------------------------------------------------------

    function restoreVersion(versionId: string, versionNumber: string): void {
        var ok = window.confirm(
            "Restore this document to v" +
                versionNumber +
                "?\n\nThis makes the snapshot the current document and records a new history entry.",
        );
        if (!ok) return;

        fetch(
            API_BASE + "restore/" + targetId + "/" + currentDocType + "/" + versionId + kindParam,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ change_summary: "Restored to version " + versionNumber }),
            },
        )
            .then(function (resp: Response) {
                if (!resp.ok) throw new Error("restore failed: " + resp.status);
                return resp.json();
            })
            .then(function () {
                window.alert("Document restored to v" + versionNumber + ".");
                loadHistory();
            })
            .catch(function (err: Error) {
                console.error("Restore error:", err);
                window.alert("Could not restore the document.");
            });
    }

    // -----------------------------------------------------------------------
    // Branch
    // -----------------------------------------------------------------------

    var branchTargetVersion: string | null = null;

    function openBranchModal(versionNumber: string): void {
        branchTargetVersion = versionNumber;
        branchVersionNo.textContent = "v" + versionNumber;
        branchNameInput.value = "";
        branchModal.classList.add("open");
        branchNameInput.focus();
    }

    function closeBranchModal(): void {
        branchModal.classList.remove("open");
        branchTargetVersion = null;
    }

    function createBranch(): void {
        var name = branchNameInput.value.trim();
        if (!name) {
            branchNameInput.focus();
            return;
        }
        var body: Record<string, unknown> = {
            doc_type: currentDocType,
            from_version: branchTargetVersion,
            branch_name: name,
            change_summary: "Starting new branch from version " + branchTargetVersion,
        };
        if (config.caseId !== null && config.caseId !== undefined) {
            body.case_id = config.caseId!;
        } else {
            body.adjudication_id = config.adjudicationId!;
        }

        fetch(API_BASE + "branch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (resp: Response) {
                if (!resp.ok) throw new Error("branch failed: " + resp.status);
                return resp.json() as Promise<VersionBranchResponse>;
            })
            .then(function (data: VersionBranchResponse) {
                closeBranchModal();
                window.alert(
                    "Branch '" +
                        (data.branch && data.branch.branch_name ? data.branch.branch_name : name) +
                        "' created.",
                );
                loadHistory();
            })
            .catch(function (err: Error) {
                console.error("Branch error:", err);
                window.alert("Could not create the branch.");
            });
    }

    // -----------------------------------------------------------------------
    // Wire up events
    // -----------------------------------------------------------------------

    document.querySelectorAll(".vc-tab").forEach(function (tab: Element) {
        tab.addEventListener("click", function () {
            document.querySelectorAll(".vc-tab").forEach(function (t: Element) {
                t.classList.toggle("active", t === tab);
            });
            currentDocType = tab.getAttribute("data-doc-type") || "petition";
            setHTML(diffSummary!, "");
            setHTML(
                diffOutput!,
                '<span class="vc-diff-empty">Select two versions above to see what changed.</span>',
            );
            loadHistory();
        });
    });

    if (compareBtn) compareBtn.addEventListener("click", runCompare);

    versionList.addEventListener("click", function (event: MouseEvent) {
        var target = event.target as HTMLElement;
        var restoreBtn = target.closest("[data-restore]") as HTMLElement | null;
        if (restoreBtn) {
            restoreVersion(
                restoreBtn.getAttribute("data-restore") || "",
                restoreBtn.getAttribute("data-version") || "",
            );
            return;
        }
        var branchBtn = target.closest("[data-branch]") as HTMLElement | null;
        if (branchBtn) {
            openBranchModal(branchBtn.getAttribute("data-version") || "");
        }
    });

    if (branchConfirm) branchConfirm.addEventListener("click", createBranch);
    if (branchCancel) branchCancel.addEventListener("click", closeBranchModal);
    branchModal.addEventListener("click", function (event: MouseEvent) {
        if (event.target === branchModal) closeBranchModal();
    });
    branchNameInput.addEventListener("keydown", function (event: KeyboardEvent) {
        if (event.key === "Enter") {
            event.preventDefault();
            createBranch();
        }
        if (event.key === "Escape") closeBranchModal();
    });

    // --- Initial load ---
    loadHistory();
});
