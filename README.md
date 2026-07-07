# DreamweaverComfy

A local-first desktop app that turns long prose stories into A4 comic books. It
analyses a story in layers, builds a story bible (characters, locations, objects),
plans scenes/panels/pages, writes a tight image prompt per panel — with explicit
*visible* and *forbidden* characters — and renders the panels through a local
[ComfyUI](https://github.com/comfyanonymous/ComfyUI) instance.

The guiding principle: **the image model must not have to guess the story.** A
planner first decides who is present, who has left, what is on screen, and what
must explicitly *not* appear, so each render stays faithful to the text with as
few hallucinations as possible.

> Built as a Python backend + static web UI, shipped as a [Tauri](https://tauri.app/)
> desktop shell. The UI is in Dutch; stories are expected in English.

---

## Features

- **Layered story planner** (`chunked_story_bible_v2`): long text is split into
  analysis chunks → characters, locations, objects, events → canonical character
  cards → world/story bible → scenes → panels → A4 pages.
- **Story briefing before rendering** (`story_brief_v1`): run **Analyseer verhaal**
  first to let the planner ask clarification questions about real characters,
  fixed appearances, metaphors, forbidden extras and object continuity. Your
  answers are applied as user-approved canon when **Maak strip** runs.
- **Cast continuity per panel**: each panel carries explicit `characterIds`,
  `absentCharacterIds` and `exitingCharacterIds`. Departures and empty scenes are
  detected so off-screen characters are kept out of frame.
- **Panel continuity ledger**: each panel also stores previous-panel context,
  current character states, focus objects and location continuity, so the planner
  can resolve ambiguity without copying old events into the panel.
- **4-panel A4 sets**: pages are planned in coherent sets of up to four panels.
  Every set carries a continuity review against the previous set.
- **Emergency GPU reset**: **Reset GPU** interrupts ComfyUI, clears the queue and
  in-memory history, unloads models and requests VRAM cleanup.
- **Grounded panel prompts**: with an LLM planner, every beat is distilled into a
  compact, visual-only English image prompt ("draw only what is visible"), with a
  strict fallback to the raw beat on any sign of hallucination.
- **Cast-locked seeds**: the render seed is derived from the visible cast, so a
  recurring character renders more consistently across panels.
- **Clean prompt separation**: all negations live in the negative prompt (diffusion
  models follow "do not" cues poorly in the positive prompt).
- **Literal-only fallback prompts**: when no LLM planner is active, or an LLM
  grounding pass fails, raw prose is cleaned into visible physical action before
  it reaches ComfyUI. Dialogue, inner thoughts and figurative comparisons are kept
  out of the positive prompt.
- **Dialogue extraction**: quoted lines are extracted per panel and attributed to
  the nearest speaker (foundation for speech balloons).
- **Story understanding**: a global synthesis pass plus scene context feed the
  grounding step (used only to resolve pronouns/ambiguity, never copied into a panel).
- **Editor**: edit a panel's positive/negative prompt and re-render a single panel;
  generate a reference portrait per character.
- **Pluggable planners**: fast local rules, local **Ollama** models, or cloud
  **OpenAI / Anthropic / Gemini / Grok** via an in-app API-keys page.
- **Pluggable panel renderers**: local ComfyUI models or cloud **Modelslab** /
  **Atlas Cloud** (same service as ImagineAI, default `seedream-3.0`)
  text-to-image models for strip panels and character references.
- **Entity classification** (LLM planners): every named entity is explicitly
  typed (person / animal / sentient being / place / object / organization /
  voice-or-thought / metaphor) so only real acting beings become characters;
  places and objects are routed to the story bible instead.
- **Literal vs figurative split**: the analysis collects figurative phrases
  ("her heart was a caged bird") with what a naive illustrator might wrongly
  draw; per panel the beat is split into literal subject-verb-object actions
  and figurative phrases. Figurative phrases are excluded via the negative
  prompt and stored on the panel (`literalCheck`) for auditing.
- **Event-aligned panel beats** (LLM planners): scenes are cut into panels at
  event boundaries (new action, character enters/leaves, location shift)
  instead of pure word count, with strict validation and an even-split fallback.
- **Document input**: `.txt`, `.md`, `.docx`, and text-based PDF.
- **Story handoff API**: external apps (like BookReader's **Maak stripverhaal**
  button) can `POST /api/handoff/story` (`{title, story, autoStart}`); opening
  `/?handoff=<id>` pre-fills the comic form and, with `autoStart`, immediately
  starts building the comic. `GET /api/health` is a lightweight liveness probe.

## Planners

| Planner | Needs | Notes |
| --- | --- | --- |
| Local rules | nothing | No model, no network, no key. Fastest, least understanding. |
| Ollama | a running Ollama with a model (e.g. `qwen2.5`) | Story text stays local. |
| OpenAI / Anthropic / Gemini / Grok | an API key | Best understanding; text is sent to the provider. |

API keys are managed in the app (gear menu → **API-keys koppelen…**) and stored
locally in `data/secrets.json` (gitignored, `chmod 600`). Saved keys take
precedence over environment variables. Keys are never returned in full by the API,
and never placed in job state or logs.

In the comic form, **Plannerbron** chooses whether the story planner uses
**Lokale planner** or **API-model**. The API dropdown remains visible next to the
local planner dropdown so Gemini, Grok and other cloud planners can be selected
explicitly.

## Panel renderers

Panel rendering can use the models installed in your ComfyUI:

- **Z-Image Turbo** (`diffusion_models/z_image_turbo_bf16.safetensors`) — text→image.
- **Wan 2.1 / 2.2** — text→video, one still per panel.
- Generic SD/SDXL checkpoints from `models/checkpoints/` (if present).

Or choose **Modelslab** in the panel-renderer dropdown. Add your key in the app
via gear menu → **API-keys koppelen…** or set `MODELSLAB_API_KEY`. The default
Modelslab entries are `flux` and `sdxl`; add more with:

```sh
MODELSLAB_IMAGE_MODELS="model-id|Friendly label,another-model-id"
```

Or choose **Atlas Cloud** (the same service used by ImagineAI). Add your key via
the API-keys page or set `ATLAS_API_KEY`. The default image model is
`seedream-3.0`; override it with `ATLAS_IMAGE_MODEL` or add extra entries with:

```sh
ATLAS_IMAGE_MODELS="model-id|Friendly label,another-model-id"
```

> Atlas' `generateImage` API has no separate negative prompt or seed; the most
> important restrictions (no text/watermark, no unlisted characters) are appended
> to the prompt as natural-language rules, which SeedDream follows well.

> Note: true identity-lock (IP-Adapter / InstantID) is **not** supported on the
> Z-Image/Wan stack — those adapters target SD1.5/SDXL and require models/nodes
> that are not part of this setup.

## Requirements

- Python 3.11+
- A running ComfyUI (default `http://127.0.0.1:8188`) with the panel-helper modules
  (`image_prompt_page.py`, `wan_prompt_page.py`) available in the ComfyUI folder.
- Optional: [Ollama](https://ollama.com/) for a local LLM planner.
- Optional: Node.js + [Tauri](https://tauri.app/) toolchain to build the desktop app.

## Getting started

Run the backend + web UI directly:

```sh
cd DreamweaverComfy
python3 server.py --host 127.0.0.1 --port 8791
# open http://127.0.0.1:8791
```

Run as a Tauri desktop app:

```sh
npm install
npm run tauri:dev      # development
npm run tauri:build    # build installer (.deb under src-tauri/target/release/bundle/)
```

### Typical workflow

1. Paste or upload a story (English).
2. Pick a **Verhaalplanner** (planner): local rules, an Ollama model, or a cloud
   provider (couple a key first).
3. Click **Analyseer verhaal**, answer the briefing questions, and correct the
   character notes before generating panels.
4. Run **Alleen storyboard** (storyboard only) first — this plans everything
   without using the GPU, so you can review the story bible, cast, continuity, dialogue and
   per-panel prompts.
5. Render panels (Z-Image or Wan). ComfyUI and Ollama share VRAM, so render one at
   a time on small GPUs.
6. In the editor, tweak prompts or regenerate individual panels.

Useful environment variables:

```sh
COMFYUI_URL=http://127.0.0.1:8188
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_PLANNER_TIMEOUT=180
OLLAMA_PANEL_PROMPT_TIMEOUT=60
DREAMWEAVER_LLM_SET_REVIEW_MAX_SETS=24
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
GEMINI_MODEL=gemini-2.0-flash
GROK_MODEL=grok-4.3
XAI_API_KEY=xai-...
MODELSLAB_API_KEY=...
MODELSLAB_IMAGE_MODEL=flux
MODELSLAB_IMAGE_MODELS="sdxl|SDXL"
```

## HTTP API (selected)

```text
POST /api/comic/brief                build story briefing + questions
POST /api/comic                      start a storyboard/comic job
GET  /api/jobs/{id}                  job status + comic plan
POST /api/comic/update-panel         edit a panel's prompt
POST /api/comic/regenerate-panel     re-render a single panel
POST /api/comic/character-reference  generate a character portrait
POST /api/reset-comfy                interrupt queue + unload/free ComfyUI VRAM
GET  /api/secrets                    provider status (masked keys)
POST /api/secrets                    save/remove an API key
GET  /api/status                     version, ComfyUI status, models, planners
POST /api/extract-text               extract text from an uploaded document
```

## Known limitations

- **Image–story fidelity is improved but still imperfect.** Stricter cast rules, grounded distillation with hallucination gates, and heavy negative-prompt enforcement on extra people help a lot (see server.py changes), but text-to-image on Z-Image/Wan/Modelslab remains relatively loose. True strong fidelity needs reference-image conditioning, ControlNet, or a different base model (IP-Adapter not available on this stack).
- **Character extraction is now hardened.** Only real persons/animals that act or speak survive to the Character Bible and portrait buttons. Non-persons (objects, places, concepts, shadows/voices/etc.) are filtered via blocklists, context scoring, `is_likely_real_person`, and LLM strictness. Portraits are now reliably only for actual characters.
- **Speech balloons are not rendered yet.** Dialogue is extracted into the panel
  data (`panel.dialogue`) but not yet drawn as an overlay.
- **No identity-lock / IP-Adapter** on the current model stack (see above).
- **No OCR** for scanned PDFs.

## Security

- API keys live only in `data/secrets.json` (gitignored, owner-readable). Do not
  commit secrets.
- Cloud planners send your story text to the selected provider — the Story Bible
  shows a note when this happens. Modelslab panel rendering sends only the final
  panel prompt and negative prompt for each rendered panel/reference. Local rules,
  Ollama and local ComfyUI rendering keep everything local.

## Project layout

```text
server.py     backend: extraction, ComfyUI bridge, planner, dialogue, cloud
              adapters, secrets, job status, panel/portrait rendering
index.html    UI structure
main.js       UI logic (upload, planner/model dropdowns, polling, editor)
api.js        frontend API wrapper
styles.css    layout for pages, panels, character/story bible, API-keys page
src-tauri/    Tauri desktop shell
HANDOFF.md    current status and next steps
```

## License

No license has been chosen yet; treat as all-rights-reserved unless a `LICENSE`
file is added.
