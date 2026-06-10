let animationId = null;
let flashTimer = null;
let loadedImages = [];
let paused = false;
let currentDream = null;
let canvasMode = "images";
let resizeTarget = null;

const positions = [
  [12, 16], [50, 14], [82, 18], [18, 48], [72, 50], [35, 76], [78, 78],
];

function randomItem(items) {
  return items[Math.floor(Math.random() * items.length)];
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = url;
  });
}

async function preload(urls, allowPartial = false) {
  if (!allowPartial) {
    return Promise.all(urls.map(loadImage));
  }
  const results = await Promise.allSettled(urls.map(loadImage));
  return results
    .filter((result) => result.status === "fulfilled")
    .map((result) => result.value);
}

async function fetchVideoFrames(mediaUrl) {
  if (!mediaUrl) return [];
  try {
    const url = new URL(mediaUrl, window.location.href);
    const query = url.searchParams.toString();
    if (!query) return [];
    const response = await fetch(`/api/video-frames?${query}&max_frames=48`, { cache: "no-store" });
    if (!response.ok) return [];
    const payload = await response.json();
    return Array.isArray(payload.frameUrls) ? payload.frameUrls : [];
  } catch {
    return [];
  }
}

function resizeCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function handleResize() {
  if (resizeTarget) {
    resizeCanvas(resizeTarget);
  }
}

function drawCanvas(canvas, startTime) {
  if (!currentDream || paused || !loadedImages.length) {
    animationId = requestAnimationFrame(() => drawCanvas(canvas, startTime));
    return;
  }
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const elapsed = (performance.now() - startTime) / 1000;
  const frameDuration = canvasMode === "videoFrames" ? 0.1 : 2.5;
  const index = Math.floor(elapsed / frameDuration) % loadedImages.length;
  const nextIndex = (index + 1) % loadedImages.length;
  const local = (elapsed % frameDuration) / frameDuration;
  const fade = canvasMode === "videoFrames" ? 0 : Math.min(1, Math.max(0, (local - 0.72) / 0.28));

  ctx.clearRect(0, 0, rect.width, rect.height);
  drawImageCover(ctx, loadedImages[index], rect.width, rect.height, 1);
  if (fade > 0) {
    drawImageCover(ctx, loadedImages[nextIndex], rect.width, rect.height, fade);
  }

  const pulse = 0.18 + Math.sin(elapsed * 1.3) * 0.05;
  const vignette = ctx.createRadialGradient(rect.width / 2, rect.height / 2, rect.width * 0.1, rect.width / 2, rect.height / 2, rect.width * 0.75);
  vignette.addColorStop(0, `rgba(124,58,237,${pulse})`);
  vignette.addColorStop(0.58, "rgba(10,10,26,0.06)");
  vignette.addColorStop(1, "rgba(0,0,0,0.58)");
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, rect.width, rect.height);

  animationId = requestAnimationFrame(() => drawCanvas(canvas, startTime));
}

function drawImageCover(ctx, image, width, height, alpha) {
  const scale = Math.max(width / image.width, height / image.height);
  const drawWidth = image.width * scale;
  const drawHeight = image.height * scale;
  ctx.globalAlpha = alpha;
  ctx.drawImage(image, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
  ctx.globalAlpha = 1;
}

function scheduleFlash(overlay, phrases) {
  clearTimeout(flashTimer);
  if (!phrases?.length || paused) {
    flashTimer = setTimeout(() => scheduleFlash(overlay, phrases), 1200);
    return;
  }
  const delay = 2000 + Math.random() * 4500;
  flashTimer = setTimeout(() => {
    const [left, top] = randomItem(positions);
    overlay.textContent = randomItem(phrases);
    overlay.style.left = `${left}%`;
    overlay.style.top = `${top}%`;
    overlay.style.fontSize = randomItem(["12px", "14px", "17px", "20px"]);
    overlay.style.color = randomItem([
      "rgba(255,255,255,0.23)",
      "rgba(221,214,254,0.2)",
      "rgba(245,208,254,0.17)",
    ]);
    overlay.classList.add("show");
    setTimeout(() => overlay.classList.remove("show"), 70);
    scheduleFlash(overlay, phrases);
  }, delay);
}

export async function playDream(dream) {
  stopDream();
  currentDream = dream;
  paused = false;
  canvasMode = "images";
  const video = document.getElementById("dreamVideo");
  const canvas = document.getElementById("dreamCanvas");
  const overlay = document.getElementById("flashOverlay");

  let frameUrls = Array.isArray(dream.frameUrls) ? dream.frameUrls : [];
  if (dream.resultType === "video" && !frameUrls.length) {
    frameUrls = await fetchVideoFrames(dream.mediaUrls?.[0]);
    dream.frameUrls = frameUrls;
  }

  const canvasUrls = dream.resultType === "video" ? frameUrls : dream.mediaUrls;
  const useCanvas = dream.resultType !== "video" || canvasUrls.length > 0;

  video.classList.toggle("hidden", useCanvas);
  canvas.classList.toggle("hidden", !useCanvas);

  if (useCanvas) {
    try {
      loadedImages = await preload(canvasUrls, dream.resultType === "video");
      if (!loadedImages.length) {
        throw new Error("Geen videoframes geladen.");
      }
      canvasMode = dream.resultType === "video" ? "videoFrames" : "images";
      resizeTarget = canvas;
      resizeCanvas(canvas);
      window.addEventListener("resize", handleResize);
      animationId = requestAnimationFrame(() => drawCanvas(canvas, performance.now()));
    } catch {
      if (dream.resultType !== "video") {
        throw new Error("De beelden konden niet worden geladen.");
      }
      canvas.classList.add("hidden");
      video.classList.remove("hidden");
      video.src = dream.mediaUrls[0];
      video.currentTime = 0;
      await video.play().catch(() => {});
    }
  } else if (dream.resultType === "video") {
    video.src = dream.mediaUrls[0];
    video.currentTime = 0;
    await video.play().catch(() => {});
  }
  scheduleFlash(overlay, dream.transformed?.phrases || []);
}

export function togglePause() {
  paused = !paused;
  const video = document.getElementById("dreamVideo");
  if (video.src) {
    if (paused) video.pause();
    else video.play().catch(() => {});
  }
  return paused;
}

export function stopDream() {
  cancelAnimationFrame(animationId);
  clearTimeout(flashTimer);
  animationId = null;
  flashTimer = null;
  loadedImages = [];
  resizeTarget = null;
  window.removeEventListener("resize", handleResize);
  const video = document.getElementById("dreamVideo");
  video.pause();
  video.removeAttribute("src");
  video.load();
}
