# DreamweaverComfy Handoff

Datum: 2026-06-12
Projectmap: `/home/pwintri2/DreamweaverComfy`
Context: lokale ComfyUI/Tauri-stripgenerator. Ouroboros/WintripAI blijven buiten scope.

## Doel

Een lokale Tauri-app die lange verhalen (Engels, tot 50.000 woorden) omzet naar
A4-stripverhalen: tekst analyseren in lagen, story bible + personages bouwen,
scenes/panels/pagina's plannen, per panel een strakke prompt met zichtbare en
verboden personages, en ComfyUI laten renderen. Kernidee: het beeldmodel mag het
verhaal niet raden; de planner bepaalt wie waar in beeld is en wat juist niet.

## Versie en git

- App-versie: `0.2.4` (nog niet opgehoogd, ondanks veel wijzigingen).
- **Nieuw deze sessie:** de map is nu een **git-repo** (was er niet). Alle werk
  staat in commits, terug te draaien. `git log --oneline` toont de historie.
- `.gitignore` sluit `node_modules/`, `src-tauri/target/`, `src-tauri/gen/`,
  `__pycache__/`, builds en **`data/`** (secrets!) uit.

Belangrijke bestanden:

- `server.py`: backend — extractie, ComfyUI-bridge, story planner, dialoog,
  cloud-planner-adapters, secrets, jobstatus, panel/portret-render.
- `index.html` / `main.js` / `styles.css` / `api.js`: UI.
- `data/secrets.json`: lokaal opgeslagen API-keys, **gitignored**, `rw-------`.

## Runtime

```text
Dreamweaver preview: http://127.0.0.1:8791   (ACTUEEL)
Oude preview:        http://127.0.0.1:8788   (oude code, pid 1177681 — laten staan/zelf stoppen)
ComfyUI:             http://127.0.0.1:8188   (draait, ~OK)
Ollama:              http://127.0.0.1:11434
```

Start preview handmatig:

```sh
cd /home/pwintri2/DreamweaverComfy
python3 server.py --host 127.0.0.1 --port 8791
```

**Let op:** na elke `server.py`-wijziging moet de preview herstart worden, anders
draait oude code (symptoom: nieuwe endpoints geven HTTP 404). Bij herstart gaan
in-memory jobs verloren → storyboard opnieuw draaien voor een verse jobId.
UI altijd hard herladen zodat nieuwe `main.js`/`api.js` laden.

Optionele env-tuning:

```sh
export OLLAMA_PLANNER_TIMEOUT=180
export OLLAMA_PANEL_PROMPT_TIMEOUT=60
export OPENAI_MODEL=gpt-4o-mini
export ANTHROPIC_MODEL=claude-haiku-4-5-20251001
export GEMINI_MODEL=gemini-2.0-flash
```

## Wat deze sessie is gebouwd

### Prompt-fideliteit (B + C)
- **Cast-gebonden seed** (`cast_seed_offset`): zelfde zichtbare cast → zelfde
  seed, zodat terugkerende personages reproduceerbaarder renderen.
- **Schone positieve prompt**: alle "do not"-regels staan nu in de **negative**
  prompt (diffusie volgt ontkenningen in de positieve prompt slecht en roept
  genoemde dingen juist op). Lege panels duwen mensen-negatieven door.

### Grounded panel-beschrijving (A)
- Met een LLM-planner wordt elke beat gedistilleerd tot één compacte, puur-visuele
  Engelse zin ("teken alleen wat zichtbaar is"). Valt terug op de rauwe beat bij
  fouten of als een afwezig personage binnensluipt. Zichtbaar als "Grounded
  beschrijving" in de paneldetails.

### Vertaling naar Engels
- Mood/locatie-output, exit/absence/empty-cues en fallback-personagenamen
  (`Narrator`/`Protagonist`) zijn Engels; de input is altijd Engels. Voorheen
  vielen die terug op Nederlandse defaults.

