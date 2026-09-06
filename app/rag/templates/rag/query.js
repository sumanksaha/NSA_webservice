/**
 * RAG Query UI — client-side handler for the Legal RAG query page.
 *
 * Wires up:
 *   - Form submission → POST /api/v2/rag/query
 *   - Result display → render answer, citations, verification, audit trail
 *   - Human review → approve/reject → transition to finalize
 */

const AGENT_API_BASE = "/api/v2/rag/";

async function submitQuery(query, collection = "fssai_legal_768") {
    const formData = new FormData();
    formData.append("query", query);
    formData.append("collection", collection);

    const resp = await fetch(`${AGENT_API_BASE}/query`, {
        method: "POST",
        body: formData,
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });

    if (!resp.ok) throw new Error(`Query submitted: ${resp.status}`);
    return await resp.json();
}

function renderResult(result) {
    const container = document.getElementById("ragResults");
    if (!container) return;

    // Answer
    const answerDiv = document.getElementById("ragAnswer");
    if (answerDiv) {
        answerDiv.innerHTML = `<p><strong>Answer:</strong> ${escapeHtml(result.answer)}</p>`;
    }

    // Citations
    const citationsDiv = document.getElementById("ragCitations");
    if (citationsDiv) {
        citationsDiv.innerHTML = `<p>Citations: ${result.citations || 0}</p>`;
    }

    // Verification status
    const verDiv = document.getElementById("ragVerification");
    if (verDiv) {
        verDiv.innerHTML = `<p>${result.verification_status || "Pending"}</p>`;
    }

    // Audit trail
    const trailDiv = document.getElementById("ragAuditTrail");
    if (trailDiv) {
        trailDiv.innerHTML = `<p>Audit trail: ${result.audit_trail?.length || 0} entries</p>`;
    }
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// Handle form submit
document.getElementById("ragSubmitBtn").addEventListener("click", async () => {
    const query = document.getElementById("ragQuery").value.trim();
    if (!query) return;

    try {
        const result = await submitQuery(
            query,
            document.getElementById("ragCollection")?.value || "fssai_legal_768"
        );
        renderResult(result);
    } catch (err) {
        showError(err.message);
    }
});

// Auto-submit on Enter key
document.getElementById("ragQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.value.trim()) {
        submitQuery(
            e.target.value.trim(),
            document.getElementById("ragCollection")?.value || "fssai_legal_768"
        );
    }
});

function showError(msg) {
    const errDiv = document.getElementById("ragError");
    if (errDiv) {
        errDiv.style.display = "block";

        // Hide results
        const resultsDiv = document.getElementById("ragResults");
        if (resultsDiv) resultsDiv.style.display = "none";

        // Show error
        const errorDiv = document.createElement("div");
        errorDiv.id = "ragError";
        errorDiv.style.cssText =
            "margin-top:24px;padding:12px;background:#fee2e2;border:1px solid #ef4444;color:#dc2626;font-family:Arial,sans-serif;";
        errorDiv.textContent = msg;
        errDiv.replaceWith(errorDiv);
    }
}
