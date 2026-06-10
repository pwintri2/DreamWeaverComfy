const HISTORY_KEY = "dreamweaver-comfy-history";

export function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveHistory(history) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 20)));
}

export function addDream(dream) {
  const history = loadHistory();
  const next = [{ ...dream, id: crypto.randomUUID(), savedAt: new Date().toISOString() }, ...history].slice(0, 20);
  saveHistory(next);
  return next;
}

export function deleteDream(id) {
  const next = loadHistory().filter((item) => item.id !== id);
  saveHistory(next);
  return next;
}
