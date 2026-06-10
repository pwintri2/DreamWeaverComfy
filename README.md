# Dreamweaver Comfy

Dreamweaver Comfy is een lokale vanilla webapp die de inhoud van het Dreamweaver-plan koppelt aan de bestaande ComfyUI-installatie op deze machine.

## Wat hij doet

### Stripmodus

- Accepteert geplakte tekst of `.txt`/Markdown/`.docx`/`.pdf`-upload tot 50.000 woorden.
- Leest DOCX en tekst-PDF's lokaal uit. Gescande PDF's hebben later nog OCR nodig.
- Hakt lange teksten eerst in analysechunks, maakt daarna een story bible met samengevoegde personages, locaties, objecten en chunk-samenvattingen.
- Kan `qwen2.5:latest` of een ander lokaal Ollama-model als verhaalplanner gebruiken; `qwen2.5:latest` wordt automatisch aanbevolen wanneer Ollama hem meldt.
- Valt per chunk terug op lokale regels als een lokale LLM geen bruikbare JSON teruggeeft.
- Analyseert personages, scenes, locaties, stemming en verhaalbeats met expliciete cast-continuity.
- Berekent automatisch panels en A4-pagina's; je hoeft dus geen panel-aantal te kiezen.
- Maakt per panel een inspecteerbare prompt die het verhaal letterlijk probeert uit te beelden.
- Rendert panelen één voor één via ComfyUI en vult de A4-pagina's live.
- Heeft dropdowns voor lokale ComfyUI-workflows en verhaalplanners. API-keys worden niet opgeslagen; cloudopties lezen alleen env-vars wanneer adapters worden aangesloten.
- Ondersteunde lokale panelrenderers:
  - Z-Image Turbo als normale image workflow.
  - Wan 2.2 / Wan 2.1 als korte video waarvan een still als panel wordt gebruikt.
  - Generieke SD-checkpoints uit `models/checkpoints/` via een basis ComfyUI checkpoint-workflow.

### Droommodus

- Neemt een persoonlijke wens als input.
- Maakt lokaal inspecteerbare metaforische zinnen en beeldprompts.
- Stuurt de originele wens niet naar ComfyUI.
- Gebruikt de bestaande ComfyUI helpers:
  - `/home/pwintri2/ComfyUI/wan_prompt_page.py`
  - `/home/pwintri2/ComfyUI/image_prompt_page.py`
- Gebruikt de huidige modelsets wanneer aanwezig:
  - Wan 2.2 14B text-to-video
  - Wan 2.1 1.3B text-to-video
  - Z-Image Turbo text-to-image slideshow

## Ontwikkelmodus

Start eerst ComfyUI, of gebruik de knop in de app:

```sh
cd /home/pwintri2/ComfyUI
.venv/bin/python main.py --listen 127.0.0.1 --port 8188
```

Start daarna Dreamweaver:

```sh
cd /home/pwintri2/DreamweaverComfy
./scripts/run-dev.sh
```

Open anders handmatig:

```text
http://127.0.0.1:8788
```

## Flatpak

De manifest staat in `ai.wintrip.Dreamweaver.yml`.

```sh
cd /home/pwintri2/DreamweaverComfy
./scripts/build-flatpak.sh
flatpak run ai.wintrip.Dreamweaver
```

De Flatpak krijgt netwerktoegang voor `127.0.0.1:8188` en read-only home-toegang zodat hij de bestaande ComfyUI helperbestanden en modelinventaris kan lezen.

## Tauri desktop-app

Deze map bevat ook een Tauri desktop-shell met eigen venster en icoon.

Ontwikkelmodus:

```sh
cd /home/pwintri2/DreamweaverComfy
npm install
npm run tauri:dev
```

Installer bouwen:

```sh
cd /home/pwintri2/DreamweaverComfy
./scripts/build-tauri.sh
```

De Debian-installer komt hier terecht:

```text
/home/pwintri2/DreamweaverComfy/src-tauri/target/release/bundle/deb/Dreamweaver Comfy_0.2.4_amd64.deb
```

De Tauri-app bundelt de Dreamweaver UI en bridge, maar niet de enorme ComfyUI modellen. Hij gebruikt de bestaande lokale ComfyUI-installatie in `/home/pwintri2/ComfyUI`.

## Praktische beperking

Een verhaal van 50.000 woorden kan honderden panelen opleveren. De analyse is snel, maar volledig renderen kan uren duren afhankelijk van model, resolutie en GPU. Gebruik `Alleen storyboard` om eerst de panelindeling en prompts te controleren.

Met `qwen2.5:latest` als planner kan de analyse ook merkbaar langer duren, vooral bij de eerste Ollama-aanroep of bij veel chunks. De standaard timeout per chunk is 180 seconden en kan worden aangepast met:

```sh
export OLLAMA_PLANNER_TIMEOUT=240
```
