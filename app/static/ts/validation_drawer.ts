/**
 * validation_drawer.ts — Phase 12 Legal Validation drawer (shared UI).
 * Migrated from validation_drawer.js — compiled to app/static/js/validation_drawer.js.
 */
import type {
    ValidationDrawerFormOpts,
    ValidationDrawerRowOpts,
    ValidationReportResponse,
    ValidationFinding,
} from "./types/api.js";

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
        var d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML;
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

    function gradeColor(score: number): string {
        if (score >= 90) return "#0b6e4f";
        if (score >= 75) return "#c77d0a";
        return "#b3261e";
    }

    function findingList(items: ValidationFinding[], icon: string, color: string): string {
        if (!items.length) return "";
        var html = '<ul style="margin: 0; padding-left: 1.25rem; line-height: 1.7;">';
        items.forEach(function (item: ValidationFinding) {
            html +=
                '<li><span style="color: ' +
                color +
                ';"><i class="fa-solid ' +
                icon +
                '"></i></span> ' +
                esc(item.message) +
                (item.field_name
                    ? ' <span class="text--muted" style="font-size: 0.8125rem;">(' +
                      esc(item.field_name) +
                      ")</span>"
                    : "") +
                (item.suggestion
                    ? ' <div class="form-hint" style="margin: 0.15rem 0 0.4rem 0;">' +
                      esc(item.suggestion) +
                      "</div>"
                    : "") +
                "</li>";
        });
        return html + "</ul>";
    }

    function renderReport(data: ValidationReportResponse, drawerEl: HTMLElement): void {
        var color = gradeColor(data.score);
        var html =
            '<div style="display: flex; align-items: center; gap: 1.25rem; flex-wrap: wrap;">' +
            '<div style="width: 92px; height: 92px; border-radius: 50%; border: 6px solid ' +
            color +
            ";" +
            ' display: flex; flex-direction: column; align-items: center; justify-content: center; background: var(--bg-subtle);">' +
            '<div style="font-size: 1.5rem; font-weight: 800; color: ' +
            color +
            '; line-height: 1;">' +
            data.score +
            "</div>" +
            '<div style="font-size: 0.625rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); margin-top: 0.2rem;">/ 100</div>' +
            "</div>" +
            "<div>" +
            '<div style="font-weight: 700; font-size: 1.125rem; color: ' +
            color +
            ';">' +
            esc(data.grade) +
            "</div>" +
            '<div class="form-hint">' +
            esc(data.case_number) +
            " &middot; " +
            esc(String(data.case_type).replace("_", " ")) +
            " &middot; " +
            data.rules_run +
            " rules evaluated</div>" +
            "</div>" +
            "</div>";

        if (data.errors && data.errors.length) {
            html +=
                '<div class="card card-sm mt--md"><div class="card-header"><h4><i class="fa-solid fa-circle-xmark"></i> Errors (' +
                data.errors.length +
                ")</h4></div>" +
                findingList(data.errors, "fa-circle-xmark", "#b3261e") +
                "</div>";
        }
        if (data.warnings && data.warnings.length) {
            html +=
                '<div class="card card-sm mt--sm"><div class="card-header"><h4><i class="fa-solid fa-triangle-exclamation"></i> Warnings (' +
                data.warnings.length +
                ")</h4></div>" +
                findingList(data.warnings, "fa-triangle-exclamation", "#c77d0a") +
                "</div>";
        }
        if (data.suggestions && data.suggestions.length) {
            html +=
                '<div class="card card-sm mt--sm"><div class="card-header"><h4><i class="fa-solid fa-lightbulb"></i> Suggestions</h4></div><ul style="margin: 0; padding-left: 1.25rem; line-height: 1.7;">';
            data.suggestions.forEach(function (s: string) {
                html += '<li class="form-hint" style="margin: 0.15rem 0;">' + esc(s) + "</li>";
            });
            html += "</ul></div>";
        }
        setHTML(drawerEl, html);
    }

    function post(
        endpoint: string,
        payload: { case_id: number; case_type: string },
        drawerEl: HTMLElement,
        statusEl: HTMLElement | null,
        btn: HTMLElement,
        originalHtml: string,
        onDone?: () => void,
    ): void {
        (btn as HTMLButtonElement).disabled = true;
        setHTML(btn, '<i class="fa-solid fa-spinner fa-spin"></i> Validating\u2026');
        if (statusEl) statusEl.textContent = "Processing\u2026";
        drawerEl.style.display = "none";

        fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
            .then(function (resp: Response) {
                return resp.json().then(function (data: ValidationReportResponse & { error?: string }) {
                    return { ok: resp.ok, data: data };
                });
            })
            .then(function (out: { ok: boolean; data: ValidationReportResponse & { error?: string } }) {
                if (!out.ok) {
                    if (statusEl) statusEl.textContent = out.data.error || "Validation failed";
                    return;
                }
                renderReport(out.data as ValidationReportResponse, drawerEl);
                drawerEl.style.display = "block";
                if (statusEl) statusEl.textContent = "";
                drawerEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
            })
            .catch(function () {
                if (statusEl) statusEl.textContent = "Request failed \u2014 please try again.";
            })
            .finally(function () {
                (btn as HTMLButtonElement).disabled = false;
                setHTML(btn, originalHtml);
                if (onDone) onDone();
            });
    }

    function initRowButtons(opts: ValidationDrawerRowOpts): void {
        ready(function () {
            var buttons = document.querySelectorAll(opts.buttonsSelector || ".js-validate-case");
            var drawerEl = document.getElementById(opts.drawerId);
            if (!buttons.length || !drawerEl) return;
            var statusEl = opts.statusId ? document.getElementById(opts.statusId) : null;
            var endpoint = opts.endpoint;
            var busy = false;

            Array.prototype.forEach.call(buttons, function (btn: HTMLElement) {
                var originalHtml = btn.innerHTML;
                btn.addEventListener("click", function () {
                    if (busy) return;
                    var caseId = parseInt(btn.getAttribute("data-case-id") || "0", 10);
                    var caseType = btn.getAttribute("data-case-type") || "case_file";
                    if (!caseId || caseId < 1) {
                        if (statusEl) statusEl.textContent = "Invalid case ID on this button.";
                        return;
                    }
                    busy = true;
                    post(
                        endpoint,
                        { case_id: caseId, case_type: caseType },
                        drawerEl!,
                        statusEl,
                        btn,
                        originalHtml,
                        function () {
                            busy = false;
                        },
                    );
                });
            });
        });
    }

    function initForm(opts: ValidationDrawerFormOpts): void {
        ready(function () {
            var btn = document.getElementById(opts.buttonId);
            var caseIdInput = document.getElementById(opts.caseIdInputId) as HTMLInputElement | null;
            var drawerEl = document.getElementById(opts.drawerId);
            if (!btn || !caseIdInput || !drawerEl) return;
            var statusEl = opts.statusId ? document.getElementById(opts.statusId) : null;
            var typeSelect = document.getElementById(opts.typeSelectId) as HTMLSelectElement | null;
            var endpoint = opts.endpoint;
            var originalHtml = btn.innerHTML;

            btn.addEventListener("click", function () {
                var caseId = parseInt(caseIdInput!.value, 10);
                if (!caseId || caseId < 1) {
                    if (statusEl) statusEl.textContent = "Enter a numeric case ID first.";
                    return;
                }
                var caseType = typeSelect ? typeSelect.value : "case_file";
                post(
                    endpoint,
                    { case_id: caseId, case_type: caseType },
                    drawerEl!,
                    statusEl,
                    btn!,
                    originalHtml,
                );
            });
        });
    }

    window.ValidationDrawer = {
        initForm: initForm,
        initRowButtons: initRowButtons,
    };
})(window, document);