### Editor (D)
- **Bewerkbare panels**: positieve/negatieve prompt per panel aanpassen
  (`POST /api/comic/update-panel`) en **per-panel opnieuw renderen**
  (`POST /api/comic/regenerate-panel`, verse seed voor variatie).

### Personage-referentieportretten (E', deelverzameling)
- Per personage één portret genereren uit de continuity-prompt
  (`POST /api/comic/character-reference`), getoond in de Character Bible met een
  genereer/opnieuw-knop. **Beperking by design:** zonder img2img/IP-Adapter wordt
  het portret niet in de panels geïnjecteerd.

### Beter verhaalbegrip (#1)
- **Dialoog-extractie** per panel (`panel.dialogue`): geciteerde zinnen + spreker
  via dichtstbijzijnde "said/asked/whispered"-clausule (vóór of ná het citaat,
  afstand als tiebreaker). Fundament voor tekstballonnen.
- **Minder hallucinatie**: vraag-/hulpwerkwoorden en zin-openers (Are, Why, Come,
  …) staan op de blocklist, zodat hoofdletter-woorden in citaten geen personage
  meer worden.
- **Scene- + globale context** in de grounding (strikt "teken dit NIET", alleen
  voor disambiguatie). Nieuwe globale synthese-pass (`build_global_story_summary`,
  één LLM-call), getoond bovenaan de Story Bible.
- Chunk-analyse-prompts naar het Engels.

### API-keys-pagina (Goose-stijl)
- Tandwiel ⚙ → "API-keys koppelen…" opent een dialoog met een kaart per provider
  (OpenAI, Anthropic, Google Gemini, Replicate): gemaskeerde status, wachtwoordveld,
  Bewaar/verwijder, doc-link.
- Keys in `data/secrets.json` (gitignored, `chmod 600`). API geeft alleen
  gemaskeerde keys terug (`sk-…ABCD`). Opgeslagen key heeft voorrang op env.

### Cloud-planner-adapters
- `openai:env` / `anthropic:env` / `gemini:env` zijn nu echt aangesloten via
  `urllib` (geen nieuwe dependencies): OpenAI chat-completions, Anthropic messages,
  Gemini generateContent — elk JSON-output. Eén dispatcher (`planner_generate_json`)
  routeert op engine-type en haalt de key via `get_provider_key`.
- **Key komt nooit in de engine-dict, job-state of logs.**
- Chunk-analyse, per-panel grounding én globale synthese lopen allemaal door de
  dispatcher; een gekoppelde cloud-key verbetert het begrip overal. Per-chunk
  graceful fallback naar lokale regels bij fouten.

## Wat werkt

- Storyboard-pipeline (`chunked_story_bible_v1`): chunks → personages → world
  bible → scenes → panels → A4-pagina's.
- Documentinvoer: `.txt`, `.md`, `.docx`, tekst-PDF (geen OCR).
- Lokale render via **Z-Image Turbo** (txt2img) en **Wan 2.1/2.2** (video-still
  per panel). Checkpoint-workflow bestaat maar `models/checkpoints/` is leeg.
- Cast-logica: zichtbaar/afwezig/vertrekkend per scene en panel; lege scenes.
- Dialoog-extractie + spreker-toewijzing (regelgebaseerd, geen LLM nodig).
- API-keys koppelen + cloud-planner (Gemini was live al "configured" via env-key).
- Editor: prompt bewerken + per-panel/portret opnieuw renderen.

## Wat NOG NIET (goed) werkt — eerlijke staat

- **Plaatjes komen nog steeds niet betrouwbaar overeen met het verhaal.** De
  grounding + cast-regels helpen, maar txt2img op Z-Image blijft vrij/los t.o.v.
  de beschrijving. Echte beeld-naar-verhaal-trouw vraagt sterkere conditionering
  (referentiebeeld-injectie / ControlNet / een ander basismodel).
