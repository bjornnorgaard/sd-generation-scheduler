# Usage

## The Queue button

**Queue** sits next to **Generate** on both txt2img and img2img. It does not generate anything itself: it saves the current state of every control that Generate would send, adds a job to the end of the queue, and shows a toast (`Queued #7 — 2 ahead`).

- The job keeps the settings **as they were when you pressed Queue**. Change the prompt afterwards and queued jobs are unaffected.
- A seed of `-1` stays random and is resolved when the job runs, so queueing the same settings twice gives two different images.
- img2img jobs keep their input / mask images (stored as PNG files under the data directory).
- The checkpoint, VAE / text encoders and UNet dtype selected at queue time are re-applied while that job runs and restored afterwards. Extra settings (e.g. Clip skip) can be added — see [settings.md](settings.md).
- If some extension passes a value the queue cannot save, you get a clear error instead of a broken job.

While a normal Generate is running, Interrupt / Skip cover only the Generate button, so Queue stays available.

## The Queue tab

| Area | What it shows / does |
|---|---|
| **Toolbar** | State (*Idle / Running / Paused*) and count. **Start queue / Pause queue**, **Interrupt current**, **Clear queued**, **Clear history** |
| **Now running** | Prompt, settings, progress bar, batch / step, elapsed time |
| **Up next** | Waiting jobs in run order. ⤒ run next · ▲ up · ▼ down · ⤓ run last · ✕ remove |
| **History** | Done / Failed / Interrupted jobs, newest first, with error text, run time and thumbnails (click one for the full image). **Requeue** appends a copy; ✕ removes it from history |

The tab title shows the number of running + waiting jobs, e.g. **Queue (3)**.

## Pausing, starting, interrupting

- **Pause queue** — the running job finishes, then nothing new starts. Jobs you add while paused wait.
- **Start queue** — resumes. On a fresh install the queue is running, so the first Queue click starts immediately.
- **Interrupt current** — stops the running job right away. The job is marked *Interrupted* (any images already saved are kept) and, by default, the queue **pauses** so it doesn't race ahead when you meant "stop". Turn that off in settings to have the queue continue with the next job.
- Removing the *running* job is not allowed; interrupt it first.
- A **failed** job (out of memory, bad model, …) never stops the queue — it is marked *Failed* with the error and the next job starts.

## Restarts and crashes

The queue is stored on disk. After a restart:

- jobs that were waiting are still waiting;
- a job that was *running* when the WebUI stopped becomes **Interrupted** ("WebUI stopped while this job was running") and can be requeued;
- the queue starts **paused** so old jobs don't start by themselves — press **Start queue** (or enable *Resume the queue automatically when the WebUI starts*).

Reloading the browser or using *Reload UI* does not affect a running queue.

## Manual Generate and the queue

Manual Generate and queued jobs share the WebUI's own lock, so they never generate at the same time. If you press Generate while the queue is running, your generation waits its turn like any other request. Note that the WebUI's model selection is global: queued jobs switch it for their duration and switch it back, but do not change the queue while a manual Generate is mid-flight if you depend on a specific model.

## Limits

- txt2img and img2img only (no Extras / batch-from-directory queue).
- Live preview images are not shown in the Queue tab; results appear when a job finishes.
- History is capped (default 100 finished jobs); older ones are deleted along with their saved input images. Generated images are never deleted by this extension.
