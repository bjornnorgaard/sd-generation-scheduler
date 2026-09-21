/**
 * Node tests for javascript/gsched_core.js.
 * Run: node tests/js/test_gsched_core.mjs
 */
import { createRequire } from "module";
import { fileURLToPath } from "url";
import path from "path";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const Core = require(path.resolve(__dirname, "../../javascript/gsched_core.js"));

let failed = 0;
let count = 0;

function assertEqual(actual, expected, msg) {
    count += 1;
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a !== e) {
        console.error(`FAIL: ${msg}\n  expected: ${e}\n  actual:   ${a}`);
        failed += 1;
    }
}

function assert(cond, msg) {
    count += 1;
    if (!cond) {
        console.error(`FAIL: ${msg}`);
        failed += 1;
    }
}

const has = (html, needle, msg) => assert(html.includes(needle), `${msg} — missing: ${needle}`);
const hasNot = (html, needle, msg) => assert(!html.includes(needle), `${msg} — unexpected: ${needle}`);

// escapeHtml
assertEqual(Core.escapeHtml(`<img src=x onerror="a('b')">&`), "&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;", "escapeHtml");
assertEqual(Core.escapeHtml(null), "", "escapeHtml null");
assertEqual(Core.escapeHtml(0), "0", "escapeHtml zero");

// formatDuration
assertEqual(Core.formatDuration(0.84), "0.8s", "duration sub-second");
assertEqual(Core.formatDuration(59.94), "59.9s", "duration under a minute");
assertEqual(Core.formatDuration(65), "1m 05s", "duration minutes");
assertEqual(Core.formatDuration(3725), "1h 02m", "duration hours");
assertEqual(Core.formatDuration(-1), "", "duration negative");
assertEqual(Core.formatDuration(NaN), "", "duration NaN");

// formatAgo
assertEqual(Core.formatAgo(100, 98), "just now", "ago just now");
assertEqual(Core.formatAgo(100, 70), "30s ago", "ago seconds");
assertEqual(Core.formatAgo(1000, 700), "5m ago", "ago minutes");
assertEqual(Core.formatAgo(20000, 1000), "5h ago", "ago hours");
assertEqual(Core.formatAgo(300000, 1000), "3d ago", "ago days");
assertEqual(Core.formatAgo(100, 200), "just now", "ago future clamps");
assertEqual(Core.formatAgo(100, undefined), "", "ago missing");

// jobTitle
assertEqual(Core.jobTitle({ summary: { prompt: "  a   cat\n on a mat " } }), "a cat on a mat", "title collapses whitespace");
assertEqual(Core.jobTitle({ summary: {} }), "(no prompt)", "title empty");
assertEqual(Core.jobTitle({}), "(no prompt)", "title no summary");

// splitMiddle / truncateMiddle
assertEqual(Core.splitMiddle("short prompt", 10), { head: "short prompt", tail: "", hidden: 0 }, "short prompt untouched");
assertEqual(Core.splitMiddle("", 5), { head: "", tail: "", hidden: 0 }, "empty prompt");
assertEqual(Core.splitMiddle(null, 5), { head: "", tail: "", hidden: 0 }, "null prompt");
const long = "AAAAA" + "m".repeat(100) + "ZZZZZ";
assertEqual(Core.splitMiddle(long, 5), { head: "AAAAA", tail: "ZZZZZ", hidden: 100 }, "start and end kept, middle counted");
assertEqual(Core.truncateMiddle(long, 5), "AAAAA … ZZZZZ", "truncateMiddle joins with an ellipsis");
assertEqual(Core.splitMiddle("x".repeat(13), 5).hidden, 0, "not truncated when it would hide almost nothing (<= 2*edge+3)");
assertEqual(Core.splitMiddle("x".repeat(14), 5).hidden, 4, "truncated just past the threshold");
assertEqual(Core.splitMiddle("  a   b\n\nc  ".repeat(1), 50).head, "a b c", "whitespace collapses before measuring");
assertEqual(Core.splitMiddle("hello world ".repeat(10) + "END", 10).tail, "world END", "tail is trimmed at its left edge");
assertEqual(Core.splitMiddle("😀".repeat(30), 5), { head: "😀".repeat(5), tail: "😀".repeat(5), hidden: 20 }, "counts characters, not UTF-16 halves");

