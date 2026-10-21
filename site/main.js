// medNAMA product page: nav on scroll, the ask box typing example questions, the scroll-played demo,
// the live server status, launch progress, and sections that rise in.
(() => {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Nav turns frosted once the painting is behind you; the painting drifts a little slower than the page.
  const nav = document.querySelector(".nav");
  const art = document.querySelector(".hero-art");
  const onScroll = () => {
    nav.classList.toggle("scrolled", window.scrollY > 40);
    if (art && !reduced && window.scrollY < 1000) art.style.setProperty("--hy", (window.scrollY * 0.25).toFixed(1));
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // The ask box types an example for each mode in turn and lights its chip.
  const asks = [
    "A 2-year-old with fever for six days, red eyes and cracked lips. What must I not miss?",
    "Explain the Frank–Starling law, and show me the page it’s on.",
    "Quiz me on the brachial plexus, one question at a time.",
    "Give me a station: counselling a mother after her child’s first febrile seizure.",
  ];
  const text = document.querySelector("[data-ask-text]");
  const chips = [...document.querySelectorAll("[data-chip]")];
  const light = (k) => chips.forEach((c) => c.classList.toggle("on", Number(c.dataset.chip) === k));
  if (text) {
    light(0);
    if (!reduced) {
      let k = 0;
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      (async function loop() {
        for (;;) {
          await wait(3200);
          for (let n = text.textContent.length; n >= 0; n -= 3) { text.textContent = text.textContent.slice(0, n); await wait(12); }
          k = (k + 1) % asks.length; light(k);
          for (let n = 1; n <= asks[k].length; n++) { text.textContent = asks[k].slice(0, n); await wait(24 + Math.random() * 30); }
        }
      })();
    }
  }

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
    const fills = document.querySelectorAll("[data-launch-fill]");
    const fill = (el) => { el.style.width = `${pct}%`; };
    if ("IntersectionObserver" in window && !reduced) {
      const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { fill(e.target); io.unobserve(e.target); } }));
      fills.forEach((el) => io.observe(el));
    } else fills.forEach(fill);
  }

  // Sections rise in once.
  const items = document.querySelectorAll(".section .ed, .split p, .bento .tile, .row-facts, .quote, .film-frame, .status-card, .steps li, .launch, .faq details, .closing-inner");
  if (!reduced && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px" });
    items.forEach((el) => { el.classList.add("rise"); io.observe(el); });
  }
})();

// The demo window rises onto the painted backdrop, then the conversation plays as you keep scrolling.
(() => {
  const section = document.querySelector(".reveal");
  const win = document.querySelector("[data-reveal-win]");
  if (!section || !win) return;
  const steps = [...win.querySelectorAll("[data-step]")];
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { steps.forEach((m) => m.classList.add("on")); return; }
  const paint = () => {
    const r = section.getBoundingClientRect();
    const total = r.height - window.innerHeight;
    const p = Math.min(1, Math.max(0, -r.top / Math.max(1, total)));
    const rise = Math.min(1, Math.max(0, (window.innerHeight - r.top) / (window.innerHeight * 1.1)));
    win.style.setProperty("--r", (1 - Math.pow(1 - rise, 3)).toFixed(3));
    steps.forEach((m) => { const k = Number(m.dataset.step); m.classList.toggle("on", p >= 0.06 + (k - 1) * 0.17); });
  };
  let ticking = false;
  window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(() => { paint(); ticking = false; }); } }, { passive: true });
  window.addEventListener("resize", paint);
  paint();
})();

// Is the app up right now? Load its icon: when the server is down, Cloudflare answers with an error page, not an
// image, so the icon only loads while the app itself is serving. No cross-site request or credentials involved.
(() => {
  const APP = "https://mednama.rekorex.online";
  const dots = document.querySelectorAll("[data-status-dot]");
  const label = document.querySelector("[data-status-label]");
  const checked = document.querySelector("[data-status-checked]");
  const badge = document.querySelector("[data-status-badge]");
  const card = document.querySelector("[data-status-card]");
  const open = document.querySelector("[data-open-app]");
  const set = (state) => {
    dots.forEach((d) => { d.classList.toggle("online", state === "online"); d.classList.toggle("offline", state === "offline"); });
    if (card) card.dataset.state = state;
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (label) label.textContent = state === "online" ? "Online now" : "Offline right now";
    if (checked) checked.textContent = state === "online" ? `Checked at ${time} · rechecks every minute` : `Checked at ${time} · probably maintenance, try again later`;
    if (badge) badge.textContent = state === "online" ? "Invite-only early access · online now" : "Invite-only early access · offline right now";
    if (open) open.firstChild.textContent = state === "online" ? "Open medNAMA " : "Try opening anyway ";
  };
  const check = () => {
    const img = new Image();
    let done = false;
    const finish = (state) => { if (!done) { done = true; set(state); } };
    const timer = setTimeout(() => finish("offline"), 8000);
    img.onload = () => { clearTimeout(timer); finish("online"); };
    img.onerror = () => { clearTimeout(timer); finish("offline"); };
    img.src = `${APP}/icons/icon-192.png?status=${Date.now()}`;
  };
  check();
  setInterval(check, 60000);
})();
