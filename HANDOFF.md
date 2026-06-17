# DreamweaverComfy Handoff

Datum: 2026-06-17
Projectmap: `/home/pwintri2/DreamweaverComfy`
Context: lokale ComfyUI/Tauri-stripgenerator. Ouroboros/WintripAI blijven buiten scope.

## Doel

Een lokale Tauri-app die lange verhalen (Engels, tot 50.000 woorden) omzet naar
A4-stripverhalen: tekst analyseren in lagen, story bible + personages bouwen,
scenes/panels/pagina's plannen, per panel een strakke prompt met zichtbare en
verboden personages, en ComfyUI laten renderen. Kernidee: het beeldmodel mag het
verhaal niet raden; de planner bepaalt wie waar in beeld is en wat juist niet.

## Versie en git

- App-versie: `0.2.9`.
- De map is een **git-repo**. Deze handoff hoort bij de v0.2.9-versie die aan het
  einde van de sessie gecommit is; `git log --oneline -1` toont de exacte commit.
- `.gitignore` sluit `node_modules/`, `src-tauri/target/`, `src-tauri/gen/`,
  `__pycache__/`, builds en **`data/`** (secrets!) uit.

Belangrijke bestanden:

- `server.py`: backend — extractie, ComfyUI-bridge, story planner, dialoog,
  cloud-planner-adapters, secrets, jobstatus, panel/portret-render.
- `index.html` / `main.js` / `styles.css` / `api.js`: UI.
- `data/secrets.json`: lokaal opgeslagen API-keys, **gitignored**, `rw-------`.

## Runtime

```text
Dreamweaver preview: http://127.0.0.1:8791   (actueel getest als v0.2.9)
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

Standalone Tauri:

```text
Lokale binary: /home/pwintri2/DreamweaverComfy/src-tauri/target/release/dreamweaver-comfy
Nieuw pakket:  /home/pwintri2/DreamweaverComfy/src-tauri/target/release/bundle/deb/Dreamweaver Comfy_0.2.9_amd64.deb
Appmenu:       ~/.local/share/applications/Dreamweaver Comfy.desktop wijst naar scripts/launch-desktop.sh
```

Let op: de systeempackage `/usr/bin/dreamweaver-comfy` stond nog op `0.1.2`
omdat `sudo dpkg -i ...` een wachtwoord vroeg. De user-level launcher start wel
de nieuwe lokale `0.2.9` Tauri-binary.

Optionele env-tuning:

```sh
export OLLAMA_PLANNER_TIMEOUT=180
export OLLAMA_PANEL_PROMPT_TIMEOUT=60
export OPENAI_MODEL=gpt-4o-mini
export ANTHROPIC_MODEL=claude-haiku-4-5-20251001
export GEMINI_MODEL=gemini-2.0-flash
export GROK_MODEL=grok-4.3
export XAI_API_KEY=xai-...
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
- Mood/locatie-output en exit/absence/empty-cues zijn Engels; de input is altijd
  Engels. Voorheen vielen die terug op Nederlandse defaults.

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
  (OpenAI, Anthropic, Google Gemini, xAI Grok, Replicate): gemaskeerde status, wachtwoordveld,
  Bewaar/verwijder, doc-link.
- Keys in `data/secrets.json` (gitignored, `chmod 600`). API geeft alleen
  gemaskeerde keys terug (`sk-…ABCD`). Opgeslagen key heeft voorrang op env.

### Cloud-planner-adapters
- `openai:env` / `anthropic:env` / `gemini:env` zijn nu echt aangesloten via
  `urllib` (geen nieuwe dependencies): OpenAI chat-completions, Anthropic messages,
  Gemini generateContent — elk JSON-output. Eén dispatcher (`planner_generate_json`)
  routeert op engine-type en haalt de key via `get_provider_key`.
- **Key komt nooit in de engine-dict, job-state of logs.**
  Grok gebruikt `XAI_API_KEY` en standaardmodel `GROK_MODEL=grok-4.3`.
