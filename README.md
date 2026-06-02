# ComfyUI-trainer

Browser UI: upload up to **10 photos** → **1 video per photo** → rate body parts → learn.

**You edit one action line** (e.g. *she waves her hand*). NSFW inpaint/video quality prompts live in `prompts.json` → `system` (hidden, not in UI).

### RunPod

```bash
cd /workspace/runpod-slim
git -C trainer pull
bash trainer/start-trainer.sh
```

Expose HTTP port **8189** in RunPod.

### Workflow

1. Upload up to **10 photos**
2. Enter **one line** — what should happen in the video
3. **Generate** — one video per photo
4. **Sync & rate** — face, hands, fingers, body, skin, correct result
5. **Apply learned profile** when learning status is active

### prompts.json structure

```json
{
  "system": { "...": "hidden NSFW + quality prompts" },
  "shared": { "clipseg_upper": "..." },
  "prompt_1": { "action": "she waves her hand" },
  "prompt_2": { "action": "" }
}
```

### Training speed (lower resolution)

In `prompts.json` → `training`:

```json
"training": {
  "max_dimension": 384,
  "video_frames": 33
}
```

Defaults: **384px** max side (was 640), **33 frames** (was 49). Applied automatically on every generate. Increase for final quality runs.

### CLI

```bash
python3 -m trainer.web --port 8189
python3 -m trainer.apply_cli --workflow app-photo-video.json
```
