// medNAMA product page: nav on scroll, feature tabs, launch status, words that light up, screens that tilt
// into place as they scroll through, sections that rise in.
(() => {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const nav = document.querySelector(".nav");
  const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 20);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Launch status from launch.js.
  const L = window.MEDNAMA_LAUNCH;
  if (L) {
    const total = L.steps.reduce((a, s) => a + s.weight, 0);
    const pct = Math.round(L.steps.reduce((a, s) => a + s.weight * s.progress, 0) / total * 100);
    document.querySelectorAll("[data-launch-pct]").forEach((el) => { el.textContent = `${pct}%`; });
    document.querySelectorAll("[data-launch-bar]").forEach((el) => el.setAttribute("aria-valuenow", String(pct)));
    const list = document.querySelector("[data-launch-steps]");
    if (list) {
      list.innerHTML = L.steps.map((s) => {
        const state = s.progress >= 1 ? "done" : s.progress > 0 ? "now" : "next";
        const label = state === "done" ? "Done" : state === "now" ? "In progress" : "Up next";
        return `<li class="${state}" style="--w:${Math.round(s.progress * 100)}%"><span class="st">${label}</span>` +
          `<h4>${s.title}</h4><p>${s.detail}</p></li>`;
      }).join("");
      const note = document.createElement("p");
      note.className = "launch-updated";
      note.textContent = `Updated ${L.updated}.`;
      list.after(note);
    }
    // Fill the bars once they are on screen.
    const fills = document.querySelectorAll("[data-launch-fill]");
    const fill = (el) => { el.style.width = `${pct}%`; };
    if ("IntersectionObserver" in window && !reduced) {
      const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { fill(e.target); io.unobserve(e.target); } }));
      fills.forEach((el) => io.observe(el));
    } else fills.forEach(fill);
  }

  // Feature tabs (arrow keys move between tabs).
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
    requestAnimationFrame(paintTilt);
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t));
    t.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      select(next);
      next.focus();
    });
  });

  // Screens tilt into place as they pass through the viewport (--p: 0 entering, 0.5 centre, 1 leaving),
  // and lean slightly toward the pointer.
  const tilted = [...document.querySelectorAll("[data-tilt]")];
  tilted.forEach((el) => { if (el.dataset.depth) el.style.setProperty("--depth", el.dataset.depth); });
  function paintTilt() {
    if (reduced) return;
    const vh = window.innerHeight;
    for (const el of tilted) {
      const r = el.getBoundingClientRect();
      if (r.bottom < -200 || r.top > vh + 200) continue;
      const p = el.dataset.tilt === "hero"
        ? Math.min(1, Math.max(0, 1 - (r.top - vh * 0.25) / (vh * 0.6)))
        : Math.min(1, Math.max(0, (vh - r.top) / (vh + r.height)));
      el.style.setProperty("--p", p.toFixed(3));
    }
  }
  if (!reduced) {
    let ticking = false;
    window.addEventListener("scroll", () => {
      if (!ticking) { ticking = true; requestAnimationFrame(() => { paintTilt(); ticking = false; }); }
    }, { passive: true });
    window.addEventListener("resize", paintTilt);
    paintTilt();
    document.querySelectorAll(".scene").forEach((scene) => {
      scene.addEventListener("pointermove", (e) => {
        const r = scene.getBoundingClientRect();
        const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
        scene.querySelectorAll("[data-tilt]").forEach((w) => {
          w.style.setProperty("--mx", `${(x * 6).toFixed(2)}deg`);
          w.style.setProperty("--my", `${(-y * 5).toFixed(2)}deg`);
        });
      });
      scene.addEventListener("pointerleave", () => scene.querySelectorAll("[data-tilt]").forEach((w) => {
        w.style.setProperty("--mx", "0deg"); w.style.setProperty("--my", "0deg");
      }));
    });
  }

  // The wall: duplicate each row so the drift loops without a seam.
  document.querySelectorAll(".wall-row").forEach((row) => { row.innerHTML += row.innerHTML; });

  // The statement: each word brightens as the paragraph scrolls through the middle of the screen.
  const para = document.querySelector(".reveal-words");
  if (para && !reduced) {
    para.innerHTML = para.textContent.trim().split(/\s+/).map((w) => `<span class="w">${w}</span> `).join("");
    const words = [...para.querySelectorAll(".w")];
    const paint = () => {
      const r = para.getBoundingClientRect();
      const vh = window.innerHeight;
      const p = Math.min(1, Math.max(0, (vh * 0.85 - r.top) / (r.height + vh * 0.45)));
      const n = Math.round(p * words.length);
      words.forEach((w, i) => w.classList.toggle("on", i < n));
    };
    window.addEventListener("scroll", paint, { passive: true });
    paint();
  }

  // Sections rise in once.
  const items = document.querySelectorAll(".split-head, .tabs, .card, .numbers, .launch, .faq details, .closing-inner");
  if (!reduced && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px" });
    items.forEach((el) => { el.classList.add("rise"); io.observe(el); });
  }
})();