// cuts land on word boundaries when a space is nearby
const sentence = "masterpiece, best quality, highly detailed, sharp focus, cinematic lighting, 8k uhd, dslr, soft film grain, professional color grading, volumetric fog, intricate background details, a red fox curled up in fresh snow at dawn";
for (let edge = 12; edge <= 90; edge += 3) {
    const r = Core.splitMiddle(sentence, edge);
    if (!r.hidden) continue;
    const ok =
        sentence.startsWith(r.head) &&
        sentence.endsWith(r.tail) &&
        sentence[r.head.length] === " " &&
        sentence[sentence.length - r.tail.length - 1] === " " &&
        r.hidden === sentence.length - r.head.length - r.tail.length;
    assert(ok, `edge ${edge}: head "${r.head.slice(-12)}" / tail "${r.tail.slice(0, 12)}" cut on whole words with an exact hidden count`);
}
assertEqual(Core.truncateMiddle(sentence, 30), "masterpiece, best quality, … up in fresh snow at dawn", "worked example");
assertEqual(Core.splitMiddle("x".repeat(200), 10).head.length, 10, "no spaces to snap to: cut at the exact edge");

// jobDetails
assertEqual(
    Core.jobDetails({
        kind: "txt2img",
        summary: { tab: "txt2img", width: 832, height: 1216, steps: 24, sampler: "Euler", scheduler: "Simple", cfg_scale: 6, seed: -1, batch_count: 2, batch_size: 1, hires: true },
        context: { model: "anime.safetensors" },
    }),
    ["txt2img", "anime.safetensors", "832×1216", "24 steps", "Euler / Simple", "CFG 6", "hires", "random seed", "2×1 batch"],
    "details full",
);
assertEqual(Core.jobDetails({ kind: "img2img", summary: {} }), ["img2img"], "details minimal falls back to kind");
assertEqual(
    Core.jobDetails({ kind: "img2img", summary: { tab: "img2img", denoising_strength: 0.45, seed: 77 } }),
    ["img2img", "denoise 0.45", "seed 77"],
    "details img2img",
);
assertEqual(Core.jobDetails({ kind: "txt2img", summary: { tab: "txt2img", hires: false, seed: 0 } }), ["txt2img", "seed 0"], "details hires off / seed zero");

// progress
assertEqual(Core.progressFraction(null), null, "progress none");
assertEqual(Core.progressFraction({ step: 10, steps: 20, job_no: 0, job_count: 1 }), 0.5, "progress single batch");
assertEqual(Core.progressFraction({ step: 10, steps: 20, job_no: 1, job_count: 2 }), 0.75, "progress second of two batches");
assertEqual(Core.progressFraction({ step: 0, steps: 0, job_no: 0, job_count: 1 }), 0, "progress before sampling");
assertEqual(Core.progressFraction({ step: 30, steps: 20, job_no: 0, job_count: 1 }), 1, "progress clamps");
assertEqual(Core.progressFraction({ step: 5, steps: 10, job_no: 0, job_count: 0 }), 0.5, "progress without job_count");
assertEqual(Core.progressFraction({ step: 0, steps: 0, job_no: 0, job_count: 0 }), null, "progress unknown");
assertEqual(Core.progressText({ step: 5, steps: 20, job_no: 1, job_count: 3 }), "batch 2/3 · step 5/20", "progress text");
assertEqual(Core.progressText({ step: 5, steps: 20, job_no: 0, job_count: 1 }), "step 5/20", "progress text single");
assertEqual(Core.progressText(null), "starting…", "progress text none");

// tabLabel
assertEqual(Core.tabLabel(null), "Queue", "tab label none");
assertEqual(Core.tabLabel({ running: null, pending: [] }), "Queue", "tab label empty");
assertEqual(Core.tabLabel({ running: { id: 1 }, pending: [{}, {}] }), "Queue (3)", "tab label counts running + pending");

