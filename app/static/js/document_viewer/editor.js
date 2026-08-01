/**
 * Document Viewer Editor — Quill 2.x integration
 *
 * Initializes Quill on #editor, loads server-rendered HTML, and provides
 * a live, sandboxed preview. Switches between document types via #docTypeSelector.
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    // --- Globals ---
    var quill = null;
    var previewFrame = null;
    var initialDocType = "petition";

    // --- Cached DOM ---
    var editorContainer = document.getElementById("editor");
    var docTypeSelector = document.getElementById("docTypeSelector");
    var previewFrameEl = document.getElementById("preview");
    var saveBtn = document.getElementById("saveBtn");

    // --- Server-rendered HTML (passed via Jinja2 as |safe) ---
    var petitionHtml = document.getElementById("petition-data").textContent;
    var permissionHtml = document.getElementById("permission-data").textContent;

    /**
     * Fetch the most recently saved HTML for a doc type.
     * Falls back to server-rendered HTML if no saved version exists.
     */
    function fetchSavedHtml(docType, fallbackHtml) {
        var caseId = window.CASE_ID || "";
        fetch("/document_viewer/saved/" + caseId + "/" + docType)
            .then(function (resp) {
                if (resp.ok) {
                    return resp.text();
                }
                return Promise.resolve(fallbackHtml);
            })
            .then(function (html) {
                petitionHtml = docType === "petition" ? html : petitionHtml;
                permissionHtml = docType === "permission" ? html : permissionHtml;
                if (quill) {
                    quill.clipboard.dangerouslyPasteHTML(html);
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
        ];

        quill = new Quill("#editor", {
            modules: {
                table: true,
                toolbar: toolbar,
            },
            theme: "snow",
            placeholder: "Loading document...",
        });

        // Load the active document content
        var content = getActiveHtml();
        quill.clipboard.dangerouslyPasteHTML(content);

        // Set up live preview
        quill.on("text-change", updatePreview);
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
        // Fetch saved HTML for the new doc type (session restore)
        fetchSavedHtml(docType, content);
    }

    /**
     * Trigger a server-side PDF download of the edited HTML.
     */
    function saveToPdf() {
        if (!quill) return;
        var html = quill.root.innerHTML;
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
            body: JSON.stringify({ html: html, doc_type: docType }),
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
        // Session restore: fetch saved HTML for the initial doc type
        fetchSavedHtml(initialDocType, petitionHtml);
    }

    if (previewFrameEl) {
        previewFrame = previewFrameEl;
    }

    if (docTypeSelector) {
        docTypeSelector.addEventListener("change", switchDocType);
        // Set initialDocType from selector if present
        initialDocType = docTypeSelector.value;
    }

    if (saveBtn) {
        saveBtn.addEventListener("click", saveToPdf);
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
    };
});