- Chunk-analyse, per-panel grounding én globale synthese lopen allemaal door de
  dispatcher; een gekoppelde cloud-key verbetert het begrip overal. Per-chunk
  graceful fallback naar lokale regels bij fouten.

### Karakterextractie verharden + stripbeeld-trouw (follow-up 2026-06-12)
- **Alleen echte personages in bible & portretten**:
  - Uitgebreide `NON_CHARACTER_NAME_WORDS` (voices, shadows, dreams, machines, times, emotions, common nouns etc.).
  - Nieuwe `is_likely_real_person(name, story)` helper met actie/attributie context scan + heuristieken.
  - Strengere poorten in `extract_character_names` (hogere score drempels), `sanitize_character_candidate_name` (roept is_likely aan), en vooral in `merge_character_cards` (finale filter op alle kandidaten).
  - LLM chunk-analyse system prompt + schema sterk aangescherpt: "ONLY real persons/animals that speak/act; NEVER objects/places/concepts... If in doubt, omit."
  - Resultaat: Character Bible toont (en portret-knoppen gelden alleen voor) echte karakters. Niet-personen worden actief uit de cast en uit portret-generatie gehouden.
- **Betere plaatjes-verhaal-overeenkomst (prompt engineering)**:
  - `llm_panel_visual_prompt`: veel strengere systeem-instructie + user prompt over exacte visible cast (no extras, empty=zero humans).
  - `grounded_panel_text`: extra post-check die grounded visual rejecteert (fallback naar raw beat) als er een onbekende capitalized naam in zit die niet in visible cast voorkomt.
  - `build_panel_prompt`: cast_rule nu "STRICT CAST: ONLY ... visible ... no other people..."; expliciete obey-clausule aan eind.
  - `build_panel_negative_prompt` + globale `COMIC_NEGATIVE_PROMPT`: veel meer "extra person / stray face / unlisted character / background human" verboden, plus als present_ids bekend: "any human not listed in positive".
  - Karakter continuityPrompts en reference prompts benadrukken "this character only / one single person / no other people".
  - Deze changes maken de gegenereerde prompts veel explicieter over "wie wel / wie niet", wat de trouw aan het geplande verhaal verbetert binnen de limieten van Z-Image/Wan txt2img (geen echte reference injection mogelijk).

### Extra contextbegrip (follow-up 2026-06-13)
- **Panel continuity ledger** toegevoegd (`panel.continuity`):
  - vorige panelbeat + zichtbare cast;
  - focusobjecten uit de world bible en beattekst;
  - locatiecontinuiteit;
  - per relevant personage status (`visible`, `off-screen`, `exiting after this panel`) met laatst geziene panel/locatie;
  - notities zoals "panel is explicitly empty" of "leaving now".
- Deze ledger wordt meegegeven aan `panel_story_context`, zodat de LLM-grounding meer context heeft om pronouns/ambiguiteit te begrijpen zonder vorige gebeurtenissen letterlijk in het nieuwe panel te tekenen.
- `build_panel_prompt` krijgt compacte focusobjecten mee in de positieve prompt.
- UI toont de ledger in paneldetails onder "Context".
- Pipeline blijft `chunked_story_bible_v2`; deze tussenstap was appversie `0.2.5`.

### Regiebriefing + 4-panelsets (follow-up 2026-06-13, v0.2.6)
- Nieuwe eerste stap: **Analyseer verhaal** roept `POST /api/comic/brief` aan en
  bouwt `story_brief_v1` met samenvatting, gevonden personages, world bible en
  verduidelijkingsvragen.
- UI toont een **Regiebriefing** waar de gebruiker personagenotities kan invullen
  (`geen personage` verwijdert een fout gedetecteerd personage) en vragen kan
  beantwoorden over echte cast, vast uiterlijk, metaforen, verboden extra figuren
  en objectcontinuiteit.