// isQueueShortcut
const key = (o) => Object.assign({ key: "Enter", ctrlKey: true, metaKey: false, shiftKey: false, altKey: false }, o);
assert(Core.isQueueShortcut(key(), true), "ctrl+enter queues");
assert(Core.isQueueShortcut(key({ ctrlKey: false, metaKey: true }), true), "cmd+enter queues");
assert(Core.isQueueShortcut(key({ shiftKey: true }), true), "shift does not matter (Forge ignores it too)");
assert(!Core.isQueueShortcut(key(), false), "override switched off: Forge's default applies");
assert(!Core.isQueueShortcut(key(), undefined), "no setting value: off");
assert(!Core.isQueueShortcut(key({ ctrlKey: false }), true), "plain enter is untouched");
assert(!Core.isQueueShortcut(key({ altKey: true }), true), "alt+enter stays Skip");
assert(!Core.isQueueShortcut(key({ key: "Escape", ctrlKey: false }), true), "Esc stays Interrupt");
assert(!Core.isQueueShortcut(key({ key: "a" }), true), "other keys are untouched");

// makeToken / takeStateEntry
const tok = Core.makeToken();
assert(tok.startsWith("gsched-") && tok.length > 12 && tok.length <= 64, "token shape");
assert(Core.makeToken() !== tok, "tokens differ");
assertEqual(Core.makeToken(() => 0.5), "gsched-ii", "token is built from the random source (0.5 -> \"i\" in base 36)");
const fifo = [{ tab: "txt2img", taskId: "a" }, { tab: "img2img", taskId: "b" }, { tab: "txt2img", taskId: "c" }];
assertEqual(Core.takeStateEntry(fifo, "txt2img").taskId, "a", "oldest entry of the tab first");
assertEqual(Core.takeStateEntry(fifo, "txt2img").taskId, "c", "then the next one, skipping other tabs");
assertEqual(Core.takeStateEntry(fifo, "txt2img"), null, "none left for the tab");
assertEqual(fifo.map((e) => e.taskId), ["b"], "other tabs' entries stay put");

// previewPlan
const running = (id, kind, extra) => ({ running: { id, kind }, progress: Object.assign({ task_id: `task(${kind}-${id})`, step: 1, steps: 8 }, extra) });
const idle = { attachedId: null, busyTabs: { txt2img: false, img2img: false } };
assertEqual(Core.previewPlan(running(1, "txt2img"), idle), { attach: { tab: "txt2img", taskId: "task(txt2img-1)", token: null }, clear: false }, "attach to running txt2img job");
assertEqual(
    Core.previewPlan({ running: { id: 1, kind: "txt2img", summary: { client_token: "gsched-xyz" } }, progress: { task_id: "task(txt2img-1)" } }, idle).attach.token,
    "gsched-xyz",
    "plan carries the job's client token",
);
assertEqual(Core.previewPlan(running(2, "img2img"), idle).attach.tab, "img2img", "attach to running img2img job in its own tab");
assertEqual(Core.previewPlan(running(1, "txt2img"), { ...idle, attachedId: "task(txt2img-1)" }), { attach: null, clear: false }, "already attached: nothing to do");
assertEqual(Core.previewPlan(running(2, "txt2img"), { ...idle, attachedId: "task(txt2img-1)" }).attach.taskId, "task(txt2img-2)", "next job attaches even if the previous one was attached");
assertEqual(Core.previewPlan(running(1, "txt2img"), { ...idle, busyTabs: { txt2img: true, img2img: false } }), { attach: null, clear: false }, "manual generate in that tab: leave it alone");
assertEqual(Core.previewPlan(running(1, "txt2img"), { ...idle, busyTabs: { txt2img: false, img2img: true } }).attach.tab, "txt2img", "manual generate in the other tab does not matter");
assertEqual(Core.previewPlan({ running: { id: 1, kind: "txt2img" }, progress: null }, idle), { attach: null, clear: false }, "task id not known yet: wait");
assertEqual(Core.previewPlan({ running: { id: 1, kind: "txt2img" }, progress: { step: 0 } }, idle), { attach: null, clear: false }, "progress without task id: wait");
assertEqual(Core.previewPlan({ running: null, progress: null }, { ...idle, attachedId: "task(txt2img-1)" }), { attach: null, clear: true }, "job finished: forget attachment");
assertEqual(Core.previewPlan({ running: null }, idle), { attach: null, clear: false }, "idle: nothing");
assertEqual(Core.previewPlan(null, idle), { attach: null, clear: false }, "no state: nothing");

