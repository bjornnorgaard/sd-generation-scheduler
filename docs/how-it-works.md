# How it works

```
 Queue click ──► Gradio event (same inputs as Generate) ──► Scheduler.enqueue
                                                              │  codec: args → JSON + side files
                                                              ▼
                                                        SQLite (jobs, meta)
                                                              ▲
 Queue tab (JS) ◄── /gsched/v1/* (FastAPI) ──► Scheduler ─────┤
                                                              ▼
                                        Runner thread ──► ForgeExecutor ──► txt2img / img2img
```

## Getting Generate's arguments

Forge builds each tab as its own `gr.Blocks` and wires Generate only once every input exists. `wiring.py`:

1. hooks `on_after_component`; when Generate is created it adds a **Queue** `gr.Button` in the same row and remembers the tab's `Blocks`;
2. from the `on_ui_tabs` callback (tabs complete, not yet rendered into the app) it finds Generate's click handler — the one that fills the tab's gallery, because extensions such as ControlNet bind their own handlers to the same click — and registers Queue's click on that `Blocks` with **the same `inputs`**.

So Queue receives exactly what Generate would, including arguments from extensions, with no knowledge of them. The click runs through Gradio's queue (so `gr.Info` toasts work) without a concurrency limit (so it never waits behind a running generation). `js=` returns the arguments unchanged.

## Storing a job

`codec.py` turns the argument list into JSON. PIL images become PNG files, NumPy arrays `.npy`, uploads are copied — all under `data/inputs/<uuid>/` and referenced by tagged objects. Tuples, dataclasses (ControlNet's `ControlNetUnit`) and Enums round-trip by class path; on restore only dataclasses and Enums are re-created, by importing the named class and assigning fields — no constructor or other code from the stored data runs. Values it cannot represent raise `UnserializableArgument`, which the Queue button reports.

`store.py` keeps jobs in SQLite (`queue.sqlite3`): status (`pending → running → done | failed | interrupted`), a float `position` for ordering (reorder = swap / min-1 / max+1), the argument JSON, a small `summary` for the UI, the model `context`, result paths, error text. Claiming the next job is one `BEGIN IMMEDIATE` transaction. A `meta` table stores the paused flag.

The `summary` (prompt, size, steps, sampler, seed…) is resolved by component `elem_id` from the Generate inputs when the tab is wired, not by argument position, so it survives Forge changing the argument order.

## Running a job

`runner.py` is one daemon thread: wait until not paused and something is pending → claim → decode arguments → `executor.run` → store the outcome → prune old history. A crash inside a job marks it *Failed* and the loop continues.

`executor.py` is the only module that touches WebUI internals (imported lazily):

- calls `modules.txt2img.txt2img` / `modules.img2img.img2img` through `call_queue.wrap_gradio_gpu_call`, so it shares the WebUI queue lock, progress tracking and cleanup with manual Generate;
- replaces the first argument (the task-id slot) with a real `task(...)` id;
- around the call, applies the job's model `context` with `main_entry.checkpoint_change / modules_change / dtype_change` (the quick-settings callbacks) plus any configured extra options, and restores the previous values afterwards — only when they actually differ, so an unchanged model is never reloaded;
- reads `shared.state.interrupted` before the wrapper's cleanup resets it, to tell *Interrupted* from *Done*;
- extracts saved file paths (`image.already_saved_as`) and the infotext from the return value.

## HTTP API

Routes under `/gsched/v1` (registered in `on_app_started`, logic in `api.py`, FastAPI glue in `routes.py`):

| Route | |
|---|---|
| `GET /state` | running job + progress, pending, finished, counts, paused |
| `POST /queue/pause`, `/queue/resume`, `/interrupt` | |
| `POST /queue/clear` `{"scope": "pending" \| "finished" \| "all"}` | never touches the running job |
| `DELETE /jobs/{id}` · `POST /jobs/{id}/move` `{"to": "top\|up\|down\|bottom"}` · `POST /jobs/{id}/requeue` | |
| `GET /jobs/{id}/outputs/{n}[/thumbnail]` | only files recorded as that job's outputs |

All `POST` / `DELETE` routes require the header `X-Gsched: 1`.

## Live preview and Ctrl+Enter

The executor registers the job's `task(...)` id with Forge's progress tracker and reports it as `progress.task_id` in `/state`. The page (`gsched_queue.js`) sees a running job whose id it isn't showing yet and calls Forge's own `requestProgress(id, gallery_container, gallery, …)` — progress bar + live preview, driven by the global generation state — and clicks a hidden per-tab **restore** button. That button's event has Generate's outputs and its handler waits for the task to finish and returns the tuple Forge recorded for it, so the images land in the gallery. (Forge's own restore button was not reused: its output list is one shorter than the recorded result.) `previewPlan` in `gsched_core.js` decides when to attach and never does so while a manual Generate is running in that tab.

`isQueueShortcut` recognises Ctrl/Cmd+Enter (without Alt) when the `gsched_override_ctrl_enter` option is on. The keydown listener runs in the capture phase and calls `stopImmediatePropagation`, so Forge's handler never also fires, then clicks the current tab's Queue button. Held-key repeats are swallowed so they can't flood the queue.

## State Manager compatibility

State Manager records the UI state by wrapping Forge's `submit()`, and saves it whenever a new image head appears in a gallery; with nothing recorded it shows an `alert`. Queue results arrive through the restore event, not `submit()`. So (only when `window.stateManager` exists):

1. The Queue click script (`CAPTURE_JS_TEMPLATE`, async) calls `gschedNoteQueuePress(tab)`, which awaits `stateManager.getCurrentState(tab)` and returns a random token. The token travels in the otherwise unused task-id slot and is stored as `summary.client_token`. Awaiting matters: the snapshot is async and reads the controls after an internal await, so a fire-and-forget snapshot can pick up edits made after the click.
2. When a job is attached to a gallery, `{tab, token}` is pushed on a FIFO. The restore event has a `.then` (`DELIVERED_JS_TEMPLATE`) that runs right after the images land and pops the oldest entry for that tab — one attach, one restore, one delivery — and sets `stateManager.lastUsedState` to that job's snapshot just before State Manager's debounced `checkHeadImage` runs.
3. With no snapshot for the job (page reloaded, other window, snapshot failed or timed out after 3 s), `lastUsedState` is cleared (never reuse another job's) and State Manager's save is skipped once via a guard, with a `console.warn`. In every other situation its original code, alert included, still runs.

## The Queue tab

`gsched_core.js` is pure (formatting, HTML strings, action → request, and `splitMiddle` — the head/tail prompt view, snapped to word boundaries; the server stores the full prompt, capped at 20,000 characters as a sanity limit); `gsched_queue.js` polls `/state` (1 s while the tab is open or work is queued, 4 s otherwise), renders into `#gsched-root` only when the HTML changed, delegates button clicks, and updates the tab title counter. All text is HTML-escaped.
