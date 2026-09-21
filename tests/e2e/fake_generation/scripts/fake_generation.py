"""E2E-only: replace the real generators so the queue can run without a model or GPU.

Loaded by ``tests/e2e/run_forge.sh`` into a throwaway Forge data directory. Never install
this into a real Forge ``extensions/`` folder.
"""
import json
import os
import time

from PIL import Image, ImageDraw

from modules import img2img, shared, txt2img

OUT = os.path.join(os.environ["E2E_DIR"], "outputs")
os.makedirs(OUT, exist_ok=True)
STEP_SECONDS = float(os.environ.get("E2E_STEP_SECONDS", "0.25"))
STEPS = 8


def publish_preview(step, prompt):
    """Mimic the sampler's live preview: a new image + a bumped id every step."""
    shade = int(255 * step / STEPS)
    image = Image.new("RGB", (256, 256), (shade, 60, 255 - shade))
    ImageDraw.Draw(image).text((10, 10), f"step {step}/{STEPS}\n{prompt[:30]}", fill=(255, 255, 255))
    shared.state.current_image = image
    shared.state.id_live_preview += 1


shared.state.set_current_image = lambda: None  # we set current_image ourselves


def make(kind, prompt_index):
    def fake(id_task, request, *args):
        prompt = args[prompt_index]
        shared.state.job_count = 1
        shared.state.job_no = 0
        shared.state.sampling_steps = STEPS
        for i in range(STEPS):
            if shared.state.interrupted:
                break
            shared.state.sampling_step = i + 1
            publish_preview(i + 1, prompt)
            time.sleep(STEP_SECONDS)
        if "FAIL" in prompt:
            raise RuntimeError("fake generator failure")
        image = Image.new("RGB", (512, 320), (40 + hash(prompt) % 200, 90, 160))
        ImageDraw.Draw(image).text((12, 12), f"{kind}: {prompt}", fill=(255, 255, 255))
        path = os.path.join(OUT, f"{kind}-{int(time.time() * 1000)}.png")
        image.save(path)
        image.already_saved_as = path
        info = {"infotexts": [f"{prompt}\nSteps: {STEPS}, Seed: 42"], "seed": 42}
        # Real Forge fills the info box with the infotext, including "Seed: N"; extensions such as
        # State Manager read the seed back from it.
        info_html = f"<p>{prompt}<br>Steps: {STEPS}, Seed: 42</p>"
        return ({"value": [image], "visible": True, "__type__": "update"}, None, json.dumps(info), info_html, "")

    return fake


txt2img.txt2img = make("txt2img", 0)
img2img.img2img = make("img2img", 1)
print("[e2e] fake generators installed")
