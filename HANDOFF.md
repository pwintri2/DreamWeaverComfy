# DreamweaverComfy Handoff

Datum: 2026-06-10  
Projectmap: `/home/pwintri2/DreamweaverComfy`  
Context: dit is een ComfyUI/Tauri-stripgenerator. Ouroboros blijft hier buiten scope.

## Doel

Een lokale Tauri-app bouwen die lange verhalen omzet naar A4-stripverhalen:

1. gebruiker uploadt of plakt een verhaal tot 50.000 woorden;
2. app analyseert de tekst eerst in lagen;
3. app maakt een story bible met personages, locaties, objecten en chunks;
4. app plant automatisch scenes, panels en A4-pagina's;
5. elk panel krijgt een strakke prompt met zichtbare en verboden personages;
6. ComfyUI rendert de panelbeelden.

De kernkeuze: het beeldmodel mag het verhaal niet raden. De planner moet eerst begrijpen wie waar is, wie vertrekt, wat zichtbaar is, en wat expliciet niet in beeld mag.

## Huidige versie

Appversie: `0.2.4`

Belangrijke bestanden:

- `server.py`: backend, documentextractie, ComfyUI-bridge, story planner, jobstatus.
- `index.html`: UI-structuur.
- `main.js`: UI-logica, upload, planner/model-dropdowns, job polling, Story Bible-weergave.
- `styles.css`: layout voor strip, panels, character bible en story bible.
- `api.js`: frontend API-wrapper.
- `src-tauri/tauri.conf.json`: Tauri-shell, current URL `http://127.0.0.1:8791/?v=0.2.4`.
- `src-tauri/Cargo.toml`: Tauri appversie `0.2.4`.
- `README.md`: korte gebruiksdocumentatie.

Gebouwde installer:

```text
/home/pwintri2/DreamweaverComfy/src-tauri/target/release/bundle/deb/Dreamweaver Comfy_0.2.4_amd64.deb
```

## Runtime

Huidige relevante services:

```text
Dreamweaver preview: http://127.0.0.1:8791
Oude preview:        http://127.0.0.1:8788
ComfyUI:             http://127.0.0.1:8188
Ollama:              http://127.0.0.1:11434
```

Let op: poort `8788` draaide nog met een oude server. De actuele versie is `8791`.

Start preview handmatig:

```sh
cd /home/pwintri2/DreamweaverComfy
python3 server.py --host 127.0.0.1 --port 8791
```

Start Tauri dev:

```sh
cd /home/pwintri2/DreamweaverComfy
npm run tauri:dev
```

Build installer:

```sh
cd /home/pwintri2/DreamweaverComfy
npm run tauri:build
```

## Wat Er Gebouwd Is

### Documentinvoer

Ondersteund:

- `.txt`
- `.md` / Markdown
- `.docx`
- tekst-PDF

Backend endpoint:

```text
POST /api/extract-text
```

DOCX wordt lokaal uit de XML gelezen. PDF gebruikt `PyPDF2`, met fallback naar `pdftotext`. Gescande PDF's hebben nog OCR nodig.

### Meerlagige Verhaalplanner

Nieuwe pipeline in `server.py`:

```text
story
-> story_analysis_chunks
-> rule_story_chunk_analysis of llm_story_chunk_analysis
-> merge_character_cards
-> build_world_bible
-> timeline_casts_for_scenes
-> panel_casts_for_scene
-> build_panel_prompt
-> paginate_comic_panels
```

Pipeline-ID in output:

```text
chunked_story_bible_v1
```

Belangrijke eigenschappen:

- lange tekst wordt eerst in analysechunks gesplitst;
- kleine overlap tussen chunks voor continuiteit;
- per chunk worden personages, locaties, objecten en events geanalyseerd;
- alle personages worden samengevoegd tot canonieke character cards;
- objecten en locaties komen in een world/story bible;
- scenes en panels krijgen expliciet:
  - `characterIds`
  - `absentCharacterIds`
  - `exitingCharacterIds`

### Lokale LLM Planner

De dropdown heet nu **Verhaalplanner**.

Als Ollama draait, worden lokale modellen getoond. `qwen2.5:latest` wordt automatisch aanbevolen.

Huidige eerste keuzes:

```text
Ollama: qwen2.5:latest (aanbevolen)
Ollama: mistral:latest
Ollama: llama3:latest
Ollama: llama3:8b
```

Uitgesloten uit de lokale plannerlijst:

- embeddings
- coder/code-modellen
- `:cloud` modellen

Als Ollama JSON faalt of te traag is, valt die chunk terug op lokale regels. De hele job hoeft dan niet stuk.

Timeout:

```sh
export OLLAMA_PLANNER_TIMEOUT=180
```

Praktische observatie: `qwen2.5:latest` werkte, maar de eerste kleine test duurde ongeveer een minuut. Voor lange verhalen eerst `Alleen storyboard` gebruiken.

### Character Continuity

Problemen die gericht zijn aangepakt:

