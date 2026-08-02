// Shared helpers for QStash-queued task submission + status polling.
// Used by bill generator, case file generator, and inspection photo upload.
(function () {
    "use strict";

    var STATUS_URL = "/tasks/status/";
    var DOWNLOAD_URL = "/tasks/download?path=";
    var POLL_INTERVAL_MS = 3000;
    var MAX_POLLS = 40; // ~2 minutes

    function taskStatusUrl(taskId) {
        return STATUS_URL + encodeURIComponent(taskId);
    }

    function downloadUrl(filePath) {
        return DOWNLOAD_URL + encodeURIComponent(filePath);
    }

    // Submit a form via fetch(), then poll the returned task_id until
    // completed/error. Calls onDone({ status, result, error, errors }).
    // `errors` (when present) is a structured {field: message} map from the
    // server used by forms to highlight invalid inputs inline.
    function submitAndPoll(form, onDone, opts) {
        opts = opts || {};
        var formData = new FormData(form);
        fetch(form.action, { method: "POST", body: formData })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    if (!resp.ok) {
                        var err = new Error(data.error || "Request failed (" + resp.status + ")");
                        err.status = resp.status;
                        err.errors = data.errors || null;
                        throw err;
                    }
                    return data;
                });
            })
            .then(function (data) {
                // Synchronous path: result is inline (no task_id).
                if (!data.task_id) {
                    onDone({ status: "completed", result: data });
                    return;
                }
                // Async (QStash): poll the status store until done.
                pollStatus(data.task_id, onDone, opts);
            })
            .catch(function (err) {
                onDone({
                    status: "error",
                    error: err.message,
                    errors: err.errors || null,
                    result: null,
                });
            });
    }

    // Poll a task's status endpoint until completed/error (no form submit).
    // Single canonical polling loop shared by submitAndPoll and direct callers.
    function pollStatus(taskId, onDone, opts) {
        opts = opts || {};
        var interval = opts.interval || POLL_INTERVAL_MS;
        var maxPolls = opts.maxPolls || MAX_POLLS;
        var attempts = 0;
        var timer = setInterval(function () {
            attempts += 1;
            fetch(taskStatusUrl(taskId))
                .then(function (resp) {
                    if (resp.status === 404) return null;
                    return resp.json();
                })
                .then(function (record) {
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
                .catch(function (err) {
                    if (attempts >= maxPolls) {
                        clearInterval(timer);
                        onDone({ status: "error", error: err.message, result: null });
                    }
                });
        }, interval);
    }

    // Build a download link markup for a completed task result.
    function downloadLink(result, label) {
        var filePath = result && result.file_path;
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

    window.TaskPoll = {
        submitAndPoll: submitAndPoll,
        pollStatus: pollStatus,
        taskStatusUrl: taskStatusUrl,
        downloadUrl: downloadUrl,
        downloadLink: downloadLink,
    };
})();
