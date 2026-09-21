# Generation Scheduler

A [Stable Diffusion WebUI Forge - Neo](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo) extension that adds a **Queue** button next to **Generate**, so you can keep tweaking parameters and line up jobs instead of waiting for each one to finish.

**Only tested with Forge Neo.** It may work on other Automatic1111-compatible frontends, but that is unsupported.

Press **Queue** instead of Generate and the current settings are saved and run later, one at a time, in order. A **Queue** tab shows what is running, what is waiting, and what has finished — and lets you reorder, remove, pause, and clear.

## Features

- **Queue button** beside Generate on txt2img and img2img
- Captures *everything* Generate would send — prompt, sampler, seed, hires fix, ControlNet units, img2img / inpaint images, other extensions' arguments
- Remembers the **checkpoint, VAE / text encoders and UNet dtype** per job, so switching models after queueing doesn't change what a queued job uses
- **Queue tab**: live progress, reorder (top / up / down / bottom), remove, requeue, pause / start, interrupt current, clear queued / history, thumbnails of results
- Survives restarts (SQLite); a job cut off by a crash is marked *Interrupted* and can be requeued
- Manual Generate keeps working and shares the WebUI's queue lock — the two never run at the same time

## Install

1. Copy or clone this folder into your Forge Neo `extensions/` directory:

   ```
   .../Stable Diffusion WebUI Forge - Neo/extensions/sd-generation-scheduler/
   ```

2. Restart the WebUI (Stability Matrix → restart package, or rerun `webui.sh`).

3. Confirm a **Queue** button appears next to Generate and a **Queue** tab appears in the tab bar.

No extra Python packages are required.

## Quick start

1. Set up a generation as usual, then press **Queue** instead of **Generate**. A toast confirms `Queued #3 — 2 ahead`.
2. Change the prompt or parameters and press **Queue** again — as often as you like.
3. Open the **Queue** tab to watch progress. The queue runs automatically; **Pause queue** lets the current job finish and holds the rest, **Start queue** continues.
4. Finished jobs stay in *History* with their thumbnails. **Requeue** runs one again; **Interrupt current** stops the running job (and pauses the queue by default).

Details: [docs/usage.md](docs/usage.md).

## Documentation

| Doc | Contents |
|---|---|
| [docs/usage.md](docs/usage.md) | The Queue button, the Queue tab, pausing / interrupting, restarts, limits |
| [docs/settings.md](docs/settings.md) | Settings page keys and defaults |
| [docs/how-it-works.md](docs/how-it-works.md) | Architecture: how a click becomes a job, storage, the worker, the HTTP API |
| [tests/e2e/README.md](tests/e2e/README.md) | Browser test harness (throwaway Forge with a fake generator) |

For agents working in this repo: [AGENTS.md](AGENTS.md).

## Tests

From the extension directory (WebUI does not need to be running):

```bash
python -m unittest discover -s tests -v
node tests/js/test_gsched_core.mjs
```

The unit tests use only the standard library. Tests that need Gradio, FastAPI, Pillow or NumPy skip themselves when those are missing; to run everything, use Forge's own interpreter (`venv/bin/python -m unittest discover -s tests`).

## Security note

The Queue tab talks to `/gsched/v1/…` routes on the WebUI's own server. Mutating calls need an `X-Gsched` header, which stops other websites from driving them. Like most extension routes they do **not** honour `--gradio-auth`; don't expose the WebUI to untrusted networks.

## License

MIT — see [LICENSE](LICENSE).