- woorden als `look`, `there`, `apartment` worden niet meer als personage gekozen;
- object-only verhalen krijgen niet automatisch een hoofdpersoon;
- personages die vertrekken worden als afwezig/off-screen gemarkeerd;
- lege panels zoals `de kamer is leeg` en `no one inside` blijven zonder personages;
- zichtbare personages worden uit de negatieve prompt gefilterd;
- gender wordt waar mogelijk uit de tekst of LLM-analyse overgenomen en in de continuity prompt vastgezet.

Promptregels per panel bevatten nu onder andere:

- `visible cast: ONLY ...`
- `do not show: ...`
- `do not invent additional people`
- `keep identity and gender locked`
- `natural anatomy, correct hands`

### UI

Toegevoegd:

- Story Bible-paneel boven de A4-pagina's;
- plannerstatus voor chunkanalyse;
- aanbevolen plannerselectie voor `qwen2.5:latest`;
- zichtbare cast en afwezige cast in paneldetails.

### ComfyUI Modelkeuze

Local image/video dropdown ondersteunt:

- `Z-Image Turbo`
- `Wan 2.2 14B - video still per panel`
- `Wan 2.1 1.3B - video still per panel`
- generieke checkpoints uit:

```text
/home/pwintri2/ComfyUI/models/checkpoints/
```

Pony Diffusion is gezocht maar niet gevonden in de standaard ComfyUI modelmappen. `models/checkpoints/` was leeg. Als Pony als `.safetensors` of `.ckpt` daar geplaatst wordt, kan de generieke checkpoint-workflow hem tonen als `Checkpoint: ...`.

## VRAM Observatie

Laatste concrete check met `nvidia-smi`:

```text
GPU: NVIDIA GeForce RTX 5060 Laptop GPU
VRAM totaal: 8151 MiB
VRAM gebruikt: ongeveer 5836 MiB

ComfyUI python: ongeveer 5074 MiB
Ollama runner:  ongeveer 380 MiB
Desktop/GPU:    ongeveer 324 MiB
```

Conclusie:

- ComfyUI gebruikt de Nvidia VRAM duidelijk.
- Ollama kan ook VRAM gebruiken, maar bij die check was het beperkt.
- ComfyUI en Ollama concurreren om dezelfde 8GB VRAM.
- Beste workflow: eerst `Alleen storyboard`, daarna pas renderen.

## Validatie Die Is Gedaan

Checks:

```sh
python3 -m py_compile server.py
npm run check
npm run tauri:build
```

Smokechecks:

- `/api/status` op `8791` geeft versie `0.2.4`;
- ComfyUI staat online;
- `qwen2.5:latest` staat als aanbevolen planner in `/api/status`;
- storyboard-only API-job geeft `chunked_story_bible_v1`;
- testverhaal met `Mira`, `Jona`, vertrek en lege kamer:
  - panel 1: Mira + Jona zichtbaar;
  - panel 2: Jona zichtbaar, Mira afwezig;
  - panel 3: lege kamer, beide afwezig.

## Bekende Beperkingen

- Beeldmodellen kunnen nog steeds rare ledematen maken. De prompt helpt, maar lost anatomie niet volledig op.
- Voor echte character consistency zijn reference sheets, IP-Adapter/InstantID/ControlNet of vergelijkbare ComfyUI-nodes nodig.
- Cloud/API planners zijn nog placeholders. Ze staan in de dropdown als env-gebaseerde opties, maar zijn nog niet aangesloten als echte planner-adapters.
- PDF OCR voor gescande PDF's ontbreekt.
- Pony Diffusion is nog niet gevonden/geplaatst.
- Er draaide nog een oude server op `8788`; actuele preview is `8791`.
- Er is geen git-repository in `/home/pwintri2/DreamweaverComfy`, dus changes zijn niet via commits vastgelegd.

## Volgende Logische Stappen

1. Oude previewserver op `8788` stoppen of standaardiseren op `8791`.
2. Character reference sheets genereren voordat panels worden gerenderd.
3. Per personage een vast portret/reference image opslaan en in ComfyUI-workflows gebruiken.
4. ControlNet/OpenPose of pose-workflow toevoegen tegen rare ledematen.
5. Pony/SDXL preset toevoegen als Pony in `models/checkpoints/` staat.
6. Storyboard review-stap uitbreiden: gebruiker kan character cards, locaties en cast per panel corrigeren voordat rendering start.
7. OCR toevoegen voor gescande PDF's.
8. Optioneel: echte OpenAI/Claude/Gemini structured-output adapters aansluiten, maar alleen met expliciete API-key/env-config.

## Belangrijk Voor Volgende Agent

- Werk in `/home/pwintri2/DreamweaverComfy`, niet in WintripAI/Ouroboros.
- Gebruik geen secrets of API-keys in bestanden.
- Houd `Ouroboros` buiten deze app; dit is een ComfyUI/Dreamweaver-tool.
- Gebruik `apply_patch` voor handmatige edits.
- Test minimaal met:

```sh
cd /home/pwintri2/DreamweaverComfy
python3 -m py_compile server.py
npm run check
npm run tauri:build
```

