/* Search snippet rendering helpers (search page).
 *
 * Used by app/search/templates/search/index.html to render result titles and
 * snippets safely: only the literal <mark> markers injected by the server
 * become real tags; everything else is HTML-escaped.
 */
(function (global) {
    "use strict";

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function renderSnippet(snippet) {
        if (!snippet) {
            return "";
        }
        // Escaping the whole string and then un-escaping markers would also
        // un-escape literal "<mark>" text coming from DB content (e.g. OCR
        // text), opening an XSS vector — so split on the markers and escape
        // only the non-marker segments.
        return String(snippet)
            .split(/(<mark>|<\/mark>)/)
            .map(function (part) {
                return part === "<mark>" || part === "</mark>" ? part : escapeHtml(part);
            })
            .join("");
    }

    global.escapeHtml = escapeHtml;
    global.renderSnippet = renderSnippet;
})(window);
