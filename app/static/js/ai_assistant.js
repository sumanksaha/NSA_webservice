/**
 * AI Assistant sidebar for the document editor (Phase 11).
 *
 * Dockable panel that sends the current Quill editor content to the
 * AI assistant backend for summarization, legal refinement, contradiction
 * detection, missing-annexure suggestions, and prayer drafting.
 *
 * Mirrors the validation_drawer.js pattern: IIFE module, ready() helper,
 * esc() escape function. Uses window.QuillEditor for content access.
 *
 * CSRF: base.html's fetch interceptor attaches the X-CSRFToken header.
 */
(function (window, document) {
    "use strict";

    function ready(fn) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", fn);
        } else {
            fn();
        }
    }

    function esc(s) {
        if (s == null) return "";
        var d = document.createElement("div");
        d.textContent = String(s);
        return d.innerHTML;
    }

    function getEditorContent() {
        // Access the Quill instance via the global exposed by editor.js.
        var qe = window.QuillEditor;
        if (!qe || !qe.getQuill) return "";
        var quill = qe.getQuill();
        if (!quill || !quill.root) return "";
        return quill.root.innerHTML || "";
    }

    function setLoading(btn, loading) {
        if (!btn) return;
        btn.disabled = loading;
        var original = btn.dataset.originalHtml;
        if (loading) {
            if (!original) {
                original = btn.innerHTML;
                btn.dataset.originalHtml = original;
            }
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing…';
        } else if (original) {
            btn.innerHTML = original;
        }
    }

    function renderResult(drawerEl, statusEl, content, isList) {
        if (!drawerEl) return;
        if (isList) {
            if (!content.length) {
                drawerEl.innerHTML = "";
                drawerEl.style.display = "none";
                if (statusEl) statusEl.textContent = "No issues found.";
                return;
            }
            var items = content
                .map(function (item) {
                    return "<li>" + esc(item) + "</li>";
                })
                .join("");
            var template = document.createElement("div");
            template.innerHTML =
                '<ul style="margin: 0; padding-left: 1.25rem; line-height: 1.7;">' +
                items +
                "</ul>";
            drawerEl.replaceChildren(...template.childNodes);
        } else {
            drawerEl.textContent = content;
        }
        drawerEl.style.display = "block";
    }

    function callAI(action, content, context, callback) {
        fetch("/ai-assistant/assist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: action, content: content, context: context }),
        })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    return { ok: resp.ok, status: resp.status, data: data };
                });
            })
            .then(function (out) {
                if (!out.ok) {
                    var msg = out.data && out.data.error ? out.data.error : "Request failed";
                    callback(null, msg, 0);
                    return;
                }
                var result = out.data.result;
                var tokens = out.data.tokens_used || 0;
                // JSON-stringified results (from detect_contradictions / suggest_annexures)
                // come back as a string; parse to array for list rendering.
                var isList = action === "detect_contradictions" || action === "suggest_annexures";
                if (isList) {
                    try {
                        result = JSON.parse(result);
                    } catch (e) {
                        result = [];
                    }
                }
                callback(result, null, tokens, isList);
            })
            .catch(function () {
                callback(null, "Network error — please try again.", 0);
            });
    }

    function init(options) {
        ready(function () {
            var buttons = document.querySelectorAll(options.buttonsSelector || ".js-ai-assistant");
            var drawerEl = document.getElementById(options.drawerId);
            var statusEl = options.statusId ? document.getElementById(options.statusId) : null;
            if (!buttons.length || !drawerEl) return;

            Array.prototype.forEach.call(buttons, function (btn) {
                btn.addEventListener("click", function () {
                    var action = btn.getAttribute("data-ai-action");
                    if (!action) {
                        if (statusEl) statusEl.textContent = "Missing data-ai-action on button.";
                        return;
                    }

                    setLoading(btn, true);
                    if (statusEl) statusEl.textContent = "Processing…";

                    var content = getEditorContent();
                    var context = btn.getAttribute("data-ai-context") || null;
                    if (context) {
                        try {
                            context = JSON.parse(context);
                        } catch (e) {
                            context = null;
                        }
                    }

                    callAI(action, content, context, function (result, error, tokens, isList) {
                        setLoading(btn, false);
                        if (error) {
                            if (statusEl) statusEl.textContent = error;
                            if (drawerEl) drawerEl.style.display = "none";
                            return;
                        }
                        if (statusEl) statusEl.textContent = "Done — " + tokens + " tokens used";
                        renderResult(drawerEl, statusEl, result, !!isList);
                    });
                });
            });
        });
    }

    window.AIAssistant = {
        init: init,
    };
})(window, document);
