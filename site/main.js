// medNAMA product page: nav on scroll, feature tabs, words that light up, sections that rise in.
(() => {
  const nav = document.querySelector(".nav");
  const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 20);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Feature tabs (arrow keys move between tabs).
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
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

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
  const items = document.querySelectorAll(".split-head, .tabs, .card, .numbers, .plan, .faq details, .closing-inner");
  if (!reduced && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px" });
    items.forEach((el) => { el.classList.add("rise"); io.observe(el); });
  }
})();