- `POST /api/comic` accepteert nu `storyBrief` + `storyAnswers`. Deze antwoorden
  worden samengevoegd tot `userGuidance` en toegepast op:
  - character `visualSignature` / `continuityPrompt`;
  - filtering van foutieve personages;
  - globale story context;
  - positieve prompts;
  - negatieve prompts tegen literalized metaphor / silent extra figures.
- A4-pagina's zijn nu coherent geplande **sets van maximaal 4 panels**. Iedere
  set krijgt `panelSets[]`, `page.setReview` en `panel.setReview`; latere sets
  worden gecontroleerd tegen de vorige set. LLM-review wordt gebruikt voor de
  eerste `DREAMWEAVER_LLM_SET_REVIEW_MAX_SETS` sets als een LLM-planner actief is,
  anders valt het terug op lokale continuity-regels.
- Deze stap zat in `0.2.6`; de actuele preview hoort na herstart `0.2.9` te zijn.

### Noodreset + literal-only promptfilter (follow-up 2026-06-13, v0.2.7)
- Nieuwe knop **Reset GPU** naast Start ComfyUI.
- Nieuwe endpoint `POST /api/reset-comfy`:
  - markeert open Dreamweaver-jobs als reset aangevraagd;
  - stuurt ComfyUI `/interrupt`;
  - wist ComfyUI `/queue`;
  - wist in-memory `/history`;
  - stuurt `/free` met `unload_models` + `free_memory`;
  - geeft queuecounts en VRAM voor/na terug.
- Promptlaag aangescherpt:
  - abstracte locaties zoals `my own life`, `heart`, `mind`, `thought` worden niet
    meer als scene location gebruikt;
  - lokale fallback maakt zichtbare actie-only tekst: dialoog, gedachten en
    figuurlijke vergelijkingen worden uit de positieve prompt gehaald;
  - LLM-grounding valt bij twijfel niet meer terug op rauwe proza, maar op deze
    lokale visual-only fallback;
  - `geen personage`-notities worden niet meer in de positieve prompt gezet, maar
    als negatieve guidance verwerkt.
  - lokale regels verzinnen niet meer automatisch `Narrator`/`Protagonist` als
    personage wanneer er geen echte naam wordt gevonden.

### Grok API-planner (follow-up 2026-06-17, v0.2.8)
- API-key dialoog heeft nu provider **xAI Grok** (`XAI_API_KEY` of lokaal opgeslagen key).
- Verhaalplanner-dropdown toont **Grok API (`grok-4.3`)** als cloudplanner.
- Dispatcher gebruikt de xAI OpenAI-compatible endpoint `https://api.x.ai/v1/chat/completions`.

### Gesplitste verhaalplanner-keuze (follow-up 2026-06-17, v0.2.9)
- Stripformulier heeft nu **Plannerbron**, **Lokale planner** en **API-model** als
  aparte dropdowns.
- Lokale planner toont lokale regels + alle bruikbare Ollama-modellen.
- API-model toont OpenAI, Anthropic, Gemini en Grok naast de lokale plannerkeuze;
  niet-gekoppelde providers blijven zichtbaar met `key nodig`.

## Wat werkt

- Storyboard-pipeline (`chunked_story_bible_v2`): chunks → personages → world
  bible → scenes → panels → A4-pagina's.
- Documentinvoer: `.txt`, `.md`, `.docx`, tekst-PDF (geen OCR).
- Lokale render via **Z-Image Turbo** (txt2img) en **Wan 2.1/2.2** (video-still
  per panel). Checkpoint-workflow bestaat maar `models/checkpoints/` is leeg.
- Cast-logica: zichtbaar/afwezig/vertrekkend per scene en panel; lege scenes; panel-level continuity ledger; 4-panel setreviews.
- Dialoog-extractie + spreker-toewijzing (regelgebaseerd, geen LLM nodig).
- API-keys koppelen + cloud-planner; Gemini en Grok zijn als `configured`
  zichtbaar getest in `/api/status` en de Tauri-wrapper.
