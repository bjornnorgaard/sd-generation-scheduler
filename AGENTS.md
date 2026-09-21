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
- Ctrl/Cmd+Enter is overridden (always queues, unless the option is off) in the **capture** phase — Forge's handler is on `document`, bubble; keep Esc / Alt+Enter untouched.
- The live preview reuses Forge's `requestProgress`; the task id must be registered as pending *before* it is advertised (see `executor.run`).
- State Manager compat lives in `gsched_queue.js` (snapshot at Queue press, hand back at delivery) — keep the click script async, keep the guard narrow (skip only when we have no snapshot), and never swallow State Manager errors otherwise.
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

## Versioning and releases

This repo follows [Semantic Versioning](https://semver.org). **Git tags are the version of record** — there is no version file to keep in sync.

- Tags are annotated and named `vMAJOR.MINOR.PATCH` (e.g. `v0.3.1`), created on `main`.
- **MAJOR stays `0`.** Do not release `1.0.0` (or any `1.x`) unless the owner explicitly says the project is ready. While on `0.x`, a breaking change bumps MINOR.
- **MINOR** — a new user-facing feature or capability; a new or changed setting, default, shortcut, or `/gsched/v1` route; a changed queue database schema; any breaking change.
- **PATCH** — a bug fix, a compatibility fix (e.g. with another extension or a Forge update), or a performance / robustness improvement with no new capability.
- **No tag** — docs, tests, the e2e harness, refactors, or `AGENTS.md` edits that do not change shipped behaviour.
- If `git tag` is empty, the first release is `v0.1.0`.

Whenever you commit and push a releasable change to `main`, tag it in the same push:

```bash
git describe --tags --abbrev=0                      # latest version (none yet -> v0.1.0)
# ... commit the change on main ...
git tag -a vX.Y.Z -m "vX.Y.Z: <one-line summary>"    # on the commit that ships the change
git push origin main vX.Y.Z                         # commit and tag together
git ls-remote --tags origin vX.Y.Z                  # verify it arrived
```

Rules:

- Pick the bump from the *whole* change, not the last commit. When several changes ship together, use the highest bump.
- Never move, delete, or re-push a tag that has been pushed. If a release was wrong, ship a new PATCH.
- Never push a tag without its commit, and never tag a commit that is not on `main`.
- Only commit / push when the user has asked you to (as elsewhere); the tag is part of that push, not a separate ask.
- In your reply, state the version you tagged and why that bump.