// buildRequest
assertEqual(Core.buildRequest("pause"), { method: "POST", path: "/gsched/v1/queue/pause" }, "req pause");
assertEqual(Core.buildRequest("resume"), { method: "POST", path: "/gsched/v1/queue/resume" }, "req resume");
assertEqual(Core.buildRequest("interrupt"), { method: "POST", path: "/gsched/v1/interrupt" }, "req interrupt");
assertEqual(Core.buildRequest("remove", "7"), { method: "DELETE", path: "/gsched/v1/jobs/7" }, "req remove");
assertEqual(Core.buildRequest("requeue", "7"), { method: "POST", path: "/gsched/v1/jobs/7/requeue" }, "req requeue");
assertEqual(Core.buildRequest("move-top", "7").body, { to: "top" }, "req move top");
assertEqual(Core.buildRequest("move-bottom", "7").body, { to: "bottom" }, "req move bottom");
assertEqual(Core.buildRequest("move-up", "7").path, "/gsched/v1/jobs/7/move", "req move path");
assertEqual(Core.buildRequest("clear-pending").body, { scope: "pending" }, "req clear pending");
assert(typeof Core.buildRequest("clear-pending").confirm === "string", "clear pending asks to confirm");
assertEqual(Core.buildRequest("clear-finished").body, { scope: "finished" }, "req clear finished");
assert(typeof Core.buildRequest("clear-finished").confirm === "string", "clear finished asks to confirm");
assertEqual(Core.buildRequest("remove", "../x").path, "/gsched/v1/jobs/..%2Fx", "job id is URL-encoded");
assertEqual(Core.buildRequest("explode", "1"), null, "unknown action");
assert(Core.buildRequest("remove", "1").confirm === undefined, "remove does not nag");

// renderState
const job = (id, extra) => Object.assign({ id, kind: "txt2img", status: "pending", summary: { tab: "txt2img", prompt: `prompt ${id}` }, context: {}, created_at: 90, ...extra });
const base = { paused: false, busy: false, running: null, progress: null, pending: [], finished: [], counts: {}, now: 100 };

assertEqual(Core.renderState(null).includes("Loading"), true, "render null state");

let html = Core.renderState(base);
has(html, "Idle", "idle status");
has(html, "Nothing queued", "empty queue message");
has(html, 'data-gsched-action="pause"', "pause button when running");
hasNot(html, 'data-gsched-action="resume"', "no resume button when running");
has(html, 'data-gsched-action="interrupt" title="Stop the running job now" disabled', "interrupt disabled when idle");
has(html, 'data-gsched-action="clear-pending" disabled', "clear queued disabled when empty");
hasNot(html, "History", "no history section when empty");
hasNot(html, "Now running", "no running section when idle");

html = Core.renderState({ ...base, paused: true, pending: [job(1), job(2), job(3)] });
has(html, "Paused", "paused status");
has(html, 'data-gsched-action="resume"', "start button when paused");
hasNot(html, 'data-gsched-action="pause"', "no pause button when paused");
has(html, "3 waiting", "waiting count");
has(html, "prompt 1", "first job shown");
has(html, 'data-gsched-action="move-up" data-gsched-id="1" title="Move up" disabled', "first job cannot move up");
has(html, 'data-gsched-action="move-down" data-gsched-id="1" title="Move down">', "first job can move down");
has(html, 'data-gsched-action="move-down" data-gsched-id="3" title="Move down" disabled', "last job cannot move down");
has(html, 'data-gsched-action="remove" data-gsched-id="2"', "remove button per job");
has(html, "10s ago", "created age shown");

