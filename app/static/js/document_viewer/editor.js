/**
 * Document Viewer Editor -- Quill 2.x integration
 *
 * Initializes Quill on #editor, loads server-rendered HTML, and provides
 * a live, sandboxed preview. Switches between document types via #docTypeSelector.
 *
 * Phase 1 enhancements:
 *  - Continuous auto-save: debounced text-change listener sends HTML + Delta to
 *    /autosave/<case_id> (no PDF generation).
 *  - Delta storage: Quill Delta captured alongside HTML for round-trip fidelity.
 *  - On load, /saved/<case_id>/<doc_type> returns JSON {html, delta}; if delta
 *    exists it is restored via quill.setContents() for lossless round-trip.
 *  - Visual auto-save indicator (status text + spinner).
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    // --- Globals ---
    var quill = null;
    var previewFrame = null;
    var initialDocType = "petition";
    var autosaveDebounceMs = 1000;

    // --- Cached DOM ---
    var editorContainer = document.getElementById("editor");
    var docTypeSelector = document.getElementById("docTypeSelector");
    var previewFrameEl = document.getElementById("preview");
    var saveBtn = document.getElementById("saveBtn");
    var exportMarkdownBtn = document.getElementById("exportMarkdownBtn");
    var autosaveStatus = document.getElementById("autosaveStatus");

    // --- Server-rendered HTML (passed via Jinja2 as |safe) ---
    var petitionHtml = document.getElementById("petition-data").textContent;
    var permissionHtml = document.getElementById("permission-data").textContent;

    // Track whether an autosave is in-flight
    var autosaveInProgress = false;

    // Hidden file input used by the toolbar image button
    var imageInput = null;

    // -----------------------------------------------------------------------
    // Debounce utility
    // -----------------------------------------------------------------------
    function debounce(fn, waitMs) {
        var timeoutId;
        return function () {
            var context = this;
            var args = arguments;
            clearTimeout(timeoutId);
            timeoutId = setTimeout(function () {
                fn.apply(context, args);
            }, waitMs);
        };
    }

    // -----------------------------------------------------------------------
    // Auto-save indicator helpers
    // -----------------------------------------------------------------------
    function setAutosaveStatus(text, isSaving) {
        if (!autosaveStatus) return;
        autosaveStatus.textContent = text;
        if (isSaving) {
            autosaveStatus.classList.add("autosaving");
        } else {
            autosaveStatus.classList.remove("autosaving");
        }
    }

    /**
     * Perform an auto-save: send current HTML + Delta to the server.
     * The server stores both WITHOUT generating a PDF (fast path).
     */
    function autoSave() {
        if (!quill || autosaveInProgress) return;

        var html = quill.root.innerHTML;
        var delta = quill.getContents().toJSON();
        var docType = docTypeSelector ? docTypeSelector.value : initialDocType;

        // Update the in-memory HTML variable so switchDocType stays in sync
        if (docType === "permission") {
            permissionHtml = html;
        } else {
            petitionHtml = html;
        }

        autosaveInProgress = true;
        setAutosaveStatus("Saving...", true);

        fetch("/document_viewer/autosave/" + (window.CASE_ID || ""), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                html: html,
                delta: delta,
                doc_type: docType,
            }),
        })
            .then(function (resp) {
                if (!resp.ok) {
                    throw new Error("Auto-save failed: " + resp.status);
                }
                return resp.json();
            })
            .then(function (data) {
                setAutosaveStatus("Saved " + (data.timestamp || ""), false);
                setTimeout(function () {
                    setAutosaveStatus("", false);
                }, 2000);
            })
            .catch(function (err) {
                console.error("Auto-save error:", err);
                setAutosaveStatus("Save failed", false);
            })
            .finally(function () {
                autosaveInProgress = false;
            });
    }

    // Debounced version of autoSave
    var debouncedAutoSave = debounce(autoSave, autosaveDebounceMs);

    /**
     * Fetch the most recently saved HTML + Delta for a doc type.
     * Falls back to server-rendered HTML if no saved version exists.
     * Returns JSON: {"html": "...", "delta": {...}|null}
     * If delta exists, restores via quill.setContents() for lossless round-trip.
     */
    function fetchSavedHtml(docType, fallbackHtml) {
        var caseId = window.CASE_ID || "";
        fetch("/document_viewer/saved/" + caseId + "/" + docType)
            .then(function (resp) {
                if (resp.ok) {
                    return resp.json();
                }
                return Promise.resolve({ html: fallbackHtml, delta: null });
            })
            .then(function (data) {
                var html = data.html || fallbackHtml;
                var delta = data.delta;
                petitionHtml = docType === "petition" ? html : petitionHtml;
                permissionHtml = docType === "permission" ? html : permissionHtml;
                if (quill) {
                    if (delta) {
                        quill.setContents(delta);
                    } else {
                        quill.clipboard.dangerouslyPasteHTML(html);
                    }
                    updatePreview();
                }
            })
            .catch(function () {
                // Silently fall back to server-rendered HTML
            });
    }

    /**
     * Update the live preview iframe with the current Quill content.
     * Uses a sandboxed iframe to prevent script execution (XSS mitigation).
     */
    function updatePreview() {
        if (!quill || !previewFrame) return;

        var html = quill.root.innerHTML;
        var doc = previewFrame.contentDocument;
        if (!doc) return;

        doc.open();
        doc.write(
            '<!DOCTYPE html>' +
            '<html><head>' +
            '<meta charset="utf-8">' +
            '<style>' +
            'body { margin: 0; padding: 30px; font-family: "Times New Roman", serif; ' +
            'line-height: 1.6; color: #333; }' +
            'table { width: 100%; border-collapse: collapse; margin: 10px 0; }' +
            'th, td { border: 1px solid #000; padding: 4px 8px; text-align: left; }' +
            '.page-break { page-break-before: always; }' +
            '</style>' +
            '</head><body>' +
            html +
            '</body></html>'
        );
        doc.close();
    }

    /**
     * Get the HTML for the currently selected document type.
     */
    function getActiveHtml() {
        var docType = docTypeSelector ? docTypeSelector.value : initialDocType;
        return docType === "permission" ? permissionHtml : petitionHtml;
    }

    /**
     * (Re)initialize Quill with the active document HTML.
     */
    function initQuill() {
        if (quill) {
            quill.off("text-change", updatePreview);
            quill = null;
        }

        var toolbar = [
            [{ header: [1, 2, 3, false] }],
            ["bold", "italic", "underline", "strike"],
            [{ list: "ordered" }, { list: "bullet" }],
            [{ indent: "-1" }, { indent: "+1" }],
            ["blockquote"],
            [{ align: [] }],
            [{ table: [[], [], false]] }],
            ["image", "link"],
        ];

        quill = new Quill("#editor", {
            modules: {
                table: true,
                toolbar: toolbar,
            },
            theme: "snow",
            placeholder: "Loading document...",
        });

        // Override the default image handler: upload to the server and
        // insert the returned URL instead of embedding a base64 data URI.
        quill.getModule("toolbar").addHandler("image", handleImageToolbar);

        var content = getActiveHtml();
        quill.clipboard.dangerouslyPasteHTML(content);

        // Set up live preview + debounced auto-save
        quill.on("text-change", updatePreview);
        quill.on("text-change", debouncedAutoSave);
    }

    /**
     * Open a file picker and upload the selected image to the server.
     * On success the returned URL is embedded into the document at the
     * current selection point (Phase 2: image upload handler).
     */
    function handleImageToolbar() {
        if (!imageInput) {
            imageInput = document.createElement("input");
            imageInput.type = "file";
            imageInput.accept = "image/*";
            imageInput.style.display = "none";
            imageInput.addEventListener("change", function () {
                var file = imageInput.files && imageInput.files[0];
                imageInput.value = "";
                if (file) {
                    uploadEditorImage(file);
                }
            });
            document.body.appendChild(imageInput);
        }
        imageInput.click();
    }

    /**
     * Upload an image file to /document_viewer/upload_image and insert the
     * returned URL into the editor at the current cursor position.
     */
    function uploadEditorImage(file) {
        var formData = new FormData();
        formData.append("image", file);
        var csrfToken = document
            .querySelector('meta[name="csrf-token"]')
            ?.getAttribute("content");

        fetch("/document_viewer/upload_image", {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken },
            body: formData,
        })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    if (!resp.ok) {
                        throw new Error(data.error || "Image upload failed");
                    }
                    return data;
                });
            })
            .then(function (data) {
                var range = quill.getSelection(true);
                if (!range) range = { index: quill.getLength() - 1, length: 0 };
                quill.insertEmbed(range.index, "image", data.url, "user");
                quill.setSelection(range.index + 1, 0, "user");
                updatePreview();
            })
            .catch(function (err) {
                console.error("Image upload error:", err);
                alert(err.message || "Image upload failed");
            });
    }

    /**
     * Export the current document as Markdown (Phase 2).
     * Sends the Quill Delta to /document_viewer/export_markdown and downloads
     * the returned .md file.
     */
    function exportMarkdown() {
        if (!quill) return;
        var delta = quill.getContents().toJSON();
        var html = quill.root.innerHTML;
        var docType = docTypeSelector ? docTypeSelector.value : "petition";
        var csrfToken = document
            .querySelector('meta[name="csrf-token"]')
            ?.getAttribute("content");

        fetch("/document_viewer/export_markdown", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify({ delta: delta, html: html, doc_type: docType }),
        })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    if (!resp.ok) {
                        throw new Error(data.error || "Markdown export failed");
                    }
                    return data;
                });
            })
            .then(function (data) {
                var blob = new Blob([data.markdown], {
                    type: "text/markdown;charset=utf-8",
                });
                var url = window.URL.createObjectURL(blob);
                var a = document.createElement("a");
                a.href = url;
                a.download = data.filename || "document.md";
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            })
            .catch(function (err) {
                console.error("Markdown export error:", err);
                alert(err.message || "Markdown export failed");
            });
    }

    /**
     * Switch the active Quill content when the document type selector changes.
     */
    function switchDocType() {
        if (!quill) return;
        var docType = docTypeSelector ? docTypeSelector.value : initialDocType;
        var content = getActiveHtml();
        quill.clipboard.dangerouslyPasteHTML(content);
        updatePreview();
        fetchSavedHtml(docType, content);
    }

    /**
     * Trigger a server-side PDF download of the edited HTML.
     * Sends Quill Delta alongside HTML for persistence.
     */
    function saveToPdf() {
        if (!quill) return;
        var html = quill.root.innerHTML;
        var delta = quill.getContents().toJSON();
        var docType = docTypeSelector ? docTypeSelector.value : "petition";
        var csrfToken = document
            .querySelector('meta[name="csrf-token"]')
            ?.getAttribute("content");

        fetch("/document_viewer/save/" + (window.CASE_ID || ""), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify({ html: html, delta: delta, doc_type: docType }),
        })
            .then(function (resp) {
                if (!resp.ok) {
                    throw new Error("Save failed");
                }
                return resp.blob();
            })
            .then(function (blob) {
                var url = window.URL.createObjectURL(blob);
                var a = document.createElement("a");
                a.href = url;
                a.download = "edited_document.pdf";
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            })
            .catch(function (err) {
                console.error("Save error:", err);
                alert("Could not save document. See console for details.");
            });
    }

    // --- Initialize ---
    if (editorContainer) {
        initQuill();
        updatePreview();
        fetchSavedHtml(initialDocType, petitionHtml);
    }

    if (previewFrameEl) {
        previewFrame = previewFrameEl;
    }

    if (docTypeSelector) {
        docTypeSelector.addEventListener("change", switchDocType);
        initialDocType = docTypeSelector.value;
    }

    if (saveBtn) {
        saveBtn.addEventListener("click", saveToPdf);
    }

    if (exportMarkdownBtn) {
        exportMarkdownBtn.addEventListener("click", exportMarkdown);
    }

    // Expose for testing / debugging
    window.QuillEditor = {
        getQuill: function () {
            return quill;
        },
        getPreviewHtml: function () {
            if (!quill) return "";
            return quill.root.innerHTML;
        },
        getDelta: function () {
            if (!quill) return null;
            return quill.getContents().toJSON();
        },
        getAutosaveDebounceMs: function () {
            return autosaveDebounceMs;
        },
        triggerAutosave: function () {
            autoSave();
        },
    };
});
