# Settings

All options live under **Settings → Generation Scheduler**. Keys use the `gsched_` prefix.

| Setting | Key | Default |
|---|---|---|
| Show a Queue button next to Generate | `gsched_show_queue_button` | `True` |
| Ctrl+Enter adds to the queue instead of generating | `gsched_override_ctrl_enter` | `True` |
| Resume the queue automatically when the WebUI starts | `gsched_autostart_on_launch` | `False` |
| Pause the queue when a running job is interrupted | `gsched_pause_on_interrupt` | `True` |
| Finished jobs to keep | `gsched_history_limit` | `100` |
| Extra settings to remember per job | `gsched_snapshot_options` | `CLIP_stop_at_last_layers` |
| Queue data directory | `gsched_data_dir` | *(empty)* = `<extension>/data` |

- **Ctrl+Enter** override: see [usage.md](usage.md#ctrlenter). Applies after a page reload.
- **Show a Queue button** and **Queue data directory** need a UI reload / WebUI restart to take effect. The other options apply to the next job.
- **Extra settings to remember** is a comma-separated list of WebUI setting keys (as in `config.json`). Their values are captured when you press Queue and applied while that job runs, then restored. The checkpoint, VAE / text encoders and UNet dtype are always remembered. Use this for settings that change the image but are not part of the Generate button — e.g. `CLIP_stop_at_last_layers, s_noise`. Unknown keys are ignored.
- **Data directory** holds `queue.sqlite3` and `inputs/` (saved img2img images), plus a `thumbs/` cache. Relative paths are relative to the extension folder. If you update the extension by replacing its folder, keep `data/` (or point this option elsewhere) to keep your queue.
