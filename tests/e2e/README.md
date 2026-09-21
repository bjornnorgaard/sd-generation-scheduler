# Browser end-to-end harness

Unit tests cover the logic; this harness runs the extension inside a *real* Forge Neo UI
so wiring against Forge's actual tab layout (and other extensions such as ControlNet) is
exercised too.

```bash
tests/e2e/run_forge.sh            # foreground; Ctrl+C to stop
E2E_STEP_SECONDS=1 tests/e2e/run_forge.sh   # slower fake jobs, easier to watch
```

It starts Forge Neo on `http://127.0.0.1:7861` in UI-debug mode (no model, CPU only) with
just this extension and `fake_generation`, which swaps `modules.txt2img.txt2img` /
`modules.img2img.img2img` for a fake that ticks through 8 sampling steps, honours
Interrupt, fails when the prompt contains `FAIL`, and writes a small PNG. Your real
Forge, its config and its GPU are not touched.

Manual checklist (all verified once against Forge Neo, Gradio 4.40):

- Queue button sits beside Generate on txt2img and img2img; while a normal Generate runs,
  Interrupt / Skip cover only the Generate button.
- Queue with a prompt → toast "Queued #N …"; job runs, thumbnails appear in History.
- Queue tab: Pause / Start, reorder (⤒ ▲ ▼ ⤓), remove, Requeue, Clear queued / history,
  Interrupt current (job → Interrupted, queue pauses).
- `FAIL` in the prompt → Failed with the error text.
- `kill -9` the server mid-job, restart → that job is Interrupted, the rest still Queued,
  queue paused until Start.
- img2img with an image in the canvas queues and runs (image saved under `data/inputs/`).

Not covered here: real sampling and real checkpoint / VAE switching — those need a model
and a free GPU, so check them by hand after installing.