- **Referentieportretten werken nog niet bruikbaar.** Twee oorzaken:
  1. **Karakterextractie ziet te veel als "persoon".** Niet-personen
     (objecten, plekken, losse woorden) belanden nog als personage in de bible,
     dus er worden portretten voor niet-personages gemaakt. De blocklist is
     uitgebreid maar dekt niet alles; dit heeft echt aandacht nodig (betere
     persoon-detectie, evt. via de LLM-planner met strengere validatie).
  2. Zelfs een correct portret wordt **niet in de panels geïnjecteerd** (geen
     img2img/IP-Adapter), dus het stuurt het beeld niet.
- **Tekstballonnen (#3) zijn NOG NIET gebouwd.** Alleen de data (`panel.dialogue`)
  wordt gevuld; er is nog geen overlay-rendering. Daarom zie je geen ballonnen.
- **Cloud-planner live niet end-to-end getest** met een echte betaalde call
  (offline gemockt en geverifieerd; Gemini-key was in env aanwezig). Let op
  kosten/latency: calls **per chunk én per panel** + 1 synthese-call.
- **Echte identity-lock (IP-Adapter/InstantID) kan niet** op de huidige stack:
  alleen Z-Image Turbo + Wan; geen SDXL/SD1.5-checkpoint en geen
  ipadapter/clip_vision/controlnet-modellen of -nodes. Die tools zijn voor
  SDXL/SD1.5 en werken niet op Z-Image. (= "optie 2", apart traject.)
- OCR voor gescande PDF's ontbreekt. App-versie nog niet opgehoogd.

## ComfyUI / VRAM

- Werkende modellen staan in `ComfyUI/models/diffusion_models/`
  (`z_image_turbo_bf16.safetensors`, `wan2.2_*`) en `loras/` (wan light loras).
- **Leeg / niet geïnstalleerd:** `ipadapter`, `clip_vision`, `controlnet`,
  `instantid`, `photomaker`, `checkpoints`. Custom nodes: alleen
  `comic_storyboard_prompter` + `websocket_image_save`.
- GPU: RTX 5060 Laptop, ~8 GB VRAM. ComfyUI en Ollama concurreren erom. Workflow:
  eerst **Alleen storyboard**, daarna renderen. Portretten + panels renderen één
  voor één.

## Belangrijke endpoints

```text
POST /api/comic                      start storyboard/strip-job
GET  /api/jobs/{id}                  jobstatus + comic
POST /api/comic/update-panel         panelprompt bewerken
POST /api/comic/regenerate-panel     1 panel opnieuw renderen
POST /api/comic/character-reference  portret per personage
GET  /api/secrets                    providerstatus (gemaskeerd)
POST /api/secrets                    key opslaan/verwijderen
GET  /api/status                     versie, ComfyUI, modellen, planners
POST /api/extract-text               documentextractie
```

## Volgende logische stappen

1. **Karakterextractie verharden** zodat alleen echte personen personages worden
   (de "ziet van alles als persoon"-bug). Dit blokkeert bruikbare portretten.
2. **#3 Tekstballonnen**: `panel.dialogue` als HTML/SVG-overlay op de panels
   renderen (auto-plaatsing + bewerkbaar, meegenomen in print/export).
3. **Beeld-naar-verhaal-trouw** verhogen: referentiebeeld-injectie of een
   SDXL+IP-Adapter-traject (optie 2 — eerst installplan, grote downloads).
4. Cloud-planner live end-to-end testen met een echte key; kosten/latency meten.
5. In-app modellen downloaden (keuze a) + app-versie ophogen + OCR.

## Belangrijk voor de volgende agent

- Werk in `/home/pwintri2/DreamweaverComfy`. Geen secrets in code/commits;
  keys horen in `data/secrets.json` (gitignored).
- Herstart `server.py` na backend-wijzigingen; hard-herlaad de UI.
- Test minimaal:

```sh
cd /home/pwintri2/DreamweaverComfy
python3 -m py_compile server.py
npm run check
```
