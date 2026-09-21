# Agent guidance — Generation Scheduler

This repo is a Forge Neo extension. Keep it aligned with sibling `sd-dynamic-placeholders` / `sd-aspect-ratio-lock` structure and voice.

## Scope

- Target **Forge Neo** (Gradio 4.40) first. Do not assume A1111 Gradio 3 APIs exist.
- No extra runtime pip dependencies. Unit tests must run with stdlib `unittest` and no WebUI process; anything needing Gradio / FastAPI / Pillow / NumPy must `skipIf` those are missing.
- Do not edit the user's Forge install. Verify UI work with `tests/e2e/run_forge.sh`, never by restarting or overwriting their running WebUI.

## Layout

| Path | Role |
|---|---|
| `scripts/generation_scheduler.py` | WebUI entry: registers callbacks only (settings, before_ui, after_component, ui_tabs, app_started) |
| `lib_generation_scheduler/store.py` | SQLite job store (pure) |
| `lib_generation_scheduler/codec.py` | Gradio args ⇄ JSON + side files (pure; PIL / NumPy optional) |
| `lib_generation_scheduler/runner.py` | Worker thread, pause / resume (pure) |
| `lib_generation_scheduler/service.py` | `Scheduler` facade: store + runner + input files (pure) |
| `lib_generation_scheduler/executor.py` | **Only** module touching Forge internals (lazy imports) |
| `lib_generation_scheduler/wiring.py` | Queue button beside Generate + click wiring (needs `gradio`) |
| `lib_generation_scheduler/api.py` / `routes.py` | API logic (pure) / FastAPI glue (no `from __future__ import annotations`!) |
| `lib_generation_scheduler/summary.py`, `enqueue.py`, `thumbnails.py`, `settings.py`, `ui.py`, `constants.py` | Helpers |
| `javascript/gsched_core.js` | Pure rendering / formatting (shared with Node tests) |
| `javascript/gsched_queue.js` | Queue tab DOM behaviour |
| `style.css` | Scoped styles (`gsched-` classes, `*_queue` ids) |
| `tests/` | Unit tests; `tests/e2e/` browser harness |
| `docs/` | User-facing docs |

Option keys and CSS/JS ids use the `gsched_` / `gsched-` prefix; the settings section id is `generation_scheduler`; API prefix `/gsched/v1`.

## Behaviour contracts

- **Queue must receive exactly Generate's inputs.** Reuse the `inputs` of the click handler that fills `{tab}_gallery` — never "the first click handler" (ControlNet adds handlers first). See `wiring.find_generate_function`.
- The Queue click must go through Gradio's queue (`gr.Info` / `gr.Error` need an event id) with `concurrency_limit=None`. `gr.Request` is injected only as the **first positional** parameter.
- Don't use Forge-only kwargs (`tooltip=`) when creating components; set `webui_tooltip` instead so plain-Gradio tests work.
- Model selection lives in global opts, not in Generate's args: the executor applies / restores it per job via `main_entry` helpers (only when values differ). Extra opts are user-configurable.
- Unknown argument types must raise `UnserializableArgument` (surfaced to the user), never be dropped or pickled. Restore must never run constructors or code from stored data.
- Mutating API routes require `X-Gsched: 1`; output routes serve only paths recorded in a job's result.
- A failed job never stops the queue; an interrupt pauses it only if `gsched_pause_on_interrupt`.
- Startup: `Scheduler.start()` is idempotent (Forge rebuilds the UI without restarting the process); the queue starts paused when jobs are left over unless autostart is on.

## JavaScript / Forge globals

- Load order is alphabetical: keep `gsched_core.js` before `gsched_queue.js` (`c` < `q`); don't rename in a way that breaks that.
- Read settings from the bare global `opts`, never `window.opts`; use `gradioApp()` for DOM lookups; boot with `onUiLoaded` + `onAfterUiUpdate` (the root may not exist yet).
- Escape every piece of job text with `GschedCore.escapeHtml` — prompts are user input rendered via `innerHTML`.

## Testing

```bash
python -m unittest discover -s tests -v          # stdlib only; Gradio-dependent tests skip
node tests/js/test_gsched_core.mjs
"<forge>/venv/bin/python" -m unittest discover -s tests   # everything, incl. real Gradio + FastAPI
tests/e2e/run_forge.sh                            # then drive http://127.0.0.1:7861 in a browser
```

When changing rendering, extend `tests/js/test_gsched_core.mjs`. When changing wiring, extend `tests/test_wiring.py` (real Gradio) — it builds a miniature of Forge's tab. When changing what Forge internals are called, extend `tests/fake_forge.py` + `tests/test_executor.py`.

Known gap: real sampling and real checkpoint switching have only been checked against fakes (they need a model and a free GPU).
