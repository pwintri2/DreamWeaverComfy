import { cancelJob, createStoryBrief, extractDocumentText, generateCharacterReference, getJob, getSecrets, getStatus, regenerateComicPanel, resetComfy, saveSecret, startComic, startComfy, startDream, updateComicPanel } from "./api.js?v=0.2.9";
import { setState } from "./state.js?v=0.2.9";
import { addDream } from "./storage.js?v=0.2.9";
import { initBackground } from "./ui/background.js?v=0.2.9";
import { playDream, stopDream, togglePause } from "./ui/dream-player.js?v=0.2.9";
import { renderHistory } from "./ui/history-view.js?v=0.2.9";
import { showToast } from "./ui/toast-view.js?v=0.2.9";

const views = {
  comic: document.getElementById("comicView"),
  input: document.getElementById("inputView"),
  loading: document.getElementById("loadingView"),
  player: document.getElementById("playerView"),
  history: document.getElementById("historyView"),
};

const loadingMessages = [
  "Metaforen weven...",
  "ComfyUI workflow klaarzetten...",
  "Droomlandschap renderen...",
  "Beelden aan elkaar laten ademen...",
  "Microtekst als overlay plannen...",
];

let currentDream = null;
let currentComicJobId = null;
let comicEditJobId = null;
let currentStoryBrief = null;
let loadingInterval = null;
let loadingIndex = 0;

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[char]);
}

