/* Evidence uploader (Phase 5).
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

    // NOTE: CSRF protection is handled globally in base.html, which wraps
    // window.fetch() and attaches the X-CSRFToken header to every
    // state-changing request — no per-call token plumbing needed here.
    var selectedFiles = [];

    function showStatus(msg, isError) {
        status.innerHTML = msg;
        status.className = "info-box " + (isError ? "info-box--error" : "info-box--success");
        status.style.display = "block";
    }

    function renderQueue() {
        queue.innerHTML = "";
        selectedFiles.forEach(function (file, index) {
            var row = document.createElement("div");
            row.className = "evidence-queue-item";
            row.innerHTML =
                '<span class="evidence-queue-icon"><i class="fa-solid fa-file"></i></span>' +
                '<span class="evidence-queue-name" title="' +
                file.name +
                '">' +
                file.name +
                "</span>" +
                '<span class="evidence-queue-size">' +
                formatSize(file.size) +
                "</span>" +
                '<button type="button" class="btn btn-secondary btn-sm" data-index="' +
                index +
                '" title="Remove">' +
                '<i class="fa-solid fa-xmark"></i></button>';
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
        addFiles(fileInput.files);
        fileInput.value = "";
    });

    // Upload
    form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (selectedFiles.length === 0) {
            showStatus(
                '<i class="fa-solid fa-circle-exclamation"></i> Add at least one file.',
                true
            );
            return;
        }

        var fd = new FormData();
        selectedFiles.forEach(function (file) {
            fd.append("files", file);
        });
        ["evidence_type", "caption", "tags", "case_id", "adjudication_id", "inspection_id"].forEach(
            function (name) {
                var field = form.querySelector('[name="' + name + '"]');
                if (field && field.value) {
                    fd.append(name, field.value);
                }
            }
        );

        var originalText = uploadBtn.innerHTML;
        uploadBtn.innerHTML =
            '<i class="fa-solid fa-spinner fa-spin"></i> Uploading ' + selectedFiles.length + "…";
        uploadBtn.disabled = true;
        showStatus('<i class="fa-solid fa-spinner fa-spin"></i> Uploading…', false);

        fetch(form.dataset.uploadUrl, {
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
                var rows = (data.results || []).map(function (r) {
                    if (r.status === "ok") {
                        return (
                            '<div><i class="fa-solid fa-circle-check"></i> ' +
                            escapeHtml(r.filename) +
                            " — uploaded</div>"
                        );
                    }
                    return (
                        '<div><i class="fa-solid fa-circle-exclamation"></i> ' +
                        escapeHtml(r.filename) +
                        " — " +
                        escapeHtml(r.error || "failed") +
                        "</div>"
                    );
                });
                showStatus(
                    "<strong>" +
                        (data.results || []).filter(function (r) {
                            return r.status === "ok";
                        }).length +
                        " of " +
                        (data.results || []).length +
                        " uploaded</strong>" +
                        rows.join(""),
                    out.resp.status >= 400 && out.resp.status !== 207
                );
                if (out.resp.ok || out.resp.status === 207) {
                    selectedFiles = [];
                    renderQueue();
                    setTimeout(function () {
                        window.location.reload();
                    }, 1200);
                }
            })
            .catch(function (err) {
                showStatus(
                    '<i class="fa-solid fa-circle-exclamation"></i> Upload failed: ' + err.message,
                    true
                );
            })
            .finally(function () {
                uploadBtn.innerHTML = originalText;
                uploadBtn.disabled = false;
            });
    });

    // Edit metadata
    document.querySelectorAll(".evidence-edit").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var id = btn.dataset.id;
            var caption = window.prompt("Caption:", btn.dataset.caption || "");
            if (caption === null) {
                return;
            }
            var tags = window.prompt("Tags (comma separated):", btn.dataset.tags || "");
            if (tags === null) {
                return;
            }
            var type = window.prompt("Evidence type:", btn.dataset.type || "");
            if (type === null) {
                return;
            }

            fetch(btn.dataset.updateUrl.replace("__ID__", encodeURIComponent(id)), {
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
                    } else {
                        window.alert(data.error || "Update failed");
                    }
                });
        });
    });

    // Delete
    document.querySelectorAll(".evidence-delete").forEach(function (btn) {
        btn.addEventListener("click", function () {
            if (!window.confirm("Delete this evidence file?")) {
                return;
            }
            fetch(btn.dataset.deleteUrl.replace("__ID__", encodeURIComponent(btn.dataset.id)), {
                method: "POST",
            })
                .then(function (resp) {
                    return resp.json();
                })
                .then(function (data) {
                    if (data.status === "ok") {
                        window.location.reload();
                    } else {
                        window.alert(data.error || "Delete failed");
                    }
                });
        });
    });

    function escapeHtml(value) {
        var div = document.createElement("div");
        div.textContent = String(value);
        return div.innerHTML;
    }
})();
