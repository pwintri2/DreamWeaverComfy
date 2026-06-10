import { deleteDream, loadHistory } from "../storage.js";

function truncate(text, length = 120) {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[char]);
}

export function renderHistory(onReplay) {
  const list = document.getElementById("historyList");
  const history = loadHistory();
  list.innerHTML = "";
  if (!history.length) {
    list.innerHTML = "<p class=\"intro\">Nog niets opgeslagen.</p>";
    return;
  }
  history.forEach((dream) => {
    const card = document.createElement("article");
    card.className = "history-card";
    card.innerHTML = `
      <header>
        <strong>${dream.resultType === "video" ? "Wan video" : "Z-Image slideshow"}</strong>
        <button class="ghost-button" type="button" data-delete>Verwijder</button>
      </header>
      <p>${escapeHtml(truncate(dream.desire || "Geen tekst"))}</p>
      <small>${new Date(dream.savedAt).toLocaleString()}</small>
      <div class="badge-row">
        ${(dream.transformed?.phrases || []).slice(0, 4).map((phrase) => `<span class="badge">${escapeHtml(phrase)}</span>`).join("")}
      </div>
    `;
    card.addEventListener("click", () => onReplay(dream));
    card.querySelector("[data-delete]").addEventListener("click", (event) => {
      event.stopPropagation();
      deleteDream(dream.id);
      renderHistory(onReplay);
    });
    list.appendChild(card);
  });
}
