(function (window, _document) {
    "use strict";
    function ready(fn) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", fn);
        }
        else {
            fn();
        }
    }
    function esc(s) {
        if (s == null)
            return "";
        var d = document.createElement("div");
        d.textContent = String(s);
        return d.innerHTML;
    }
    function getEditorContent() {
        var qe = window.QuillEditor;
        if (!qe || !qe.getQuill)
            return "";
        var quill = qe.getQuill();
        if (!quill || !quill.root)
            return "";
        return quill.root.innerHTML || "";
    }
    function setLoading(btn, loading) {
        if (!btn)
            return;
        btn.disabled = loading;
        var original = btn.dataset.originalHtml;
        if (loading) {
            if (!original) {
                original = btn.innerHTML;
                btn.dataset.originalHtml = original;
            }
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing\u2026';
        }
        else if (original) {
            btn.innerHTML = original;
        }
    }
    function renderResult(drawerEl, statusEl, content, isList) {
        if (!drawerEl)
            return;
        if (isList) {
            if (!content.length) {
                drawerEl.innerHTML = "";
                drawerEl.style.display = "none";
                if (statusEl)
                    statusEl.textContent = "No issues found.";
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
            drawerEl.replaceChildren(...Array.from(template.childNodes));
        }
        else {
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
                var msg = out.data && out.data.error ? String(out.data.error) : "Request failed";
                callback(null, msg, 0);
                return;
            }
            var resultRaw = out.data.result;
            var tokens = out.data.tokens_used || 0;
            var isList = action === "detect_contradictions" || action === "suggest_annexures";
            var result = resultRaw;
            if (isList) {
                try {
                    result = JSON.parse(resultRaw);
                }
                catch {
                    result = [];
                }
            }
            callback(result, null, tokens, isList);
        })
            .catch(function () {
            callback(null, "Network error \u2014 please try again.", 0);
        });
    }
    function init(options) {
        ready(function () {
            var buttons = document.querySelectorAll(options.buttonsSelector || ".js-ai-assistant");
            var drawerEl = document.getElementById(options.drawerId);
            var statusEl = options.statusId ? document.getElementById(options.statusId) : null;
            if (!buttons.length || !drawerEl)
                return;
            Array.prototype.forEach.call(buttons, function (btn) {
                btn.addEventListener("click", function () {
                    var action = btn.getAttribute("data-ai-action");
                    if (!action) {
                        if (statusEl)
                            statusEl.textContent = "Missing data-ai-action on button.";
                        return;
                    }
                    setLoading(btn, true);
                    if (statusEl)
                        statusEl.textContent = "Processing\u2026";
                    var content = getEditorContent();
                    var contextRaw = btn.getAttribute("data-ai-context") || null;
                    var context = null;
                    if (contextRaw) {
                        try {
                            context = JSON.parse(contextRaw);
                        }
                        catch {
                            context = null;
                        }
                    }
                    callAI(action, content, context, function (result, error, tokens, isList) {
                        setLoading(btn, false);
                        if (error) {
                            if (statusEl)
                                statusEl.textContent = error;
                            if (drawerEl)
                                drawerEl.style.display = "none";
                            return;
                        }
                        if (statusEl)
                            statusEl.textContent = "Done \u2014 " + tokens + " tokens used";
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
//# sourceMappingURL=ai_assistant.js.map