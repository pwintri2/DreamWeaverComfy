import { cancelJob, extractDocumentText, getJob, getStatus, regenerateComicPanel, startComic, startComfy, startDream, updateComicPanel } from "./api.js?v=0.2.4";
import { setState } from "./state.js?v=0.2.4";
import { addDream } from "./storage.js?v=0.2.4";
import { initBackground } from "./ui/background.js?v=0.2.4";
import { playDream, stopDream, togglePause } from "./ui/dream-player.js?v=0.2.4";
import { renderHistory } from "./ui/history-view.js?v=0.2.4";
import { showToast } from "./ui/toast-view.js?v=0.2.4";

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

function setOptions(select, options, fallbackLabel) {
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  const source = options?.length ? options : [{ id: "auto", label: fallbackLabel || "Auto", supported: true }];
  let recommended = "";
  for (const item of source) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    if (item.recommended) recommended = item.id;
    if (item.supported === false || item.configured === false) {
      option.disabled = true;
      option.textContent = `${item.label} (niet actief)`;
    }
    select.appendChild(option);
  }
  const canKeepPrevious = [...select.options].some((option) => option.value === previous && !option.disabled);
  if (recommended && select.id === "cloudModelSelect" && !select.dataset.initialized && (!previous || previous === "local_rules")) {
    select.value = recommended;
  } else if (canKeepPrevious) {
    select.value = previous;
  }
  select.dataset.initialized = "true";
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
  const estimatedPages = Math.max(1, Math.ceil(estimatedPanels / 6));
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
      ${characters.map((character) => `
        <article class="character-card">
          <strong>${escapeHtml(character.name)}</strong>
          <span>${escapeHtml(character.role)} · ${Number(character.mentions || 0)} vermeldingen</span>
          <p>${escapeHtml(character.visualSignature || "")}</p>
        </article>
      `).join("")}
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
  if (!analysis.pipeline && !chunks.length && !locations.length && !objects.length) {
    target.innerHTML = "";
    return;
  }
  const chunkPreview = chunks.slice(0, 6);
  target.innerHTML = `
    <h3>Story Bible</h3>
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
      <header class="page-label">Pagina ${escapeHtml(page.pageNumber)}</header>
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
    setOptions(document.getElementById("cloudModelSelect"), status.cloudModels || [], "Lokaal");
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
    cloudModel: document.getElementById("cloudModelSelect").value,
    renderMode: document.getElementById("comicRenderModeSelect").value,
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

async function handleComicSubmit(event) {
  event.preventDefault();
  const payload = payloadFromComicForm();
  const words = countWords(payload.story);
  if (words < 5) {
    showToast("Upload of plak eerst een verhaaltekst.");
    return;
  }
  if (words > 50000) {
    showToast("Deze versie accepteert maximaal 50.000 woorden per verhaal.", 5200);
    return;
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
  storyInput.addEventListener("input", updateStoryCount);
  document.getElementById("storyFileInput").addEventListener("change", async (event) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    try {
      showToast(`${file.name} uitlezen...`);
      const result = await textFromStoryFile(file);
      storyInput.value = result.text || "";
      updateStoryCount();
      const words = result.wordCount ?? countWords(result.text || "");
      showToast(`${file.name} geladen · ${Number(words || 0).toLocaleString("nl-NL")} woorden.`);
    } catch (error) {
      showToast(error.message, 6200);
    } finally {
      event.currentTarget.value = "";
    }
  });

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
