async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

export async function getStatus() {
  return readJson(await fetch("/api/status"));
}

export async function startComfy() {
  return readJson(await fetch("/api/start-comfy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }));
}

export async function resetComfy(payload = {}) {
  return readJson(await fetch("/api/reset-comfy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function startDream(payload) {
  return readJson(await fetch("/api/dream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function startComic(payload) {
  return readJson(await fetch("/api/comic", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function createStoryBrief(payload) {
  return readJson(await fetch("/api/comic/brief", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function extractDocumentText(payload) {
  return readJson(await fetch("/api/extract-text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function cancelJob(jobId) {
  return readJson(await fetch("/api/cancel-job", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jobId }),
  }));
}

export async function getJob(jobId) {
  return readJson(await fetch(`/api/jobs/${encodeURIComponent(jobId)}`));
}

export async function updateComicPanel(payload) {
  return readJson(await fetch("/api/comic/update-panel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function regenerateComicPanel(payload) {
  return readJson(await fetch("/api/comic/regenerate-panel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function generateCharacterReference(payload) {
  return readJson(await fetch("/api/comic/character-reference", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function getSecrets() {
  return readJson(await fetch("/api/secrets"));
}

export async function saveSecret(provider, key) {
  return readJson(await fetch("/api/secrets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, key }),
  }));
}