html = Core.renderState({ ...base, paused: true, running: job(9, { status: "running", started_at: 80 }) });
has(html, "the current job will finish first", "paused while running hint");

html = Core.renderState({
    ...base,
    running: job(5, { status: "running", started_at: 70, context: { model: "m.safetensors" } }),
    progress: { step: 5, steps: 20, job_no: 0, job_count: 2 },
    pending: [job(6)],
});
has(html, "Now running", "running section");
has(html, "width:13%", "progress bar width"); // (0 + 5/20) / 2 = 0.125 → 13%
has(html, "batch 1/2 · step 5/20 · 30.0s", "progress meta + elapsed");
has(html, "m.safetensors", "model chip");
has(html, 'data-gsched-action="interrupt" title="Stop the running job now">', "interrupt enabled while running");
hasNot(html, "Idle", "not idle");

html = Core.renderState({ ...base, running: job(5, { status: "running", started_at: 70 }), progress: null });
has(html, "gsched-indeterminate", "indeterminate bar before progress is known");

html = Core.renderState({
    ...base,
    finished: [
        job(4, { status: "done", finished_at: 95, result: { outputs: ["a.png", "b.png", "c.png", "d.png", "e.png"], elapsed: 12.3 } }),
        job(3, { status: "failed", error: "OutOfMemoryError: <script>alert(1)</script>", finished_at: 50 }),
        job(2, { status: "interrupted", finished_at: 10, error: "WebUI stopped while this job was running." }),
    ],
});
has(html, "History", "history section");
has(html, "gsched-badge-done", "done badge");
has(html, "Failed", "failed label");
has(html, "Interrupted", "interrupted label");
has(html, "12.3s", "elapsed shown for finished job");
has(html, "50s ago", "age fallback when no elapsed");
has(html, "/gsched/v1/jobs/4/outputs/0/thumbnail", "thumbnail url");
has(html, 'href="/gsched/v1/jobs/4/outputs/3"', "full image link");
hasNot(html, "/gsched/v1/jobs/4/outputs/4/thumbnail", "thumbnails capped");
assertEqual((html.match(/class="gsched-thumb"/g) || []).length, Core.MAX_THUMBS, "thumb count capped");
hasNot(html, "<script>alert(1)</script>", "error text is escaped");
has(html, "&lt;script&gt;", "escaped error visible");
has(html, 'data-gsched-action="requeue" data-gsched-id="3"', "requeue per finished job");
has(html, 'data-gsched-action="clear-finished">', "clear history enabled with history");

has(html, 'class="gsched-history"', "history grid container");
assertEqual((html.match(/class="gsched-card gsched-finished"/g) || []).length, 3, "one card per finished job");
const firstCard = html.slice(html.indexOf('data-gsched-job="4"'), html.indexOf('data-gsched-job="3"'));
assert(firstCard.indexOf("gsched-thumbs") !== -1 && firstCard.indexOf("gsched-thumbs") < firstCard.indexOf("gsched-title"), "thumbnails come before the prompt in a card");
assert(firstCard.indexOf("gsched-badge-done") < firstCard.indexOf("gsched-title"), "status badge sits in the card head");
const noImageCard = html.slice(html.indexOf('data-gsched-job="3"'), html.indexOf('data-gsched-job="2"'));
hasNot(noImageCard, "gsched-thumbs", "a card without outputs has no image area");
hasNot(Core.renderState({ ...base, pending: [job(1)] }), "gsched-history", "no history grid when there is no history");
hasNot(Core.renderState({ ...base, pending: [job(1)] }), "gsched-card gsched-finished", "pending jobs are not history cards");

html = Core.renderState({ ...base, pending: [job(1, { summary: { tab: "txt2img", prompt: `<b onclick="x">hi</b>` } })] });
hasNot(html, "<b onclick", "prompt is escaped");
has(html, "&lt;b onclick=&quot;x&quot;&gt;hi&lt;/b&gt;", "escaped prompt shown");

console.log(failed === 0 ? `ok — ${count} assertions` : `${failed} of ${count} assertions failed`);
process.exit(failed === 0 ? 0 : 1);