function countWords(text) {
  const matches = String(text).trim().match(/[\wÀ-ÿ']+/g);
  return matches ? matches.length : 0;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

function documentKind(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith(".txt") || name.endsWith(".md") || name.endsWith(".markdown") || name.endsWith(".text")) {
    return "plain";
  }
  if (name.endsWith(".docx")) return "binary";
  if (name.endsWith(".pdf")) return "binary";
  return "unsupported";
}

async function textFromStoryFile(file) {
  const kind = documentKind(file);
  if (kind === "plain") {
    return { text: await file.text(), wordCount: null, kind: "text" };
  }
  if (kind === "unsupported") {
    throw new Error("Ondersteund: TXT, Markdown, DOCX en PDF.");
  }
  if (file.size > 35 * 1024 * 1024) {
    throw new Error("Dit bestand is te groot voor direct uitlezen. Maximaal 35 MB.");
  }
  const payload = await extractDocumentText({
    filename: file.name,
    mimeType: file.type,
    dataBase64: arrayBufferToBase64(await file.arrayBuffer()),
  });
  return payload;
}

function setOptions(select, options, fallbackLabel, config = {}) {
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  const source = options?.length ? options : [{ id: "auto", label: fallbackLabel || "Auto", supported: true }];
  let recommended = "";
  const disableUnavailable = config.disableUnavailable !== false;
  const annotateUnavailable = config.annotateUnavailable !== false;
  for (const item of source) {
    const option = document.createElement("option");
    option.value = item.id;
    option.dataset.provider = item.provider || "";
    option.dataset.configured = item.configured === false ? "false" : "true";
    option.dataset.description = item.description || "";
    option.textContent = item.label;
    if (item.recommended) recommended = item.id;
    const unavailable = item.supported === false || item.configured === false;
    if (unavailable && annotateUnavailable) {
      option.textContent = `${item.label} (${item.configured === false ? "key nodig" : "niet actief"})`;
    }
    if (unavailable && disableUnavailable) {
      option.disabled = true;
    }
    select.appendChild(option);
  }
  const optionsList = [...select.options];
  const canKeepPrevious = optionsList.some((option) => option.value === previous && !option.disabled);
  const canUseRecommended = optionsList.some((option) => option.value === recommended && !option.disabled);
  const firstConfigured = optionsList.find((option) => !option.disabled && option.dataset.configured !== "false");
  const firstAvailable = optionsList.find((option) => !option.disabled);
  if (recommended && canUseRecommended && config.preferRecommended && !select.dataset.initialized && (!previous || previous === "local_rules")) {
    select.value = recommended;
  } else if (canKeepPrevious) {
    select.value = previous;
  } else if (config.preferFirstConfigured && firstConfigured) {
    select.value = firstConfigured.value;
  } else if (firstAvailable) {
    select.value = firstAvailable.value;
  }
  select.dataset.initialized = "true";
}

function selectedPlannerId() {
  const source = document.getElementById("plannerSourceSelect")?.value || "local";
  const localSelect = document.getElementById("localPlannerSelect");
  const apiSelect = document.getElementById("apiPlannerSelect");
  if (source === "api") {
    const selected = apiSelect?.selectedOptions?.[0];
    if (!apiSelect?.value) {
      throw new Error("Kies eerst een API-model voor de verhaalplanner.");
    }
    if (selected?.dataset.configured === "false") {
      const label = selected.textContent.replace(/\s+\(key nodig\)$/, "");
      throw new Error(`Koppel eerst de API-key voor ${label}.`);
    }
    return apiSelect.value;
  }
  return localSelect?.value || "local_rules";
}

function syncPlannerControls() {
  const source = document.getElementById("plannerSourceSelect")?.value || "local";
  const localSelect = document.getElementById("localPlannerSelect");
  const apiSelect = document.getElementById("apiPlannerSelect");
  const apiHint = document.getElementById("apiPlannerHint");
  document.querySelectorAll(".planner-field").forEach((field) => {
    field.classList.toggle("is-inactive", field.dataset.plannerKind !== source);
  });
  const apiOptions = [...(apiSelect?.options || [])];
  const configuredApis = apiOptions.filter((option) => option.dataset.configured !== "false" && option.value);
  if (apiHint) {
    apiHint.textContent = configuredApis.length
      ? "API-planner actief zodra Plannerbron op API-model staat."
      : "Koppel eerst een API-key via instellingen om een API-planner te gebruiken.";
  }
}

function updateStoryCount() {
  const input = document.getElementById("storyInput");
  const words = countWords(input.value);
  const counter = document.getElementById("storyWordCount");
  const estimate = document.getElementById("storyEstimate");
  counter.textContent = `${words.toLocaleString("nl-NL")} / 50.000 woorden`;
  counter.style.color = words > 50000 ? "var(--bad)" : "var(--muted)";
  if (!words) {
    estimate.textContent = "Nog geen storyboard.";
    return;
  }
  const estimatedPanels = Math.max(1, Math.round(words / 80));
  const estimatedPages = Math.max(1, Math.ceil(estimatedPanels / 4));
  estimate.textContent = `Schatting: ongeveer ${estimatedPanels} panels op ${estimatedPages} A4-pagina's.`;
}

function showView(name) {
  Object.entries(views).forEach(([key, element]) => {
    element.classList.toggle("hidden", key !== name);
  });
  setState({ view: name });
}

function updateProgress(status) {
  const bar = document.getElementById("progressBar");
  const detail = document.getElementById("loadingDetail");
  const statusMap = {
    queued: ["Job aangemaakt.", 10],
    prepared: ["Prompt veilig getransformeerd.", 20],
    queued_video: ["Wan video staat in de ComfyUI queue.", 32],
    rendering_video: ["Wan video rendert. Dit kan even duren.", 62],
    rendering_image_1: ["Z-Image beeld 1 van 4 rendert.", 32],
    rendering_image_2: ["Z-Image beeld 2 van 4 rendert.", 48],
    rendering_image_3: ["Z-Image beeld 3 van 4 rendert.", 64],
    rendering_image_4: ["Z-Image beeld 4 van 4 rendert.", 80],
    success: ["Klaar.", 100],
    error: ["Er ging iets mis.", 100],
  };
  const [message, percent] = statusMap[status] || [status || "Bezig...", 45];
  detail.textContent = message;
  bar.style.width = `${percent}%`;
}

function startLoadingMessages() {
  clearInterval(loadingInterval);
  loadingIndex = 0;
  document.getElementById("loadingTitle").textContent = loadingMessages[0];
  loadingInterval = setInterval(() => {
    loadingIndex = (loadingIndex + 1) % loadingMessages.length;
    document.getElementById("loadingTitle").textContent = loadingMessages[loadingIndex];
  }, 3000);
}

function stopLoadingMessages() {
  clearInterval(loadingInterval);
  loadingInterval = null;
}

function renderAudit(dream) {
  const panel = document.getElementById("auditPanel");
  const transformed = dream.transformed || {};
  const grammar = transformed.visualGrammar || {};
  const grammarItems = [
    grammar.environment,
    grammar.symbol,
    grammar.material,
    grammar.motion,
    grammar.transition,
    grammar.palette,
    transformed.symbolicProfile?.theme ? `thema: ${transformed.symbolicProfile.theme}` : null,
  ].filter(Boolean);
  panel.innerHTML = `
    <h3>Audit</h3>
    <p class="intro">${escapeHtml(transformed.subconsciousMessage || "Symbolische beeldboodschap gegenereerd.")}</p>
    <div class="badge-row">
      ${(transformed.phrases || []).map((phrase) => `<span class="badge">${escapeHtml(phrase)}</span>`).join("")}
    </div>
    <div class="prompt-list">
      ${grammarItems.map((item) => `<span class="prompt-chip">${escapeHtml(item)}</span>`).join("")}
    </div>
    <div class="prompt-list">
      ${(transformed.imagePrompts || []).map((prompt) => `<span class="prompt-chip">${escapeHtml(prompt)}</span>`).join("")}
    </div>
  `;
}

function charactersForPanel(panel, characters, field = "characterIds") {
  const byId = new Map((characters || []).map((character) => [character.id, character]));
  return (panel[field] || [])
    .map((id) => byId.get(id)?.name)
    .filter(Boolean)
    .join(", ");
}

function renderCharacterBible(characters) {
  const target = document.getElementById("characterBible");
  if (!characters?.length) {
    target.innerHTML = "";
    return;
  }
  target.innerHTML = `
    <h3>Personages</h3>
    <div class="character-grid">
      ${characters.map((character) => {
        const refStatus = character.referenceStatus || "";
        const refImage = character.referenceImageUrl
          ? `<img class="character-ref" src="${escapeHtml(character.referenceImageUrl)}" alt="Referentie ${escapeHtml(character.name)}">`
          : `<div class="character-ref-placeholder">${refStatus === "rendering" ? "Portret renderen..." : refStatus === "error" ? "Renderen mislukt" : "Geen portret"}</div>`;
        const refLabel = character.referenceImageUrl ? "Nieuw portret" : "Genereer portret";
        const relationships = (character.relationships || []).slice(0, 3).map((relationship) => {
          const other = relationship.sourceId === character.id ? relationship.target : relationship.source;
          const relation = relationship.relation || "relatie";
          return other ? `${other}: ${relation}` : relation;
        }).filter(Boolean).join(" · ");
        return `
        <article class="character-card">
          <strong>${escapeHtml(character.name)}</strong>
          <span>${escapeHtml(character.role)} · ${Number(character.mentions || 0)} vermeldingen</span>
          ${refImage}
          ${relationships ? `<p class="character-relations">${escapeHtml(relationships)}</p>` : ""}
          <p>${escapeHtml(character.visualSignature || "")}</p>
          <button type="button" class="character-ref-btn" data-character-id="${escapeHtml(character.id)}">${refLabel}</button>
        </article>
      `;
      }).join("")}
    </div>
  `;
}

function renderStoryBible(comic) {
  const target = document.getElementById("storyBible");
  if (!target) return;
  const analysis = comic?.analysis || {};
  const world = comic?.world || analysis.world || {};
  const planner = analysis.planner || {};
  const chunks = world.chunkSummaries || [];
  const locations = world.locations || [];
  const objects = world.objects || [];
  const relationships = world.relationships || [];
  if (!analysis.pipeline && !chunks.length && !locations.length && !objects.length && !relationships.length) {
    target.innerHTML = "";
    return;
  }
  const chunkPreview = chunks.slice(0, 6);
  const globalSummary = analysis.globalSummary || "";
  const relationshipText = relationships.slice(0, 6)
    .map((relationship) => `${relationship.source} → ${relationship.target}: ${relationship.relation}`)
    .join(", ");
  target.innerHTML = `
    <h3>Story Bible</h3>
    ${globalSummary ? `<p class="story-summary">${escapeHtml(globalSummary)}</p>` : ""}
    <div class="bible-grid">
      <article>
        <strong>Planner</strong>
        <span>${escapeHtml(planner.label || comic.planner || "Lokale planner")}</span>
        <span>${Number(analysis.chunkCount || chunks.length || 0)} chunks</span>
      </article>
      <article>
        <strong>Locaties</strong>
        <span>${locations.slice(0, 8).map((item) => escapeHtml(item.name)).join(", ") || "geen vaste locaties"}</span>
      </article>
      <article>
        <strong>Objecten</strong>
        <span>${objects.slice(0, 8).map((item) => escapeHtml(item.name)).join(", ") || "geen vaste objecten"}</span>
      </article>
      <article>
        <strong>Relaties</strong>
        <span>${relationshipText ? escapeHtml(relationshipText) : "geen expliciete relaties"}</span>
      </article>
    </div>
    <details class="bible-details">
      <summary>Chunk-samenvattingen</summary>
      ${chunkPreview.map((chunk) => `
        <p><strong>${escapeHtml(chunk.chunkNumber)}.</strong> ${escapeHtml(chunk.summary || "Geen samenvatting.")}</p>
      `).join("")}
      ${chunks.length > chunkPreview.length ? `<p>+ ${chunks.length - chunkPreview.length} extra chunks</p>` : ""}
    </details>
  `;
}

function resetStoryBrief() {
  currentStoryBrief = null;
  const panel = document.getElementById("storyBriefPanel");
  panel.classList.add("hidden");
  document.getElementById("storyBriefSummary").innerHTML = "";
  document.getElementById("storyBriefCharacters").innerHTML = "";
  document.getElementById("storyBriefQuestions").innerHTML = "";
  const notes = document.getElementById("storyGlobalNotes");
  if (notes) notes.value = "";
}

function clearComicRuntimeUi() {
  currentComicJobId = null;
  comicEditJobId = null;
  document.getElementById("comicOutput").classList.add("hidden");
  document.getElementById("comicPages").innerHTML = "";
  document.getElementById("storyBible").innerHTML = "";
  document.getElementById("characterBible").innerHTML = "";
  document.getElementById("comicJobPanel").classList.add("hidden");
  document.getElementById("comicProgressBar").style.width = "0%";
}

function formatVramReset(result) {
  const device = (result?.vramAfter || [])[0];
  if (!device?.vramFree || !device?.vramTotal) return "";
  const free = Number(device.vramFree) / (1024 ** 3);
  const total = Number(device.vramTotal) / (1024 ** 3);
  return ` · VRAM vrij: ${free.toFixed(1)} / ${total.toFixed(1)} GB`;
}

function renderStoryBriefPanel(brief) {
  currentStoryBrief = brief;
  const panel = document.getElementById("storyBriefPanel");
  const title = document.getElementById("storyBriefTitle");
  const meta = document.getElementById("storyBriefMeta");
  const summary = document.getElementById("storyBriefSummary");
  const characterTarget = document.getElementById("storyBriefCharacters");
  const questionTarget = document.getElementById("storyBriefQuestions");
  const characters = brief?.characters || [];
  const questions = brief?.questions || [];
  const world = brief?.world || {};
  const planner = brief?.planner || {};
  title.textContent = brief?.title || "Verhaal begrepen";
  meta.textContent = `${Number(brief?.wordCount || 0).toLocaleString("nl-NL")} woorden · ${characters.length} personages · ${questions.length} vragen`;
  const locations = (world.locations || []).slice(0, 8).map((item) => item.name).filter(Boolean).join(", ");
  const objects = (world.objects || []).slice(0, 8).map((item) => item.name).filter(Boolean).join(", ");
  summary.innerHTML = `
    <p>${escapeHtml(brief?.globalSummary || "De planner heeft een eerste story bible gemaakt.")}</p>
    <div class="brief-facts">
      <span>Planner: ${escapeHtml(planner.label || planner.type || "lokaal")}</span>
      <span>Locaties: ${escapeHtml(locations || "geen vaste locaties")}</span>
      <span>Objecten: ${escapeHtml(objects || "geen vaste objecten")}</span>
    </div>
  `;
  characterTarget.innerHTML = `
    <h3>Personages controleren</h3>
    <div class="brief-grid">
      ${characters.map((character) => `
        <article class="brief-card">
          <strong>${escapeHtml(character.name || character.id || "Onbekend")}</strong>
          <span>${escapeHtml(character.role || "rol onbekend")} · ${escapeHtml(character.gender || "gender onbekend")}</span>
          <p>${escapeHtml(character.visualSignature || "Nog geen vast uiterlijk.")}</p>
          <textarea class="brief-character-note" data-character-id="${escapeHtml(character.id || "")}" rows="3" placeholder="Vast uiterlijk, leeftijd, gender, kleding. Schrijf 'geen personage' als deze eruit moet."></textarea>
        </article>
      `).join("") || "<p>Geen personages gevonden. Voeg in de algemene notities toe welke cast zichtbaar mag zijn.</p>"}
    </div>
  `;
  questionTarget.innerHTML = `
    <h3>Vragen voor betere regie</h3>
    <div class="brief-question-list">
      ${questions.map((question) => `
        <label class="brief-question">
          <span>${escapeHtml(question.question || "")}</span>
          ${question.why ? `<em>${escapeHtml(question.why)}</em>` : ""}
          <textarea class="brief-question-answer" data-question-id="${escapeHtml(question.id || "")}" rows="2" placeholder="Jouw antwoord wordt canon voor de strip."></textarea>
        </label>
      `).join("") || "<p>Geen extra vragen nodig volgens de planner.</p>"}
    </div>
  `;
  panel.classList.remove("hidden");
}

function collectStoryAnswers() {
  const answers = {};
  document.querySelectorAll(".brief-question-answer").forEach((input) => {
    const id = input.dataset.questionId;
    if (id && input.value.trim()) answers[id] = input.value.trim();
  });
  const characterNotes = {};
  document.querySelectorAll(".brief-character-note").forEach((input) => {
    const id = input.dataset.characterId;
    if (id && input.value.trim()) characterNotes[id] = input.value.trim();
  });
  return {
    answers,
    characterNotes,
    globalNotes: document.getElementById("storyGlobalNotes")?.value.trim() || "",
  };
}

function renderPanelContinuity(panel) {
  const continuity = panel?.continuity || {};
  const parts = [];
  const previous = continuity.previousPanel || {};
  if (previous.caption) {
    parts.push(`<p><em>Vorige panel:</em> ${escapeHtml(previous.caption)}</p>`);
  }
  const objects = continuity.focusObjects || [];
  if (objects.length) {
    parts.push(`<p><em>Focusobjecten:</em> ${objects.slice(0, 6).map(escapeHtml).join(", ")}</p>`);
  }
  const locations = continuity.focusLocations || [];
  if (locations.length) {
    parts.push(`<p><em>Locatiecontinuiteit:</em> ${locations.slice(0, 4).map(escapeHtml).join(", ")}</p>`);
  }
  const states = (continuity.characterStates || []).slice(0, 8).map((state) => {
    const bits = [state.name, state.status].filter(Boolean).join(": ");
    const location = state.location ? ` @ ${state.location}` : "";
    const lastSeen = state.lastSeenPanel && state.status === "off-screen" ? `, laatst in panel ${state.lastSeenPanel}` : "";
    return `${bits}${location}${lastSeen}`;
  }).filter(Boolean);
  if (states.length) {
    parts.push(`<p><em>Continuity:</em> ${states.map(escapeHtml).join(" · ")}</p>`);
  }
  const notes = continuity.notes || [];
  if (notes.length) {
    parts.push(`<p><em>Notities:</em> ${notes.slice(0, 5).map(escapeHtml).join(" · ")}</p>`);
  }
  return parts.length ? `<div class="panel-context">${parts.join("")}</div>` : "";
}

function renderSetReview(page) {
  const review = page?.setReview || {};
  const notes = (review.notes || []).filter(Boolean);
  const fixes = (review.fixes || []).filter(Boolean);
  const summary = page?.setSummary || "";
  if (!notes.length && !fixes.length && !summary) return "";
  const status = review.ok === false ? "Let op" : "Continuity";
  return `
    <div class="set-review ${review.ok === false ? "needs-work" : "ok"}">
      <strong>${escapeHtml(status)}</strong>
      ${summary ? `<span>${escapeHtml(summary)}</span>` : ""}
      ${notes.length ? `<span>${notes.slice(0, 2).map(escapeHtml).join(" · ")}</span>` : ""}
      ${fixes.length && review.ok === false ? `<span>${fixes.slice(0, 2).map(escapeHtml).join(" · ")}</span>` : ""}
    </div>
  `;
}

function renderComicPlan(comic) {
  if (!comic) return;
  const output = document.getElementById("comicOutput");
  const pages = document.getElementById("comicPages");
  output.classList.remove("hidden");
  document.getElementById("comicTitle").textContent = comic.title || "Storyboard";
  document.getElementById("comicMeta").textContent = `${comic.wordCount} woorden · ${comic.sceneCount} scenes · ${comic.panelCount} panels · ${comic.pageCount} A4-pagina's`;
  renderStoryBible(comic);
  renderCharacterBible(comic.characters || []);
  pages.innerHTML = (comic.pages || []).map((page) => `
    <article class="comic-page ${escapeHtml(page.layout || "layout-4")}">
      <header class="page-label">Pagina ${escapeHtml(page.pageNumber)} · Set ${escapeHtml(page.setNumber || page.pageNumber)}</header>
      ${renderSetReview(page)}
      ${(page.panels || []).map((panel) => {
        const names = charactersForPanel(panel, comic.characters || []);
        const absentNames = charactersForPanel(panel, comic.characters || [], "absentCharacterIds");
        const status = panel.status || "planned";
        const image = panel.imageUrl
          ? `<img src="${escapeHtml(panel.imageUrl)}" alt="Panel ${escapeHtml(panel.panelNumber)}">`
          : `<div class="panel-placeholder"><strong>${escapeHtml(panel.panelNumber)}</strong><span>${escapeHtml(status)}</span></div>`;
        return `
          <section class="comic-panel panel-${escapeHtml(panel.slot || 1)} ${escapeHtml(status)}">
            <div class="panel-art">${image}</div>
            <div class="panel-caption">${escapeHtml(panel.caption || "")}</div>
            <details class="panel-details">
              <summary>Prompt bewerken</summary>
              <p><strong>${escapeHtml(panel.shot || "shot")}</strong>${names ? ` · zichtbaar: ${escapeHtml(names)}` : " · geen personages"}</p>
              ${absentNames ? `<p>Afwezig/off-screen: ${escapeHtml(absentNames)}</p>` : ""}
              ${renderPanelContinuity(panel)}
              ${panel.visualDescription ? `<p><em>Grounded beschrijving:</em> ${escapeHtml(panel.visualDescription)}</p>` : ""}
              <label class="panel-prompt-label">Positieve prompt</label>
              <textarea class="panel-prompt-input" data-field="prompt" data-panel-id="${escapeHtml(panel.id)}" rows="4">${escapeHtml(panel.prompt || "")}</textarea>
              <label class="panel-prompt-label">Negatieve prompt</label>
              <textarea class="panel-prompt-input" data-field="negativePrompt" data-panel-id="${escapeHtml(panel.id)}" rows="2">${escapeHtml(panel.negativePrompt || "")}</textarea>
              <div class="panel-actions">
                <button type="button" class="panel-save" data-panel-id="${escapeHtml(panel.id)}">Bewaar prompt</button>
                <button type="button" class="panel-regen" data-panel-id="${escapeHtml(panel.id)}">Regenereer panel</button>
              </div>
            </details>
          </section>
        `;
      }).join("")}
    </article>
  `).join("");
}

function renderComicJob(job) {
  const panel = document.getElementById("comicJobPanel");
  const title = document.getElementById("comicJobTitle");
  const detail = document.getElementById("comicJobDetail");
  const bar = document.getElementById("comicProgressBar");
  panel.classList.remove("hidden");
  const total = Number(job.totalPanels || job.comic?.panelCount || 0);
  const rendered = Number(job.renderedPanels || 0);
  const status = job.status || "queued";
  const statusText = {
    queued: "Job aangemaakt.",
    analyzing: "Tekst analyseren, personages en scenes maken.",
    analyzing_story: "Verhaal opdelen en analyse voorbereiden.",
    analyzing_chunk: `Chunk ${job.currentChunk || "?"} van ${job.totalChunks || "?"} analyseren.`,
    writing_panel_prompts: `Panelprompt ${job.currentPanel || "?"} schrijven (grounded).`,
    planned: "Storyboard en prompts zijn klaar.",
    rendering_comic_panel: `Panel ${job.currentPanel || rendered + 1} van ${total || "?"} renderen.`,
    success: "Klaar.",
    cancelled: "Gestopt.",
    error: "Er ging iets mis.",
  }[status] || status;
  title.textContent = job.comic?.title || "Strip maken";
  detail.textContent = job.error || statusText;
  const percent = status === "success"
    ? 100
    : status === "analyzing" || status === "analyzing_story"
      ? 12
      : status === "analyzing_chunk" && job.totalChunks
        ? Math.min(42, 12 + Math.round((Number(job.currentChunk || 0) / Number(job.totalChunks || 1)) * 30))
      : total
        ? Math.min(98, Math.max(18, Math.round((rendered / total) * 100)))
        : 8;
  bar.style.width = `${percent}%`;
  if (job.comic) {
    renderComicPlan(job.comic);
  }
}

async function refreshStatus() {
  const statusElement = document.getElementById("comfyStatus");
  try {
    const status = await getStatus();
    const inventory = status.inventory || {};
    const models = [
      inventory.wan22 ? "Wan 2.2" : null,
      inventory.wan21 ? "Wan 2.1" : null,
      inventory.zimage ? "Z-Image" : null,
    ].filter(Boolean).join(", ") || "geen complete modelset gevonden";
    statusElement.textContent = `${status.comfyRunning ? "online" : "offline"} · ${models}`;
    statusElement.style.color = status.comfyRunning ? "var(--good)" : "var(--muted)";
    document.getElementById("startComfyBtn").disabled = status.comfyRunning;
    setOptions(document.getElementById("localComicModelSelect"), status.localModels || [], "Auto");
    const combinedPlanners = status.cloudModels || [];
    const localPlanners = status.localPlannerModels || combinedPlanners.filter((item) => ["local", "ollama"].includes(item.provider));
    const apiPlanners = status.apiPlannerModels || combinedPlanners.filter((item) => !["local", "ollama", "replicate"].includes(item.provider));
    setOptions(document.getElementById("localPlannerSelect"), localPlanners, "Lokale regels", { preferRecommended: true });
    setOptions(document.getElementById("apiPlannerSelect"), apiPlanners, "Geen API-modellen", {
      disableUnavailable: false,
      preferFirstConfigured: true,
    });
    syncPlannerControls();
    setState({ status });
  } catch (error) {
    statusElement.textContent = error.message;
    statusElement.style.color = "var(--bad)";
  }
}

async function pollJob(jobId, desire) {
  while (true) {
    const job = await getJob(jobId);
    updateProgress(job.status);
    if (job.done) {
      if (job.status === "error") {
        throw new Error(job.error || "ComfyUI job mislukt.");
      }
      return {
        ...job,
        desire,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, 2400));
  }
}

async function pollComicJob(jobId) {
  while (true) {
    const job = await getJob(jobId);
    renderComicJob(job);
    if (job.done) {
      if (job.status === "error") {
        throw new Error(job.error || "Stripjob mislukt.");
      }
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 2400));
  }
}

function panelPromptInputs(panelId) {
  const fields = {};
  document.querySelectorAll(`.panel-prompt-input[data-panel-id="${CSS.escape(panelId)}"]`).forEach((el) => {
    fields[el.dataset.field] = el.value;
  });
  return fields;
}

async function handlePanelEditClick(event) {
  const saveBtn = event.target.closest(".panel-save");
  const regenBtn = event.target.closest(".panel-regen");
  if (!saveBtn && !regenBtn) return;
  if (!comicEditJobId) {
    showToast("Geen actieve strip om te bewerken.");
    return;
  }
  const button = saveBtn || regenBtn;
  const panelId = button.dataset.panelId;
  const fields = panelPromptInputs(panelId);
  button.disabled = true;
  try {
    await updateComicPanel({
      jobId: comicEditJobId,
      panelId,
      prompt: fields.prompt,
      negativePrompt: fields.negativePrompt,
    });
    if (saveBtn) {
      showToast("Prompt bewaard.");
      return;
    }
    regenBtn.textContent = "Renderen...";
    await regenerateComicPanel({ jobId: comicEditJobId, panelId });
    await pollPanelRegen(comicEditJobId, panelId);
    showToast("Panel opnieuw gerenderd.");
  } catch (error) {
    showToast(error.message, 6200);
  } finally {
    button.disabled = false;
    if (regenBtn) regenBtn.textContent = "Regenereer panel";
  }
}

async function pollPanelRegen(jobId, panelId) {
  for (let i = 0; i < 240; i += 1) {
    const job = await getJob(jobId);
    const panel = (job.comic?.panels || []).find((p) => p.id === panelId);
    const busy = job.panelBusy === panelId || panel?.status === "rendering";
    if (!busy) {
      if (job.comic) renderComicPlan(job.comic);
      if (panel?.status === "error") {
        throw new Error(job.panelError || "Panel renderen mislukt.");
      }
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error("Panel renderen duurde te lang.");
}

function renderApiKeys(providers) {
  const list = document.getElementById("apiKeysList");
  list.innerHTML = (providers || []).map((provider) => {
    const statusLabel = provider.source === "saved"
      ? `Gekoppeld · ${escapeHtml(provider.masked)}`
      : provider.source === "env"
        ? `Via omgevingsvariabele ${escapeHtml(provider.envVar)} · ${escapeHtml(provider.masked)}`
        : "Niet gekoppeld";
    const statusClass = provider.configured ? "api-key-status configured" : "api-key-status";
    return `
      <article class="api-key-card">
        <div class="api-key-head">
          <strong>${escapeHtml(provider.label)}</strong>
          <span class="${statusClass}">${statusLabel}</span>
        </div>
        <div class="api-key-row">
          <input type="password" class="api-key-input" data-provider="${escapeHtml(provider.id)}" placeholder="${escapeHtml(provider.hint)}" autocomplete="off" spellcheck="false">
          <button type="button" class="api-key-save" data-provider="${escapeHtml(provider.id)}">Bewaar</button>
        </div>
        <a class="api-key-docs" href="${escapeHtml(provider.docs)}" target="_blank" rel="noreferrer noopener">Key aanmaken bij ${escapeHtml(provider.label)}</a>
      </article>
    `;
  }).join("");
}

async function openApiKeysDialog() {
  const dialog = document.getElementById("apiKeysDialog");
  document.getElementById("apiKeysList").innerHTML = "<p>Laden...</p>";
  dialog.showModal();
  try {
    const { providers } = await getSecrets();
    renderApiKeys(providers);
  } catch (error) {
    document.getElementById("apiKeysList").innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
}

async function handleApiKeySave(event) {
  const button = event.target.closest(".api-key-save");
  if (!button) return;
  const provider = button.dataset.provider;
  const input = document.querySelector(`.api-key-input[data-provider="${CSS.escape(provider)}"]`);
  const key = input ? input.value : "";
  button.disabled = true;
  try {
    const { providers } = await saveSecret(provider, key);
    renderApiKeys(providers);
    showToast(key.trim() ? "API-key gekoppeld." : "API-key verwijderd.");
    refreshStatus();
  } catch (error) {
    showToast(error.message, 5200);
  } finally {
    button.disabled = false;
  }
}

async function handleCharacterRefClick(event) {
  const button = event.target.closest(".character-ref-btn");
  if (!button) return;
  if (!comicEditJobId) {
    showToast("Geen actieve strip om portretten voor te maken.");
    return;
  }
  const characterId = button.dataset.characterId;
  button.disabled = true;
  button.textContent = "Renderen...";
  try {
    await generateCharacterReference({ jobId: comicEditJobId, characterId });
    await pollCharacterRef(comicEditJobId, characterId);
    showToast("Portret klaar.");
  } catch (error) {
    showToast(error.message, 6200);
  } finally {
    button.disabled = false;
  }
}

async function pollCharacterRef(jobId, characterId) {
  for (let i = 0; i < 240; i += 1) {
    const job = await getJob(jobId);
    const character = (job.comic?.characters || []).find((c) => c.id === characterId);
    const busy = job.characterBusy === characterId || character?.referenceStatus === "rendering";
    if (!busy) {
      if (job.comic) renderComicPlan(job.comic);
      if (character?.referenceStatus === "error") {
        throw new Error(job.characterError || "Portret renderen mislukt.");
      }
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error("Portret renderen duurde te lang.");
}

function payloadFromForm() {
  const [width, height] = document.getElementById("sizeSelect").value.split("x").map(Number);
  return {
    desire: document.getElementById("desireInput").value.trim(),
    mode: document.getElementById("modeSelect").value,
    model: document.getElementById("modelSelect").value,
    seconds: Number(document.getElementById("durationSelect").value),
    width,
    height,
  };
}

function payloadFromComicForm() {
  const [width, height] = document.getElementById("comicSizeSelect").value.split("x").map(Number);
  return {
    story: document.getElementById("storyInput").value.trim(),
    style: document.getElementById("comicStyleSelect").value,
    localModel: document.getElementById("localComicModelSelect").value,
    cloudModel: selectedPlannerId(),
    renderMode: document.getElementById("comicRenderModeSelect").value,
    storyBrief: currentStoryBrief,
    storyAnswers: collectStoryAnswers(),
    width,
    height,
  };
}

async function handleDreamSubmit(event) {
  event.preventDefault();
  const payload = payloadFromForm();
  if (!/[\wÀ-ÿ]/.test(payload.desire)) {
    showToast("Typ eerst iets dat verbeeld mag worden.");
    return;
  }

  showView("loading");
  updateProgress("queued");
  startLoadingMessages();

  try {
    const { jobId } = await startDream(payload);
    const dream = await pollJob(jobId, payload.desire);
    stopLoadingMessages();
    currentDream = dream;
    showView("player");
    renderAudit(dream);
    await playDream(dream);
    showToast("Droom klaar.");
  } catch (error) {
    stopLoadingMessages();
    showView("input");
    showToast(error.message, 5200);
  }
}

async function handleAnalyzeStoryClick(event) {
  const button = event.currentTarget;
  let payload;
  try {
    payload = payloadFromComicForm();
  } catch (error) {
    showToast(error.message, 5200);
    return;
  }
  const words = countWords(payload.story);
  if (words < 5) {
    showToast("Upload of plak eerst een verhaaltekst.");
    return;
  }
  if (words > 50000) {
    showToast("Deze versie accepteert maximaal 50.000 woorden per verhaal.", 5200);
    return;
  }
  button.disabled = true;
  const previousLabel = button.textContent;
  button.textContent = "Analyseren...";
  try {
    const { brief } = await createStoryBrief({
      story: payload.story,
      style: payload.style,
      cloudModel: payload.cloudModel,
    });
    renderStoryBriefPanel(brief);
    showToast(`Analyse klaar · ${(brief.characters || []).length} personages · ${(brief.questions || []).length} vragen.`);
  } catch (error) {
    showToast(error.message, 6200);
  } finally {
    button.disabled = false;
    button.textContent = previousLabel;
  }
}

async function handleResetComfyClick(event) {
  const button = event.currentTarget;
  button.disabled = true;
  const previousLabel = button.textContent;
  button.textContent = "Resetten...";
  try {
    stopDream();
    currentDream = null;
    clearComicRuntimeUi();
    const result = await resetComfy({ clearHistory: true });
    const before = result.queueBefore || {};
    const after = result.queueAfter || {};
    showToast(
      `Reset klaar · queue ${Number(before.running || 0) + Number(before.pending || 0)} → ${Number(after.running || 0) + Number(after.pending || 0)}${formatVramReset(result)}`,
      6200,
    );
    await refreshStatus();
  } catch (error) {
    showToast(error.message, 6200);
  } finally {
    button.disabled = false;
    button.textContent = previousLabel;
  }
}

async function handleComicSubmit(event) {
  event.preventDefault();
  let payload;
  try {
    payload = payloadFromComicForm();
  } catch (error) {
    showToast(error.message, 5200);
    return;
  }
  const words = countWords(payload.story);
  if (words < 5) {
    showToast("Upload of plak eerst een verhaaltekst.");
    return;
  }
  if (words > 50000) {
    showToast("Deze versie accepteert maximaal 50.000 woorden per verhaal.", 5200);
    return;
  }
  if (!currentStoryBrief) {
    showToast("Tip: analyseer eerst het verhaal voor betere personage- en metafoorcontrole.", 4200);
  }

  document.getElementById("comicOutput").classList.add("hidden");
  document.getElementById("comicPages").innerHTML = "";
  document.getElementById("storyBible").innerHTML = "";
  document.getElementById("characterBible").innerHTML = "";
  document.getElementById("comicJobPanel").classList.remove("hidden");
  document.getElementById("comicProgressBar").style.width = "8%";
  document.getElementById("comicJobTitle").textContent = "Strip maken";
  document.getElementById("comicJobDetail").textContent = "Job starten.";

  try {
    const { jobId } = await startComic(payload);
    currentComicJobId = jobId;
    comicEditJobId = jobId;
    const job = await pollComicJob(jobId);
    renderComicJob(job);
    showToast(payload.renderMode === "plan" ? "Storyboard klaar." : "Strip klaar.");
  } catch (error) {
    showToast(error.message, 6200);
  } finally {
    currentComicJobId = null;
  }
}

function bindEvents() {
  const desireInput = document.getElementById("desireInput");
  desireInput.addEventListener("input", () => {
    document.getElementById("charCount").textContent = `${desireInput.value.length} / 300`;
  });

  const storyInput = document.getElementById("storyInput");
  storyInput.addEventListener("input", () => {
    updateStoryCount();
    resetStoryBrief();
  });
  document.getElementById("storyFileInput").addEventListener("change", async (event) => {
    const input = event.currentTarget;
    const file = input?.files?.[0];
    if (!file) return;
    try {
      showToast(`${file.name} uitlezen...`);
      const result = await textFromStoryFile(file);
      storyInput.value = result.text || "";
      updateStoryCount();
      resetStoryBrief();
      const words = result.wordCount ?? countWords(result.text || "");
      showToast(`${file.name} geladen · ${Number(words || 0).toLocaleString("nl-NL")} woorden.`);
    } catch (error) {
      showToast(error.message, 6200);
    } finally {
      if (input) input.value = "";
    }
  });

  document.getElementById("comicStyleSelect").addEventListener("change", resetStoryBrief);
  document.getElementById("plannerSourceSelect").addEventListener("change", () => {
    syncPlannerControls();
    resetStoryBrief();
  });
  document.getElementById("localPlannerSelect").addEventListener("change", resetStoryBrief);
  document.getElementById("apiPlannerSelect").addEventListener("change", resetStoryBrief);
  document.getElementById("analyzeStoryBtn").addEventListener("click", handleAnalyzeStoryClick);
  document.getElementById("comicForm").addEventListener("submit", handleComicSubmit);
  document.getElementById("dreamForm").addEventListener("submit", handleDreamSubmit);
  document.getElementById("comicNav").addEventListener("click", () => {
    stopDream();
    showView("comic");
  });
  document.getElementById("inputNav").addEventListener("click", () => {
    stopDream();
    showView("input");
  });
  document.getElementById("historyNav").addEventListener("click", () => {
    renderHistory(replayDream);
    showView("history");
  });
  document.getElementById("settingsNav").addEventListener("click", () => {
    document.getElementById("settingsDialog").showModal();
  });
  document.getElementById("openApiKeysBtn").addEventListener("click", () => {
    document.getElementById("settingsDialog").close();
    openApiKeysDialog();
  });
  document.getElementById("closeApiKeysBtn").addEventListener("click", () => {
    document.getElementById("apiKeysDialog").close();
  });
  document.getElementById("apiKeysList").addEventListener("click", handleApiKeySave);
  document.getElementById("cancelBackBtn").addEventListener("click", () => {
    stopLoadingMessages();
    showView("input");
  });
  document.getElementById("newDreamBtn").addEventListener("click", () => {
    stopDream();
    showView("input");
  });
  document.getElementById("pauseBtn").addEventListener("click", (event) => {
    const paused = togglePause();
    event.currentTarget.textContent = paused ? "Hervat" : "Pauze";
  });
  document.getElementById("saveBtn").addEventListener("click", () => {
    if (!currentDream) return;
    addDream(currentDream);
    renderHistory(replayDream);
    showToast("Droom opgeslagen.");
  });
  document.getElementById("cancelComicBtn").addEventListener("click", async () => {
    if (!currentComicJobId) return;
    try {
      await cancelJob(currentComicJobId);
      showToast("Stop aangevraagd.");
    } catch (error) {
      showToast(error.message, 5200);
    }
  });
  document.getElementById("exportComicBtn").addEventListener("click", () => {
    window.print();
  });
  document.getElementById("comicPages").addEventListener("click", handlePanelEditClick);
  document.getElementById("characterBible").addEventListener("click", handleCharacterRefClick);
  document.getElementById("startComfyBtn").addEventListener("click", async () => {
    try {
      const result = await startComfy();
      showToast(result.message || (result.started ? "ComfyUI wordt gestart." : "ComfyUI draait al."));
      for (const delay of [3500, 8000, 15000, 30000]) {
        setTimeout(refreshStatus, delay);
      }
    } catch (error) {
      showToast(error.message, 5200);
    }
  });
  document.getElementById("resetComfyBtn").addEventListener("click", handleResetComfyClick);
}

async function replayDream(dream) {
  currentDream = dream;
  showView("player");
  renderAudit(dream);
  await playDream(dream);
}

async function boot() {
  initBackground(document.getElementById("backgroundCanvas"));
  bindEvents();
  renderHistory(replayDream);
  updateStoryCount();
  showView("comic");
  await refreshStatus();
  setInterval(refreshStatus, 15000);
}

boot();
