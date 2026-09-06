(function (window, _document) {
    "use strict";
    function esc(s) {
        if (s == null)
            return "";
        var d = document.createElement("div");
        d.textContent = String(s);
        return d.innerHTML;
    }
    function fmtNum(n, digits) {
        digits = digits == null ? 3 : digits;
        if (n == null || isNaN(n))
            return "\u2014";
        return Number(n).toFixed(digits);
    }
    function truncate(text, max) {
        text = esc(text || "");
        max = max || 280;
        if (text.length <= max)
            return text;
        return text.slice(0, max - 3) + "\u2026";
    }
    function showStatus(el, message, kind) {
        kind = kind || "info";
        el.className = "rag-status rag-status--" + kind + " visible";
        el.innerHTML = '<span class="rag-spinner" style="margin-right:0.5rem;"></span> ' + message;
        el.style.display = "block";
    }
    function hideStatus(el) {
        el.className = "rag-status";
        el.style.display = "none";
    }
    function setLoading(btn, loading) {
        var icon = btn.querySelector("i");
        if (loading) {
            btn.disabled = true;
            if (icon)
                icon.className = "fa-solid fa-spinner fa-spin";
        }
        else {
            btn.disabled = false;
            if (icon)
                icon.className = "fa-solid fa-paper-plane";
        }
    }
    var _reviewState = null;
    function showReview(reviewPayload) {
        var reviewEl = document.getElementById("ragReview");
        var contentEl = document.getElementById("ragReviewContent");
        var resultsEl = document.getElementById("ragResults");
        if (!reviewEl || !contentEl || !resultsEl)
            return;
        contentEl.innerHTML =
            "<strong>Proposed answer:</strong> " +
                esc(truncate(reviewPayload.proposed_answer || "", 300)) ||
                esc("Review the agent's proposed answer before finalizing.");
        resultsEl.innerHTML = "";
        reviewEl.style.display = "block";
    }
    function hideReview() {
        var reviewEl = document.getElementById("ragReview");
        if (reviewEl)
            reviewEl.style.display = "none";
    }
    function renderResponse(data) {
        var resultsEl = document.getElementById("ragResults");
        var statusEl = document.getElementById("ragStatus");
        if (!resultsEl || !statusEl)
            return;
        hideStatus(statusEl);
        var citationHtml = "";
        var citations = data.citations || [];
        if (citations.length) {
            citationHtml = '<div class="rag-citations">';
            citations.forEach(function (c) {
                var label = c.section_number
                    ? c.document_title + " \u00a7" + c.section_number
                    : c.document_title || "Source";
                citationHtml +=
                    '<div class="rag-citation">' +
                        '<span class="rag-citation-label">' +
                        esc(label) +
                        "</span>" +
                        '<span class="rag-citation-snippet">' +
                        truncate(c.snippet, 200) +
                        "</span>" +
                        '<span class="rag-citation-confidence">conf: ' +
                        fmtNum(c.confidence) +
                        "</span>" +
                        "</div>";
            });
            citationHtml += "</div>";
        }
        var hallucHtml = "";
        if (data.hallucination_detected) {
            hallucHtml =
                '<div class="rag-hallucination">' +
                    "<strong>\u26a0 Hallucination detected</strong> \u2014 " +
                    esc((data.hallucinated_claims || []).join(". ")) +
                    "</div>";
        }
        var verificationHtml = "";
        var ver = data.verification;
        if (ver && ver.enabled) {
            if (ver.error) {
                verificationHtml =
                    '<div class="rag-verification" title="' +
                        esc(ver.error) +
                        '">' +
                        "Claim verification unavailable for this answer." +
                        "</div>";
            }
            else {
                var verified = ver.claims_verified || 0;
                var total = ver.claims_total || 0;
                var unverified = ver.claims_unverified || 0;
                var cls = ver.detected ? " rag-verification-warning" : "";
                verificationHtml =
                    '<div class="rag-verification' +
                        cls +
                        '">' +
                        (ver.detected ? "\u26a0 " : "\u2713 ") +
                        "Claim verification \u2014 " +
                        verified +
                        "/" +
                        total +
                        " claims evidence-backed" +
                        (unverified > 0 ? " \u00b7 " + unverified + " unverified" : "") +
                        (ver.escalated_claims && ver.escalated_claims > 0
                            ? " \u00b7 " + ver.escalated_claims + " escalated"
                            : "") +
                        " \u00b7 score: " +
                        fmtNum(ver.groundedness_score) +
                        "</div>";
            }
        }
        var chunkHtml = "";
        var chunks = data.retrieved_chunks || [];
        if (chunks.length) {
            chunkHtml =
                '<div style="margin-top:1rem;"><small style="color:var(--text-muted,#6b7280);font-weight:600;">' +
                    "Retrieved context (" +
                    chunks.length +
                    " chunk" +
                    (chunks.length > 1 ? "s" : "") +
                    ")" +
                    '</small><div class="rag-chunks">';
            chunks.forEach(function (ch) {
                var title = ch.document_title || "Untitled";
                var sec = ch.section_number ? " \u00a7" + ch.section_number : "";
                var act = ch.act_name ? " / " + ch.act_name : "";
                chunkHtml +=
                    '<div class="rag-chunk">' +
                        '<div class="rag-chunk-header">' +
                        '<span class="rag-chunk-title">' +
                        esc(title + sec + act) +
                        "</span>" +
                        '<span class="rag-chunk-score">score: ' +
                        fmtNum(ch.score, 4) +
                        "</span>" +
                        "</div>" +
                        '<div class="rag-chunk-text">' +
                        truncate(ch.text, 300) +
                        "</div>" +
                        "</div>";
            });
            chunkHtml += "</div></div>";
        }
        var agentHtml = "";
        if (data.pipeline === "agent" && data.agent) {
            var ag = data.agent;
            agentHtml =
                '<div class="rag-agent-block">' +
                    "Pipeline: agent | retries: " +
                    (ag.retry_count || 0) +
                    (ag.expanded_query
                        ? " | expanded query: " + esc(truncate(ag.expanded_query, 100))
                        : "") +
                    "</div>";
        }
        var stubHtml = "";
        if (data.llm_model && String(data.llm_model).indexOf("stub") === 0) {
            stubHtml =
                '<div class="rag-stub-warning">' +
                    "\u26a0 <strong>Stub mode</strong> \u2014 this answer was generated " +
                    "without a live LLM (no OPENROUTER_API_KEY configured on the server)." +
                    "</div>";
        }
        var html = '<div class="rag-answer-card">' +
            stubHtml +
            '<div class="rag-answer-meta">' +
            '<span class="rag-gauge">Groundedness: ' +
            fmtNum(data.groundedness_score) +
            "</span>" +
            '<span class="rag-gauge">Confidence: ' +
            fmtNum(data.confidence) +
            "</span>" +
            '<span class="rag-gauge">Latency: ' +
            (data.total_latency_ms || 0) +
            " ms</span>" +
            (data.llm_model
                ? '<span class="rag-gauge">Model: ' + esc(data.llm_model) + "</span>"
                : "") +
            "</div>" +
            '<div class="rag-answer-text">' +
            esc(data.answer || "(no answer generated)") +
            "</div>" +
            hallucHtml +
            verificationHtml +
            citationHtml +
            agentHtml +
            chunkHtml +
            "</div>";
        resultsEl.innerHTML = html;
    }
    var USER_FRIENDLY_ERRORS = {
        400: "Your question could not be processed. Please check the wording and try again.",
        500: "An unexpected error occurred. Please try again shortly.",
        503: "The legal knowledge base is currently offline. Please try again in a few minutes.",
    };
    function renderError(message, statusCode) {
        console.error("[RAG]", message);
        var friendly = (statusCode && USER_FRIENDLY_ERRORS[statusCode]) || message;
        var resultsEl = document.getElementById("ragResults");
        var statusEl = document.getElementById("ragStatus");
        if (!resultsEl || !statusEl)
            return;
        hideStatus(statusEl);
        resultsEl.innerHTML =
            '<div class="rag-answer-card" style="border-color:#fecaca;">' +
                '<div class="rag-answer-text" style="color:#b91c1c;">' +
                esc(friendly) +
                "</div></div>";
    }
    function getPayload() {
        var query = document.getElementById("ragQuery").value.trim();
        var domain = document.getElementById("ragDomain").value;
        var topK = parseInt(document.getElementById("ragTopK").value, 10) || 10;
        var useAgent = document.getElementById("ragUseAgent");
        var payload = { query: query, top_k: topK };
        if (domain) {
            payload.collection_name = domain + "_legal_768";
        }
        if (useAgent) {
            payload.use_agent = useAgent.checked;
        }
        return { payload: payload, query: query };
    }
    function validateQuery(query) {
        if (!query)
            return "Please enter a legal question.";
        if (query.length > 2000)
            return "Question is too long (max 2000 characters).";
        return null;
    }
    function submitQuery() {
        var btn = document.getElementById("ragSubmitBtn");
        var statusEl = document.getElementById("ragStatus");
        var resultsEl = document.getElementById("ragResults");
        var ctx = getPayload();
        var err = validateQuery(ctx.query);
        if (err) {
            showStatus(statusEl, err, "error");
            return;
        }
        setLoading(btn, true);
        showStatus(statusEl, "Querying the legal knowledge base\u2026", "info");
        resultsEl.innerHTML = "";
        hideReview();
        var body = JSON.parse(JSON.stringify(ctx.payload));
        if (_reviewState && _reviewState.thread_id) {
            body.thread_id = _reviewState.thread_id;
        }
        fetch("/api/rag/query/agent", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (resp) {
            return resp.json().then(function (data) {
                return { ok: resp.ok, status: resp.status, data: data };
            });
        })
            .then(function (out) {
            setLoading(btn, false);
            hideStatus(statusEl);
            if (out.status === 202) {
                _reviewState = {
                    thread_id: out.data.thread_id,
                    review: out.data.review,
                };
                showStatus(statusEl, "Agent paused \u2014 awaiting your review.", "info");
                showReview(out.data.review || {});
                return;
            }
            if (!out.ok) {
                var msg = out.data && out.data.error
                    ? String(out.data.error)
                    : "Request failed (HTTP " + out.status + ")";
                showStatus(statusEl, msg, "error");
                renderError(msg, out.status);
                return;
            }
            showStatus(statusEl, "Answer generated.", "success");
            renderResponse(out.data);
            saveHistoryEntry(ctx.query, out.data);
        })
            .catch(function () {
            setLoading(btn, false);
            hideStatus(statusEl);
            showStatus(statusEl, "Network error \u2014 please try again.", "error");
            renderError("Network error \u2014 please check your connection.");
        });
    }
    function resumeReview(approved) {
        if (!_reviewState || !_reviewState.thread_id)
            return;
        var btn = approved
            ? document.getElementById("ragApproveBtn")
            : document.getElementById("ragRejectBtn");
        var statusEl = document.getElementById("ragStatus");
        setLoading(btn, true);
        showStatus(statusEl, approved ? "Approving\u2026" : "Rejecting and retrying\u2026", "info");
        hideReview();
        fetch("/api/rag/query/agent/resume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ thread_id: _reviewState.thread_id, approved: approved }),
        })
            .then(function (resp) {
            return resp.json().then(function (data) {
                return { ok: resp.ok, status: resp.status, data: data };
            });
        })
            .then(function (out) {
            var a = document.getElementById("ragApproveBtn");
            var r = document.getElementById("ragRejectBtn");
            if (a)
                a.disabled = false;
            if (r)
                r.disabled = false;
            hideStatus(statusEl);
            if (out.status === 202) {
                _reviewState = { thread_id: out.data.thread_id, review: out.data.review };
                showStatus(statusEl, "Agent paused again \u2014 awaiting review.", "info");
                showReview(out.data.review || {});
                return;
            }
            if (!out.ok) {
                var msg = out.data && out.data.error
                    ? String(out.data.error)
                    : "Resume failed (HTTP " + out.status + ")";
                showStatus(statusEl, msg, "error");
                renderError(msg, out.status);
                return;
            }
            showStatus(statusEl, "Answer finalized.", "success");
            var reviewedQuery = _reviewState && _reviewState.review ? _reviewState.review.query || "" : "";
            renderResponse(out.data);
            saveHistoryEntry(reviewedQuery, out.data);
        })
            .catch(function () {
            var a = document.getElementById("ragApproveBtn");
            var r = document.getElementById("ragRejectBtn");
            if (a)
                a.disabled = false;
            if (r)
                r.disabled = false;
            hideStatus(statusEl);
            showStatus(statusEl, "Network error \u2014 please try again.", "error");
            renderError(" Network error \u2014 please try again.");
        });
    }
    // ------------------------------------------------------------------ //
    // Session history (localStorage)                                      //
    // ------------------------------------------------------------------ //
    var HISTORY_KEY = "ragSessionHistory";
    var HISTORY_MAX = 20;
    function pad2(n) {
        return (n < 10 ? "0" : "") + n;
    }
    function loadHistory() {
        try {
            var raw = window.localStorage.getItem(HISTORY_KEY);
            var items = raw ? JSON.parse(raw) : [];
            return Array.isArray(items) ? items : [];
        }
        catch {
            return [];
        }
    }
    function persistHistory(items) {
        try {
            window.localStorage.setItem(HISTORY_KEY, JSON.stringify(items));
        }
        catch {
            /* storage unavailable */
        }
    }
    function saveHistoryEntry(query, data) {
        if (!query && !(data && data.answer))
            return;
        var items = loadHistory();
        items.unshift({ query: query || "", ts: Date.now(), data: data || {} });
        if (items.length > HISTORY_MAX)
            items = items.slice(0, HISTORY_MAX);
        persistHistory(items);
        renderHistory();
    }
    function clearHistory() {
        persistHistory([]);
        renderHistory();
    }
    function renderHistory() {
        var panel = document.getElementById("ragHistoryPanel");
        if (!panel)
            return;
        var items = loadHistory();
        if (!items.length) {
            panel.style.display = "none";
            panel.innerHTML = "";
            return;
        }
        var html = '<div class="rag-history-header">' +
            "<strong>Session history</strong> (" +
            items.length +
            ")" +
            ' <button id="ragClearHistoryBtn" type="button" class="rag-history-clear">Clear</button>' +
            "</div>";
        items.forEach(function (item, i) {
            var when = new Date(item.ts || Date.now());
            var hhmm = pad2(when.getHours()) + ":" + pad2(when.getMinutes());
            html +=
                '<div class="rag-history-item" data-idx="' +
                    i +
                    '">' +
                    '<span class="rag-history-q">' +
                    truncate(item.query || "(no question)", 120) +
                    "</span>" +
                    '<span class="rag-history-meta">' +
                    hhmm +
                    (item.data && item.data.llm_model ? " \u00b7 " + esc(item.data.llm_model) : "") +
                    "</span>" +
                    "</div>";
        });
        panel.innerHTML = html;
        panel.style.display = "block";
        var clearBtn = document.getElementById("ragClearHistoryBtn");
        if (clearBtn)
            clearBtn.addEventListener("click", clearHistory);
        Array.prototype.forEach.call(panel.querySelectorAll(".rag-history-item"), function (el) {
            el.addEventListener("click", function () {
                var idx = parseInt(el.getAttribute("data-idx") || "0", 10);
                var item = loadHistory()[idx];
                if (!item)
                    return;
                var ta = document.getElementById("ragQuery");
                if (ta)
                    ta.value = item.query || "";
                hideReview();
                renderResponse(item.data);
            });
        });
    }
    function init() {
        var ready = function () {
            var submitBtn = document.getElementById("ragSubmitBtn");
            var approveBtn = document.getElementById("ragApproveBtn");
            var rejectBtn = document.getElementById("ragRejectBtn");
            var queryInput = document.getElementById("ragQuery");
            if (!submitBtn)
                return;
            submitBtn.addEventListener("click", submitQuery);
            renderHistory();
            if (approveBtn)
                approveBtn.addEventListener("click", function () {
                    resumeReview(true);
                });
            if (rejectBtn)
                rejectBtn.addEventListener("click", function () {
                    resumeReview(false);
                });
            if (queryInput) {
                queryInput.addEventListener("keydown", function (e) {
                    if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        submitQuery();
                    }
                });
            }
        };
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", ready);
        }
        else {
            ready();
        }
    }
    window.RagQueryUI = {
        esc: esc,
        fmtNum: fmtNum,
        truncate: truncate,
        showStatus: showStatus,
        hideStatus: hideStatus,
        setLoading: setLoading,
        init: init,
        loadHistory: loadHistory,
        saveHistoryEntry: saveHistoryEntry,
        clearHistory: clearHistory,
        renderHistory: renderHistory,
    };
})(window, document);
//# sourceMappingURL=rag_query.js.map