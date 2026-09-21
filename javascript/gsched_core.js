/**
 * Pure helpers for the Queue tab: formatting, HTML rendering, and action → request mapping.
 * Loaded as a plain script in Forge (sets globalThis.GschedCore) and via require() in Node tests.
 * No DOM access here — everything takes plain data and returns strings / plain objects.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    root.GschedCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    const API = "/gsched/v1";
    const MAX_THUMBS = 4;

    const STATUS_LABELS = {
        pending: "Queued",
        running: "Running",
        done: "Done",
        failed: "Failed",
        interrupted: "Interrupted",
    };

    // -- formatting ----------------------------------------------------

    function escapeHtml(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatDuration(seconds) {
        const total = Number(seconds);
        if (!Number.isFinite(total) || total < 0) return "";
        if (total < 60) return `${total.toFixed(1)}s`;
        const whole = Math.floor(total);
        const h = Math.floor(whole / 3600);
        const m = Math.floor((whole % 3600) / 60);
        const s = whole % 60;
        const pad = (n) => String(n).padStart(2, "0");
        return h > 0 ? `${h}h ${pad(m)}m` : `${m}m ${pad(s)}s`;
    }

    function formatAgo(now, timestamp) {
        if (!Number.isFinite(now) || !Number.isFinite(timestamp)) return "";
        const delta = Math.max(0, now - timestamp);
        if (delta < 5) return "just now";
        if (delta < 60) return `${Math.floor(delta)}s ago`;
        if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
        if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
        return `${Math.floor(delta / 86400)}d ago`;
    }

    function jobTitle(job) {
        const prompt = String((job.summary && job.summary.prompt) || "")
            .replace(/\s+/g, " ")
            .trim();
        return prompt || "(no prompt)";
    }

    function jobDetails(job) {
        const s = job.summary || {};
        const details = [];
        details.push(s.tab || job.kind);
        if (job.context && job.context.model) details.push(job.context.model);
        if (s.width && s.height) details.push(`${s.width}×${s.height}`);
        if (s.steps) details.push(`${s.steps} steps`);
        if (s.sampler) details.push(s.scheduler ? `${s.sampler} / ${s.scheduler}` : s.sampler);
        if (s.cfg_scale !== undefined) details.push(`CFG ${s.cfg_scale}`);
        if (s.denoising_strength !== undefined && s.tab === "img2img") {
            details.push(`denoise ${s.denoising_strength}`);
        }
        if (s.hires === true) details.push("hires");
        if (s.seed !== undefined && s.seed !== "") {
            details.push(Number(s.seed) === -1 ? "random seed" : `seed ${s.seed}`);
        }
        const count = Number(s.batch_count || 1);
        const size = Number(s.batch_size || 1);
        if (count > 1 || size > 1) details.push(`${count}×${size} batch`);
        return details;
    }

    /** 0..1 progress of the running job, or null when unknown. */
    function progressFraction(progress) {
        if (!progress) return null;
        const count = Number(progress.job_count) || 0;
        const steps = Number(progress.steps) || 0;
        const step = Number(progress.step) || 0;
        const within = steps > 0 ? Math.min(step / steps, 1) : 0;
        if (count > 0) {
            const done = Math.min(Number(progress.job_no) || 0, count);
            return Math.min((done + within) / count, 1);
        }
        return steps > 0 ? within : null;
    }

    function progressText(progress) {
        if (!progress) return "starting…";
        const parts = [];
        if (Number(progress.job_count) > 1) {
            parts.push(`batch ${Math.min((Number(progress.job_no) || 0) + 1, progress.job_count)}/${progress.job_count}`);
        }
        if (Number(progress.steps) > 0) parts.push(`step ${progress.step || 0}/${progress.steps}`);
        return parts.join(" · ") || "starting…";
    }

    function tabLabel(state) {
        if (!state) return "Queue";
        const running = state.running ? 1 : 0;
        const pending = (state.pending || []).length;
        const total = running + pending;
        return total > 0 ? `Queue (${total})` : "Queue";
    }

    // -- live preview & shortcut -----------------------------------------

    /**
     * Should this keydown be turned into a Queue press?
     *
     * Ctrl/Cmd + Enter is Forge's Generate shortcut (interrupt-and-restart while running). With
     * the override on it queues instead — an idle, unpaused queue starts the job right away, so
     * it still "generates". Alt+Enter (Skip) and Esc (Interrupt) are not touched.
     */
    function isQueueShortcut(event, enabled) {
        return (
            !!enabled &&
            event.key === "Enter" &&
            (event.ctrlKey || event.metaKey) &&
            !event.altKey
        );
    }

    /**
     * Decide whether the page should start showing the running queue job's live preview.
     *
     * state:   the /state payload
     * current: { attachedId: id of the job already being shown (or null),
     *            busyTabs: { txt2img: bool, img2img: bool } — a manual Generate is running there }
     * Returns { attach: { tab, taskId } | null, clear: bool }. `clear` means forget the previous
     * attachment (its progress bar removes itself when its task ends).
     */
    function previewPlan(state, current) {
        const attachedId = (current && current.attachedId) || null;
        const busyTabs = (current && current.busyTabs) || {};
        const job = state && state.running;
        const taskId = job && state.progress && state.progress.task_id;

        if (!job || !taskId) return { attach: null, clear: attachedId !== null };
        if (attachedId === taskId) return { attach: null, clear: false };
        // Don't fight a manual generation for the same gallery.
        if (busyTabs[job.kind]) return { attach: null, clear: false };
        return { attach: { tab: job.kind, taskId }, clear: false };
    }

    // -- actions -------------------------------------------------------

    const CLEAR_CONFIRM = {
        pending: "Remove every queued job that has not started?",
        finished: "Remove all finished jobs from the history? Generated images are not deleted.",
    };

    /**
     * Map a UI action to an HTTP request. Returns null for unknown actions.
     * `confirm` (when present) is a question to ask before sending.
     */
    function buildRequest(action, jobId) {
        const job = `${API}/jobs/${encodeURIComponent(jobId)}`;
        switch (action) {
            case "pause":
                return { method: "POST", path: `${API}/queue/pause` };
            case "resume":
                return { method: "POST", path: `${API}/queue/resume` };
            case "interrupt":
                return { method: "POST", path: `${API}/interrupt` };
            case "clear-pending":
                return {
                    method: "POST",
                    path: `${API}/queue/clear`,
                    body: { scope: "pending" },
                    confirm: CLEAR_CONFIRM.pending,
                };
            case "clear-finished":
                return {
                    method: "POST",
                    path: `${API}/queue/clear`,
                    body: { scope: "finished" },
                    confirm: CLEAR_CONFIRM.finished,
                };
            case "remove":
                return { method: "DELETE", path: job };
            case "requeue":
                return { method: "POST", path: `${job}/requeue` };
            case "move-top":
            case "move-up":
            case "move-down":
            case "move-bottom":
                return { method: "POST", path: `${job}/move`, body: { to: action.slice(5) } };
            default:
                return null;
        }
    }

    // -- rendering -----------------------------------------------------

    function button(action, label, options) {
        const opts = options || {};
        const id = opts.id !== undefined ? ` data-gsched-id="${escapeHtml(opts.id)}"` : "";
        const disabled = opts.disabled ? " disabled" : "";
        const cls = ["gsched-btn"].concat(opts.classes || []).join(" ");
        const title = opts.title ? ` title="${escapeHtml(opts.title)}"` : "";
        return `<button type="button" class="${cls}" data-gsched-action="${action}"${id}${title}${disabled}>${label}</button>`;
    }

    function detailsHtml(job) {
        return jobDetails(job)
            .map((d) => `<span class="gsched-chip">${escapeHtml(d)}</span>`)
            .join("");
    }

    function renderToolbar(state) {
        const pending = (state.pending || []).length;
        const running = state.running;
        let status;
        if (state.paused) {
            status = running ? "Paused — the current job will finish first" : "Paused";
        } else if (running) {
            status = "Running";
        } else {
            status = pending ? "Starting…" : "Idle";
        }
        const toggle = state.paused
            ? button("resume", "▶ Start queue", {
                  classes: ["gsched-primary"],
                  title: "Run the queued jobs one after another",
              })
            : button("pause", "⏸ Pause queue", {
                  title: "Let the current job finish, then hold the rest",
              });
        return (
            `<div class="gsched-toolbar">` +
            `<div class="gsched-status gsched-status-${state.paused ? "paused" : running ? "running" : "idle"}">` +
            `<strong>${escapeHtml(status)}</strong> · ${pending} waiting</div>` +
            `<div class="gsched-actions">${toggle}` +
            button("interrupt", "⏹ Interrupt current", {
                disabled: !running,
                title: "Stop the running job now",
            }) +
            button("clear-pending", "Clear queued", { disabled: pending === 0 }) +
            button("clear-finished", "Clear history", {
                disabled: (state.finished || []).length === 0,
            }) +
            `</div></div>`
        );
    }

    function renderRunning(state) {
        const job = state.running;
        if (!job) return "";
        const fraction = progressFraction(state.progress);
        const width = fraction === null ? 0 : Math.round(fraction * 100);
        const elapsed = job.started_at ? formatDuration(state.now - job.started_at) : "";
        return (
            `<section class="gsched-section"><h3>Now running</h3>` +
            `<div class="gsched-card gsched-running" data-gsched-job="${job.id}">` +
            `<div class="gsched-title" title="${escapeHtml(jobTitle(job))}">${escapeHtml(jobTitle(job))}</div>` +
            `<div class="gsched-chips">${detailsHtml(job)}</div>` +
            `<div class="gsched-progress"><div class="gsched-progress-bar${fraction === null ? " gsched-indeterminate" : ""}" style="width:${width}%"></div></div>` +
            `<div class="gsched-meta">${escapeHtml(progressText(state.progress))}${elapsed ? ` · ${escapeHtml(elapsed)}` : ""}</div>` +
            `</div></section>`
        );
    }

    function renderPending(state) {
        const jobs = state.pending || [];
        let body;
        if (jobs.length === 0) {
            body = `<p class="gsched-empty">Nothing queued. Press <strong>Queue</strong> next to Generate to add the current settings.</p>`;
        } else {
            body = jobs
                .map((job, index) => {
                    const first = index === 0;
                    const last = index === jobs.length - 1;
                    return (
                        `<div class="gsched-row" data-gsched-job="${job.id}">` +
                        `<div class="gsched-pos">${index + 1}</div>` +
                        `<div class="gsched-main"><div class="gsched-title" title="${escapeHtml(jobTitle(job))}">${escapeHtml(jobTitle(job))}</div>` +
                        `<div class="gsched-chips">${detailsHtml(job)}</div></div>` +
                        `<div class="gsched-when">${escapeHtml(formatAgo(state.now, job.created_at))}</div>` +
                        `<div class="gsched-row-actions">` +
                        button("move-top", "⤒", { id: job.id, disabled: first, title: "Run next" }) +
                        button("move-up", "▲", { id: job.id, disabled: first, title: "Move up" }) +
                        button("move-down", "▼", { id: job.id, disabled: last, title: "Move down" }) +
                        button("move-bottom", "⤓", { id: job.id, disabled: last, title: "Run last" }) +
                        button("remove", "✕", { id: job.id, classes: ["gsched-danger"], title: "Remove from queue" }) +
                        `</div></div>`
                    );
                })
                .join("");
        }
        return `<section class="gsched-section"><h3>Up next <span class="gsched-count">${jobs.length}</span></h3>${body}</section>`;
    }

    function renderThumbs(job) {
        const outputs = (job.result && job.result.outputs) || [];
        return outputs
            .slice(0, MAX_THUMBS)
            .map(
                (_path, i) =>
                    `<a href="${API}/jobs/${job.id}/outputs/${i}" target="_blank" rel="noopener">` +
                    `<img class="gsched-thumb" loading="lazy" alt="Output ${i + 1}" src="${API}/jobs/${job.id}/outputs/${i}/thumbnail"></a>`,
            )
            .join("");
    }

    function renderFinished(state) {
        const jobs = state.finished || [];
        if (jobs.length === 0) return "";
        const rows = jobs
            .map((job) => {
                const elapsed = job.result && job.result.elapsed ? formatDuration(job.result.elapsed) : "";
                const thumbs = renderThumbs(job);
                const error = job.error ? `<div class="gsched-error">${escapeHtml(job.error)}</div>` : "";
                return (
                    `<div class="gsched-row gsched-finished" data-gsched-job="${job.id}">` +
                    `<div class="gsched-badge gsched-badge-${escapeHtml(job.status)}">${escapeHtml(STATUS_LABELS[job.status] || job.status)}</div>` +
                    `<div class="gsched-main"><div class="gsched-title" title="${escapeHtml(jobTitle(job))}">${escapeHtml(jobTitle(job))}</div>` +
                    `<div class="gsched-chips">${detailsHtml(job)}</div>${error}` +
                    (thumbs ? `<div class="gsched-thumbs">${thumbs}</div>` : "") +
                    `</div>` +
                    `<div class="gsched-when">${escapeHtml(elapsed || formatAgo(state.now, job.finished_at))}</div>` +
                    `<div class="gsched-row-actions">` +
                    button("requeue", "↻ Requeue", { id: job.id, title: "Add a copy to the end of the queue" }) +
                    button("remove", "✕", { id: job.id, classes: ["gsched-danger"], title: "Remove from history" }) +
                    `</div></div>`
                );
            })
            .join("");
        return `<section class="gsched-section"><h3>History <span class="gsched-count">${jobs.length}</span></h3>${rows}</section>`;
    }

    function renderState(state) {
        if (!state) return `<p class="gsched-loading">Loading queue…</p>`;
        return renderToolbar(state) + renderRunning(state) + renderPending(state) + renderFinished(state);
    }

    return {
        API,
        MAX_THUMBS,
        STATUS_LABELS,
        escapeHtml,
        formatDuration,
        formatAgo,
        jobTitle,
        jobDetails,
        progressFraction,
        progressText,
        tabLabel,
        isQueueShortcut,
        previewPlan,
        buildRequest,
        renderState,
    };
});
