/**
 * Shared helpers for QStash-queued task submission + status polling.
 * Used by bill generator, case file generator, and inspection photo upload.
 *
 * Migrated from task_status.js — compiled output is served from
 * app/static/js/task_status.js via tsc.
 */
import type {
    TaskPollResult,
    PollOptions,
    TaskStatusRecord,
} from "./types/api.js";

(function () {
    "use strict";

    // -----------------------------------------------------------------------
    // Constants
    // -----------------------------------------------------------------------

    var STATUS_URL = "/tasks/status/";
    var DOWNLOAD_URL = "/tasks/download?path=";
    var POLL_INTERVAL_MS = 3000;
    var MAX_POLLS = 40; // ~2 minutes

    // -----------------------------------------------------------------------
    // Internal error type for submitAndPoll's fetch error path
    // -----------------------------------------------------------------------

    /** Custom error shape附加 to Error in submitAndPoll's catch block. */
    interface SubmitFetchError extends Error {
        status?: number;
        errors?: Record<string, string> | null;
        data?: Record<string, unknown> | null;
    }

    // -----------------------------------------------------------------------
    // URL builders
    // -----------------------------------------------------------------------

    function taskStatusUrl(taskId: string): string {
        return STATUS_URL + encodeURIComponent(taskId);
    }

    function downloadUrl(filePath: string): string {
        return DOWNLOAD_URL + encodeURIComponent(filePath);
    }

    // -----------------------------------------------------------------------
    // Core: submitAndPoll
    // -----------------------------------------------------------------------

    /**
     * Submit a form via fetch(), then poll the returned task_id until
     * completed/error. Calls onDone({ status, result, error, errors }).
     * `errors` (when present) is a structured {field: message} map from the
     * server used by forms to highlight invalid inputs inline.
     */
    function submitAndPoll(
        form: HTMLFormElement,
        onDone: (result: TaskPollResult) => void,
        opts?: PollOptions,
    ): void {
        var resolvedOpts = opts || {};
        var formData = new FormData(form);
        fetch(form.action, { method: "POST", body: formData })
            .then(function (resp: Response) {
                return resp.json().then(function (data: Record<string, unknown>) {
                    if (!resp.ok) {
                        var err: SubmitFetchError = new Error(
                            (data.error as string) ||
                                "Request failed (" + resp.status + ")",
                        );
                        err.status = resp.status;
                        err.errors = (data.errors as Record<string, string>) || null;
                        err.data = data; // full body — e.g. bill_id when only the PDF step failed
                        throw err;
                    }
                    return data;
                });
            })
            .then(function (data: Record<string, unknown>) {
                // Synchronous path: result is inline (no task_id).
                if (!data.task_id) {
                    onDone({ status: "completed", result: data });
                    return;
                }
                // Async (QStash): poll the status store until done.
                pollStatus(data.task_id as string, onDone, resolvedOpts);
            })
            .catch(function (err: SubmitFetchError) {
                onDone({
                    status: "error",
                    error: err.message,
                    errors: err.errors || null,
                    data: err.data || null,
                    result: null,
                });
            });
    }

    // -----------------------------------------------------------------------
    // Core: pollStatus
    // -----------------------------------------------------------------------

    /**
     * Poll a task's status endpoint until completed/error (no form submit).
     * Single canonical polling loop shared by submitAndPoll and direct callers.
     */
    function pollStatus(
        taskId: string,
        onDone: (result: TaskPollResult) => void,
        opts?: PollOptions,
    ): void {
        var resolvedOpts = opts || {};
        var interval = resolvedOpts.interval || POLL_INTERVAL_MS;
        var maxPolls = resolvedOpts.maxPolls || MAX_POLLS;
        var attempts = 0;
        var timer = setInterval(function () {
            attempts += 1;
            fetch(taskStatusUrl(taskId))
                .then(function (resp: Response) {
                    if (resp.status === 404) return null;
                    return resp.json();
                })
                .then(function (record: TaskStatusRecord | null) {
                    if (attempts >= maxPolls) {
                        clearInterval(timer);
                        onDone({
                            status: "error",
                            error: "Timed out waiting for task.",
                            result: null,
                        });
                        return;
                    }
                    if (!record) return; // not tracked yet — keep polling
                    if (record.status === "completed") {
                        clearInterval(timer);
                        onDone({
                            status: "completed",
                            result: record.result || {},
                            task: record.task,
                        });
                        return;
                    }
                    if (record.status === "error") {
                        clearInterval(timer);
                        onDone({
                            status: "error",
                            error: record.error || "Task failed.",
                            result: record.result || null,
                        });
                    }
                    // pending / running — keep polling
                })
                .catch(function (err: Error) {
                    if (attempts >= maxPolls) {
                        clearInterval(timer);
                        onDone({
                            status: "error",
                            error: err.message,
                            result: null,
                        });
                    }
                });
        }, interval);
    }

    // -----------------------------------------------------------------------
    // downloadLink
    // -----------------------------------------------------------------------

    /**
     * Build a download link markup for a completed task result.
     */
    function downloadLink(
        result: Record<string, unknown> | null,
        label?: string,
    ): string {
        var filePath = result && (result.file_path as string | undefined);
        if (!filePath) return "";
        var text = label || "Download file";
        return (
            '<a class="btn btn-primary btn-sm" href="' +
            downloadUrl(filePath) +
            '" target="_blank" rel="noopener"><i class="fa-solid fa-file-arrow-down"></i> ' +
            text +
            "</a>"
        );
    }

    // -----------------------------------------------------------------------
    // Expose on window
    // -----------------------------------------------------------------------

    window.TaskPoll = {
        submitAndPoll: submitAndPoll,
        pollStatus: pollStatus,
        taskStatusUrl: taskStatusUrl,
        downloadUrl: downloadUrl,
        downloadLink: downloadLink,
    };
})();
