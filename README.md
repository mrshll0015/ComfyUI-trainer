# ComfyUI-trainer

Browser UI for ComfyUI: upload photo → batch generate → rate outputs → auto-learn prompts & settings.

**Prompts live in `prompts.json` only** — not in Python code.

### RunPod

Clone into `/workspace/runpod-slim/trainer`, then:

```bash
cd /workspace/runpod-slim
bash trainer/start-trainer.sh
```

Expose HTTP port **8189** in RunPod.

| Service | URL |
|---------|-----|
| ComfyUI | `https://YOUR-POD-8188.proxy.runpod.net/` |
| Trainer | `https://YOUR-POD-8189.proxy.runpod.net/` |

### Workflow

1. **Generate tab** — upload training photo
2. Choose **Prompt 1** or **Prompt 2** (edit text in Prompts tab)
3. Run **10 generations** (or 5/3/1)
4. Wait for batch to finish
5. See **unrated video count** → **Sync & start rating**
6. Rate hands, fingers, skin, face, motion…
7. System updates `prompts.json` + sampler settings from best ratings

### Prompt profiles

- `prompt_1` / `prompt_2` in `prompts.json`
- `app-photo-video.json` has **empty** CLIPTextEncode widgets — models only, prompts injected at queue time
- CLIPSeg mask text in `shared` section of prompts.json

### CLI

```bash
python3 -m trainer.web --port 8189
python3 -m trainer.suggest --workflow app-photo-video.json
python3 -m trainer.apply_cli --workflow app-photo-video.json
```

### Database

`ratings.sqlite` — generations, ratings, batch runs (created at runtime, gitignored)