- Editor: prompt bewerken + per-panel/portret opnieuw renderen.

## Wat NOG NIET (goed) werkt — eerlijke staat

- **Plaatjes komen nog steeds niet 100% overeen met het verhaal.** Grounding + cast-regels + strikte positive/negative cast-enforcement helpen significant (zie recente aanscherpingen), maar txt2img op Z-Image/Wan blijft relatief los t.o.v. de beschrijving. Echte pixel-trouwe beeld-naar-verhaal fidelity vraagt nog altijd sterkere conditionering (IP-Adapter etc. — niet beschikbaar op huidige model-stack).
- **Referentieportretten zijn nu veel bruikbaarder (alleen echte personages).**
  1. **Karakterextractie is sterk verhard** (is_likely_real_person + uitgebreide NON_CHARACTER_NAME_WORDS + score-gates in extract + sanitize + final merge filter + LLM system prompt). Alleen echte acterende/sprekende personen of dieren (met context-signalen) komen in de Story Bible / Character Bible en krijgen portret-knoppen. Objecten, locaties, abstracten, voices/shadows etc. worden actief geweerd. (Was top priority.)
  2. Portret wordt nog steeds **niet in de panels geïnjecteerd** (geen img2img/IP-Adapter op Z-Image stack), dus portretten dienen puur als referentie/visuele bible.
- **Tekstballonnen (#3) zijn NOG NIET gebouwd.** Alleen de data (`panel.dialogue`)
  wordt gevuld; er is nog geen overlay-rendering. Daarom zie je geen ballonnen.
- **Cloud-planner live niet end-to-end getest** met een echte betaalde verhaal-call.
  Status/UI is wel geverifieerd: Gemini en Grok verschenen als configured
  API-planners. Let op kosten/latency: calls **per chunk én per panel** + 1
  synthese-call.
- **Echte identity-lock (IP-Adapter/InstantID) kan niet** op de huidige stack:
  alleen Z-Image Turbo + Wan; geen SDXL/SD1.5-checkpoint en geen
  ipadapter/clip_vision/controlnet-modellen of -nodes. Die tools zijn voor
  SDXL/SD1.5 en werken niet op Z-Image. (= "optie 2", apart traject.)
- OCR voor gescande PDF's ontbreekt.

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
POST /api/comic/brief                story briefing + vragen
POST /api/comic                      start storyboard/strip-job
GET  /api/jobs/{id}                  jobstatus + comic
POST /api/comic/update-panel         panelprompt bewerken
POST /api/comic/regenerate-panel     1 panel opnieuw renderen
POST /api/comic/character-reference  portret per personage
POST /api/reset-comfy                queue interrupten/wissen + VRAM vrijvragen
GET  /api/secrets                    providerstatus (gemaskeerd)
POST /api/secrets                    key opslaan/verwijderen
GET  /api/status                     versie, ComfyUI, modellen, planners
POST /api/extract-text               documentextractie
```

## Volgende logische stappen

1. ~~**Karakterextractie verharden**~~ — grotendeels opgelost in deze follow-up (is_likely_real_person + multi-laags filter + LLM-strict + blocklist). Nog steeds monitoren op edge cases in exotische verhalen (personificaties, rare diernamen etc.).
2. **#3 Tekstballonnen**: `panel.dialogue` als HTML/SVG-overlay op de panels
   renderen (auto-plaatsing + bewerkbaar, meegenomen in print/export).
3. **Beeld-naar-verhaal-trouw** verhogen: referentiebeeld-injectie of een
   SDXL+IP-Adapter-traject (optie 2 — eerst installplan, grote downloads). Prompt-verbeteringen (cast-strict + grounded-gates) helpen maar kunnen de limiet van de huidige Z-Image/Wan stack niet volledig overwinnen.
4. Cloud-planner live end-to-end testen met Gemini/Grok; kosten/latency meten.
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
cargo check --manifest-path src-tauri/Cargo.toml
npm run tauri:build
```
