export function initBackground(canvas) {
  const ctx = canvas.getContext("2d");
  const particles = Array.from({ length: 40 }, (_, index) => ({
    x: Math.random(),
    y: Math.random(),
    radius: 1.2 + Math.random() * 2.8,
    hue: 250 + Math.random() * 60,
    speed: 0.00014 + Math.random() * 0.00028,
    phase: index * 0.37,
  }));

  function resize() {
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(window.innerWidth * ratio);
    canvas.height = Math.floor(window.innerHeight * ratio);
    canvas.style.width = `${window.innerWidth}px`;
    canvas.style.height = `${window.innerHeight}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function draw(time) {
    const width = window.innerWidth;
    const height = window.innerHeight;
    ctx.clearRect(0, 0, width, height);
    const gradient = ctx.createRadialGradient(width * 0.5, height * 0.15, 0, width * 0.5, height * 0.5, height);
    gradient.addColorStop(0, "rgba(124,58,237,0.12)");
    gradient.addColorStop(1, "rgba(10,10,26,0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    for (const particle of particles) {
      particle.y = (particle.y + particle.speed) % 1;
      const drift = Math.sin(time * 0.00025 + particle.phase) * 24;
      const x = particle.x * width + drift;
      const y = particle.y * height;
      ctx.beginPath();
      ctx.fillStyle = `hsla(${particle.hue}, 88%, 72%, 0.22)`;
      ctx.arc(x, y, particle.radius, 0, Math.PI * 2);
      ctx.fill();
    }
    requestAnimationFrame(draw);
  }

  resize();
  window.addEventListener("resize", resize);
  requestAnimationFrame(draw);
}
