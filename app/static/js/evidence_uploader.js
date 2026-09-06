"use strict";
/**
 * Evidence uploader (Phase 5).
 * Migrated from evidence_uploader.js — compiled to app/static/js/evidence_uploader.js.
 *
 * Drag-and-drop multi-file upload with a visible file queue, inline
 * upload status per file, and the card edit/delete actions.
 */
(function () {
    "use strict";
    var dropzone = document.getElementById("evidenceDropzone");
    var fileInput = document.getElementById("evidenceFiles");
    var queue = document.getElementById("fileQueue");
    var status = document.getElementById("uploadStatus");
    var form = document.getElementById("evidenceForm");
    var uploadBtn = document.getElementById("evidenceUploadBtn");
    if (!dropzone || !form) {
        return;
    }
    var selectedFiles = [];
    function showStatus(msg, isError) {
        setHTML(status, msg);
        status.className = "info-box " + (isError ? "info-box--error" : "info-box--success");
        status.style.display = "block";
    }
    function renderQueue() {
        queue.replaceChildren();
        selectedFiles.forEach(function (file, index) {
            var row = document.createElement("div");
            row.className = "evidence-queue-item";
            var safeName = escapeHtml(file.name);
            setHTML(row, '<span class="evidence-queue-icon"><i class="fa-solid fa-file"></i></span>' +
                '<span class="evidence-queue-name" title="' +
                safeName +
                '">' +
                safeName +
                "</span>" +
                '<span class="evidence-queue-size">' +
                formatSize(file.size) +
                "</span>" +
                '<button type="button" class="btn btn-secondary btn-sm" data-index="' +
                index +
                '" title="Remove">' +
                '<i class="fa-solid fa-xmark"></i></button>');
            row.querySelector("button").addEventListener("click", function () {
                selectedFiles.splice(index, 1);
                renderQueue();
            });
            queue.appendChild(row);
        });
    }
    function formatSize(bytes) {
        if (!bytes) {
            return "0 KB";
        }
        if (bytes < 1024 * 1024) {
            return (bytes / 1024).toFixed(1) + " KB";
        }
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }
    function addFiles(fileList) {
        Array.prototype.forEach.call(fileList, function (file) {
            selectedFiles.push(file);
        });
        renderQueue();
    }
    // Drag & drop
    ["dragenter", "dragover"].forEach(function (evt) {
        dropzone.addEventListener(evt, function (e) {
            e.preventDefault();
            dropzone.classList.add("evidence-dropzone--over");
        });
    });
    ["dragleave", "drop"].forEach(function (evt) {
        dropzone.addEventListener(evt, function (e) {
            e.preventDefault();
            dropzone.classList.remove("evidence-dropzone--over");
        });
    });
    dropzone.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files) {
            addFiles(e.dataTransfer.files);
        }
    });
    dropzone.addEventListener("click", function () {
        fileInput.click();
    });
    fileInput.addEventListener("change", function () {
        if (fileInput.files)
            addFiles(fileInput.files);
        fileInput.value = "";
    });
    // Upload
    form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (selectedFiles.length === 0) {
            showStatus('<i class="fa-solid fa-circle-exclamation"></i> Add at least one file.', true);
            return;
        }
        var fd = new FormData();
        selectedFiles.forEach(function (file) {
            fd.append("files", file);
        });
        [
            "evidence_type",
            "caption",
            "tags",
            "case_id",
            "adjudication_id",
            "inspection_id",
        ].forEach(function (name) {
            var field = form.querySelector('[name="' + name + '"]');
            if (field && field.value) {
                fd.append(name, field.value);
            }
        });
        var originalText = uploadBtn.innerHTML;
        setHTML(uploadBtn, '<i class="fa-solid fa-spinner fa-spin"></i> Uploading ' + selectedFiles.length + "\u2026");
        uploadBtn.disabled = true;
        showStatus('<i class="fa-solid fa-spinner fa-spin"></i> Uploading\u2026', false);
        fetch(form.dataset.uploadUrl || "", {
            method: "POST",
            body: fd,
        })
            .then(function (resp) {
            return resp.json().then(function (data) {
                return { resp: resp, data: data };
            });
        })
            .then(function (out) {
            var data = out.data;
            var results = (data.results || []);
            var rows = results.map(function (r) {
                if (r.status === "ok") {
                    return ('<div><i class="fa-solid fa-circle-check"></i> ' +
                        escapeHtml(r.filename) +
                        " \u2014 uploaded</div>");
                }
                return ('<div><i class="fa-solid fa-circle-exclamation"></i> ' +
                    escapeHtml(r.filename) +
                    " \u2014 " +
                    escapeHtml(r.error || "failed") +
                    "</div>");
            });
            var okCount = results.filter(function (r) {
                return r.status === "ok";
            }).length;
            showStatus("<strong>" +
                okCount +
                " of " +
                results.length +
                " uploaded</strong>" +
                rows.join(""), out.resp.status >= 400 && out.resp.status !== 207);
            if (out.resp.ok || out.resp.status === 207) {
                selectedFiles = [];
                renderQueue();
                setTimeout(function () {
                    window.location.reload();
                }, 1200);
            }
        })
            .catch(function (err) {
            showStatus('<i class="fa-solid fa-circle-exclamation"></i> Upload failed: ' +
                escapeHtml(err.message), true);
        })
            .finally(function () {
            setHTML(uploadBtn, originalText);
            uploadBtn.disabled = false;
        });
    });
    // Edit metadata
    document.querySelectorAll(".evidence-edit").forEach(function (btn) {
        var htmlBtn = btn;
        htmlBtn.addEventListener("click", function () {
            var id = htmlBtn.dataset.id;
            var caption = window.prompt("Caption:", htmlBtn.dataset.caption || "");
            if (caption === null) {
                return;
            }
            var tags = window.prompt("Tags (comma separated):", htmlBtn.dataset.tags || "");
            if (tags === null) {
                return;
            }
            var type = window.prompt("Evidence type:", htmlBtn.dataset.type || "");
            if (type === null) {
                return;
            }
            fetch((htmlBtn.dataset.updateUrl || "").replace("__ID__", encodeURIComponent(id || "")), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ caption: caption, tags: tags, evidence_type: type }),
            })
                .then(function (resp) {
                return resp.json();
            })
                .then(function (data) {
                if (data.status === "ok") {
                    window.location.reload();
                }
                else {
                    window.alert(String(data.error || "Update failed"));
                }
            });
        });
    });
    // Delete
    document.querySelectorAll(".evidence-delete").forEach(function (btn) {
        var htmlBtn = btn;
        htmlBtn.addEventListener("click", function () {
            if (!window.confirm("Delete this evidence file?")) {
                return;
            }
            fetch((htmlBtn.dataset.deleteUrl || "").replace("__ID__", encodeURIComponent(htmlBtn.dataset.id || "")), {
                method: "POST",
            })
                .then(function (resp) {
                return resp.json();
            })
                .then(function (data) {
                if (data.status === "ok") {
                    window.location.reload();
                }
                else {
                    window.alert(String(data.error || "Delete failed"));
                }
            });
        });
    });
    function setHTML(el, html) {
        el.replaceChildren();
        if (html) {
            var doc = new DOMParser().parseFromString(html, "text/html");
            while (doc.body.firstChild) {
                el.append(doc.body.firstChild);
            }
        }
    }
    function escapeHtml(value) {
        var div = document.createElement("div");
        div.textContent = String(value == null ? "" : value);
        return div.innerHTML;
    }
})();
//# sourceMappingURL=evidence_uploader.js.map