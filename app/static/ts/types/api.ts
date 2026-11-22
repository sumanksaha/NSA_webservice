/**
 * Shared API type contracts for the NSA Webservice frontend.
 *
 * All interfaces are sourced from the actual Flask route return values.
 * Fields whose runtime type varies are flagged with comments.
 *
 * Architecture note: these are declared as top-level (global) interfaces
 * so they can be consumed by vanilla-JS-style TS files (IIFEs) via
 * `/// <reference>` without requiring ES module imports.
 *
 * @module api
 */

// ---------------------------------------------------------------------------
// Common error envelope
// ---------------------------------------------------------------------------

/** Standard error shape returned by most Flask endpoints on failure. */
export interface ApiError {
    error: string;
}

/** Error with field-level validation details (form endpoints). */
export interface ValidationError extends ApiError {
    /**
     * Map of field names to human-readable error messages.
     * Populated by validate_case_file_form / validate_adjudication_form.
     */
    errors: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Preview endpoints
// ---------------------------------------------------------------------------

/** POST /case_file_generator/preview — 200 response. */
export interface CaseFilePreviewResponse {
    petition_html: string;
    permission_html: string;
    case_number: string;
}

/** POST /adjudication/preview — 200 response. */
export interface AdjudicationPreviewResponse {
    petition_html: string;
    permission_html: string;
    case_number: string;
}

// ---------------------------------------------------------------------------
// Task status polling (task_status.js)
// ---------------------------------------------------------------------------

/**
 * Task status record as returned by GET /tasks/status/<message_id>.
 *
 * Shape: stored in Redis by qstash_client.py::store_task_status(),
 * merged with the message_id at return time.
 *
 * AMBIGUOUS FIELD: `result` varies by task — bill generator returns
 * {file_path: string}, case file generator returns {case_id, file_path, ...},
 * photo upload returns {inspection_id, ...}. Type is `Record<string, unknown>`
 * until task-specific subtypes are added.
 */
export interface TaskStatusRecord {
    message_id: string;
    status: "unknown" | "pending" | "running" | "completed" | "error" | "failed";
    /** Task-specific result payload. Shape varies by task name. */
    result?: Record<string, unknown> | null;
    /** Human-readable error message (populated on status "error" / "failed"). */
    error?: string;
    /** Task name, e.g. "generate_bill_pdf". */
    task?: string;
}

/**
 * Normalised result delivered to the onDone callback by submitAndPoll / pollStatus.
 * This is the frontend's canonical view — NOT the raw server record.
 */
export interface TaskPollResult {
    status: "completed" | "error";
    result: Record<string, unknown> | null;
    error?: string;
    /** Field-level validation errors from form submission. */
    errors?: Record<string, string> | null;
    /** Full response body when the initial POST fails (e.g. bill_id present even on PDF failure). */
    data?: Record<string, unknown> | null;
    task?: string;
}

/**
 * Options for pollStatus — controls timing behaviour.
 */
export interface PollOptions {
    /** Milliseconds between polls (default: 3000). */
    interval?: number;
    /** Max poll attempts before timeout (default: 40). */
    maxPolls?: number;
}

// ---------------------------------------------------------------------------
// Window globals set by Jinja2 templates
// ---------------------------------------------------------------------------

/** Augment the global Window interface with template-injected globals. */
declare global {
    interface Window {
        /** TaskPoll facade exposed by task_status.js. */
        TaskPoll: {
            submitAndPoll(
                form: HTMLFormElement,
                onDone: (result: TaskPollResult) => void,
                opts?: PollOptions
            ): void;
            pollStatus(
                taskId: string,
                onDone: (result: TaskPollResult) => void,
                opts?: PollOptions
            ): void;
            taskStatusUrl(taskId: string): string;
            downloadUrl(filePath: string): string;
            downloadLink(result: Record<string, unknown>, label?: string): string;
        };
        /** ValidationDrawer facade exposed by validation_drawer.js. */
        ValidationDrawer: {
            initForm(opts: ValidationDrawerFormOpts): void;
            initRowButtons(opts: ValidationDrawerRowOpts): void;
        };
        /** RagQueryUI facade exposed by rag_query.js. */
        RagQueryUI: {
            init(): void;
            esc(s: unknown): string;
            fmtNum(n: unknown, digits?: number): string;
            truncate(text: string, max?: number): string;
            showStatus(el: HTMLElement, message: string, kind?: string): void;
            hideStatus(el: HTMLElement): void;
            setLoading(btn: HTMLElement, loading: boolean): void;
            loadHistory(): RagHistoryEntry[];
            saveHistoryEntry(query: string, data: Record<string, unknown>): void;
            clearHistory(): void;
            renderHistory(): void;
        };
    }
}

// ---------------------------------------------------------------------------
// Validation drawer types (validation_drawer.js)
// ---------------------------------------------------------------------------

/** A single finding (error or warning) from the validation engine. */
export interface ValidationFinding {
    message: string;
    field_name?: string;
    suggestion?: string;
}

/** POST /validation/validate — 200 response. */
export interface ValidationReportResponse {
    score: number;
    grade: string;
    case_number: string;
    case_type: string;
    rules_run: number;
    errors: ValidationFinding[];
    warnings: ValidationFinding[];
    suggestions: string[];
}

/** Options for ValidationDrawer.initRowButtons(). */
export interface ValidationDrawerRowOpts {
    buttonsSelector?: string;
    drawerId: string;
    statusId?: string;
    endpoint: string;
}

/** Options for ValidationDrawer.initForm(). */
export interface ValidationDrawerFormOpts {
    buttonId: string;
    caseIdInputId: string;
    typeSelectId: string;
    drawerId: string;
    statusId?: string;
    endpoint: string;
}

// ---------------------------------------------------------------------------
// RAG query types (rag_query.js)
// ---------------------------------------------------------------------------

/** POST /api/rag/query/agent — 200 response (RAGResponse schema). */
export interface RagResponse {
    answer: string;
    groundedness_score: number;
    confidence: number;
    total_latency_ms: number;
    llm_model?: string;
    pipeline: "legacy" | "agent";
    citations: RagCitation[];
    retrieved_chunks: RagChunk[];
    hallucination_detected: boolean;
    hallucinated_claims?: string[];
    verification?: RagVerification;
    agent?: RagAgentInfo;
}

/** Citation in a RAG response. */
export interface RagCitation {
    document_title: string;
    section_number?: string;
    snippet: string;
    confidence: number;
}

/** Retrieved chunk in a RAG response. */
export interface RagChunk {
    document_title: string;
    section_number?: string;
    act_name?: string;
    text: string;
    score: number;
}

/** Verification block from the hallucination detector. */
export interface RagVerification {
    enabled: boolean;
    detected?: boolean;
    claims_verified?: number;
    claims_total?: number;
    claims_unverified?: number;
    escalated_claims?: number;
    groundedness_score?: number;
    error?: string;
}

/** Agent pipeline info block. */
export interface RagAgentInfo {
    retry_count?: number;
    expanded_query?: string;
}

/** POST /api/rag/query/agent — 202 response (HITL review pause). */
export interface RagReviewPause {
    thread_id: string;
    review: RagReviewPayload;
}

/** Payload shown to the user during HITL review. */
export interface RagReviewPayload {
    query?: string;
    proposed_answer?: string;
}

/** POST /api/rag/query/agent/resume — request body. */
export interface RagResumeRequest {
    thread_id: string;
    approved: boolean;
}

/** RAG history entry stored in localStorage. */
export interface RagHistoryEntry {
    query: string;
    ts: number;
    data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Case list items (timeline picker in base.html)
// ---------------------------------------------------------------------------

/** Item from GET /case_file_generator/list_cases or /adjudication/list_cases. */
export interface CaseListItem {
    id: number;
    case_number: string;
    manufacturer_name?: string;
    fbo_name?: string;
    product_name?: string;
}
