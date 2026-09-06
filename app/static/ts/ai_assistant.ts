/**
 * AI Assistant sidebar for the document editor (Phase 11).
 * Migrated from ai_assistant.js — compiled to app/static/js/ai_assistant.js.
 */
import type { AiAssistantOpts } from "./types/api.js";

(function (window: Window, _document: Document) {
    "use strict";

    function ready(fn: () => void): void {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", fn);
        } else {
            fn();
        }
    }

    function esc(s: unknown): string {
        if (s == null) return "";
        var d = document.createElement("div");
        d.textContent = String(s);
        return d.innerHTML;
    }

    function getEditorContent(): string {
        var qe = window.QuillEditor;
        if (!qe || !qe.getQuill) return "";
        var quill = qe.getQuill() as { root?: { innerHTML?: string } } | null;
        if (!quill || !quill.root) return "";
        return quill.root.innerHTML || "";
    }

    function setLoading(btn: HTMLElement | null, loading: boolean): void {
        if (!btn) return;
        (btn as HTMLButtonElement).disabled = loading;
        var original = (btn.dataset as Record<string, string>).originalHtml;
        if (loading) {
            if (!original) {
                original = btn.innerHTML;
                (btn.dataset as Record<string, string>).originalHtml = original;
            }
            btn.textContent = "";
            var icon = document.createElement("i");
            icon.className = "fa-solid fa-spinner fa-spin";
            btn.appendChild(icon);
            btn.appendChild(document.createTextNode(" Processing\u2026"));
        } else if (original) {
            btn.textContent = original; // ponytail: restores plain text only; icons preserved via CSS/classes on page load
        }
    }

    function renderResult(
        drawerEl: HTMLElement,
        statusEl: HTMLElement | null,
        content: string | string[],
        isList: boolean
    ): void {
        if (!drawerEl) return;
        if (isList) {
            if (!(content as string[]).length) {
                drawerEl.textContent = "";
                drawerEl.style.display = "none";
                if (statusEl) statusEl.textContent = "No issues found.";
                return;
            }
            var ul = document.createElement("ul");
            ul.style.margin = "0";
            ul.style.paddingLeft = "1.25rem";
            ul.style.lineHeight = "1.7";
            (content as string[]).forEach(function (item: string) {
                var li = document.createElement("li");
                li.textContent = item;
                ul.appendChild(li);
            });
            drawerEl.replaceChildren(ul);
        } else {
            drawerEl.textContent = content as string;
        }
        drawerEl.style.display = "block";
    }

    function callAI(
        action: string,
        content: string,
        context: Record<string, unknown> | null,
        callback: (
            result: string | string[] | null,
            error: string | null,
            tokens: number,
            isList?: boolean
        ) => void
    ): void {
        fetch("/ai-assistant/assist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: action, content: content, context: context }),
        })
            .then(function (resp: Response) {
                return resp.json().then(function (data: Record<string, unknown>) {
                    return { ok: resp.ok, status: resp.status, data: data };
                });
            })
            .then(function (out: { ok: boolean; status: number; data: Record<string, unknown> }) {
                if (!out.ok) {
                    var msg =
                        out.data && out.data.error ? String(out.data.error) : "Request failed";
                    callback(null, msg, 0);
                    return;
                }
                var resultRaw = out.data.result as string;
                var tokens = (out.data.tokens_used as number) || 0;
                var isList = action === "detect_contradictions" || action === "suggest_annexures";
                var result: string | string[] = resultRaw;
                if (isList) {
                    try {
                        result = JSON.parse(resultRaw) as string[];
                    } catch {
                        result = [];
                    }
                }
                callback(result, null, tokens, isList);
            })
            .catch(function () {
                callback(null, "Network error \u2014 please try again.", 0);
            });
    }

    function init(options: AiAssistantOpts): void {
        ready(function () {
            var buttons = document.querySelectorAll(options.buttonsSelector || ".js-ai-assistant");
            var drawerEl = document.getElementById(options.drawerId);
            var statusEl = options.statusId ? document.getElementById(options.statusId) : null;
            if (!buttons.length || !drawerEl) return;

            Array.prototype.forEach.call(buttons, function (btn: HTMLElement) {
                btn.addEventListener("click", function () {
                    var action = btn.getAttribute("data-ai-action");
                    if (!action) {
                        if (statusEl) statusEl.textContent = "Missing data-ai-action on button.";
                        return;
                    }

                    setLoading(btn, true);
                    if (statusEl) statusEl.textContent = "Processing\u2026";

                    var content = getEditorContent();
                    var contextRaw = btn.getAttribute("data-ai-context") || null;
                    var context: Record<string, unknown> | null = null;
                    if (contextRaw) {
                        try {
                            context = JSON.parse(contextRaw) as Record<string, unknown>;
                        } catch {
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
                        if (statusEl)
                            statusEl.textContent = "Done \u2014 " + tokens + " tokens used";
                        renderResult(drawerEl!, statusEl, result!, !!isList);
                    });
                });
            });
        });
    }

    window.AIAssistant = {
        init: init,
    };
})(window, document);
