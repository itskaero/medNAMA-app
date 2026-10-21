// renderer.js - medNAMA Electron Client Controller

const state = {
  currentTab: "services",
  services: [],
  logs: {},
  nasInfo: {},
  logFilter: "all",
  activeWebviewUrl: "http://192.168.1.42:3000",
};

// ─── Window Controls ──────────────────────────────────────────────────────────
document.getElementById("btnMinimize").addEventListener("click", () => window.api.windowMinimize());
document.getElementById("btnMaximize").addEventListener("click", () => window.api.windowMaximize());
document.getElementById("btnClose").addEventListener("click", () => window.api.windowClose());

// ─── Tab Switching ────────────────────────────────────────────────────────────
document.querySelectorAll(".nav-tab").forEach((tabBtn) => {
  tabBtn.addEventListener("click", () => {
    document.querySelectorAll(".nav-tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));

    tabBtn.classList.add("active");
    const targetId = `pane-${tabBtn.dataset.tab}`;
    const targetPane = document.getElementById(targetId);
    if (targetPane) {
      targetPane.classList.add("active");
      state.currentTab = tabBtn.dataset.tab;
    }
  });
});

// ─── Master Service Actions ───────────────────────────────────────────────────
document.getElementById("btnStartCore").addEventListener("click", async () => {
  showToast("Starting Core Services (DB, Backend, Frontend)...");
  await window.api.startCore();
  refreshStatus();
});

document.getElementById("btnStartAll").addEventListener("click", async () => {
  showToast("Starting All medNAMA Services...");
  await window.api.startAll();
  refreshStatus();
});

document.getElementById("btnStopAll").addEventListener("click", async () => {
  showToast("Stopping all services...");
  await window.api.stopAll();
  refreshStatus();
});

// ─── Service Grid Rendering ───────────────────────────────────────────────────
function renderServicesGrid(services) {
  const grid = document.getElementById("servicesGrid");
  if (!services || !services.length) {
    grid.innerHTML = `<div style="color:var(--text-muted); padding:20px;">No services reported by launcher.</div>`;
    return;
  }

  const categoryIcons = {
    core: "⚡",
    lab: "🧪",
    site: "🌐",
    infra: "🗄️",
  };

  grid.innerHTML = services.map((s) => {
    const isRunning = s.status === "running";
    const isExternal = s.status === "external";
    const isOnline = isRunning || isExternal;

    const pillClass = isRunning ? "running" : isExternal ? "external" : "stopped";
    const pillLabel = isRunning ? "Online" : isExternal ? "External" : "Stopped";
    const icon = categoryIcons[s.category] || "📦";

    return `
      <div class="service-card">
        <div>
          <div class="card-header">
            <div class="card-title-group">
              <div class="card-icon">${icon}</div>
              <div>
                <div class="card-name">${escapeHtml(s.name)}</div>
                <div class="card-port">Port :${s.port} ${s.pid ? `· PID ${s.pid}` : ""}</div>
              </div>
            </div>
            <span class="status-pill ${pillClass}">
              <span class="dot"></span> ${pillLabel}
            </span>
          </div>
          <div class="card-desc">${escapeHtml(s.description)}</div>
        </div>

        <div class="card-footer">
          <div class="card-controls">
            ${
              isOnline
                ? `<button class="btn btn-rose" onclick="handleServiceStop('${s.id}')">Stop</button>`
                : `<button class="btn btn-emerald" onclick="handleServiceStart('${s.id}')">Start</button>`
            }
            ${
              s.url
                ? `<button class="btn" onclick="openEmbedded('${s.url}')" title="Open inside Embedded Viewer">Embed</button>
                   <button class="btn" onclick="openExternal('${s.url}')" title="Open in browser">Browser</button>`
                : ""
            }
          </div>
        </div>
      </div>
    `;
  }).join("");
}

window.handleServiceStart = async (id) => {
  showToast(`Starting ${id}...`);
  await window.api.startService(id);
  refreshStatus();
};

window.handleServiceStop = async (id) => {
  showToast(`Stopping ${id}...`);
  await window.api.stopService(id);
  refreshStatus();
};

window.openEmbedded = (url) => {
  const frame = document.getElementById("embeddedFrame");
  if (frame) {
    frame.src = url;
    state.activeWebviewUrl = url;
  }
  // Switch to embedded tab
  const tabBtn = document.querySelector('[data-tab="embedded"]');
  if (tabBtn) tabBtn.click();
};

window.openExternal = (url) => {
  window.api.openExternal(url);
};

// ─── Status & Polling ─────────────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const data = await window.api.getStatus();
    if (data && data.services) {
      state.services = data.services;
      renderServicesGrid(data.services);
    }
    if (data && data.nas_log && data.nas_log.length) {
      state.logs["nas"] = data.nas_log;
    }
  } catch (err) {
    console.error("Status error:", err);
  }

  // Also fetch logs periodically
  try {
    const logs = await window.api.getLogs();
    if (logs) {
      state.logs = logs;
      updateTerminalFeed();
    }
  } catch {}
}

async function refreshNasTelemetry() {
  try {
    const data = await window.api.getNasStatus();
    if (data) {
      state.nasInfo = data;
      if (document.getElementById("nasActiveDb")) {
        document.getElementById("nasActiveDb").textContent = data.active_db || "medrag";
      }
      if (document.getElementById("nasTotalMcqs")) {
        document.getElementById("nasTotalMcqs").textContent = Number(data.total_mcqs || 76232).toLocaleString();
      }
      if (document.getElementById("nasPgetCount")) {
        document.getElementById("nasPgetCount").textContent = data.pget_count || 792;
      }
      if (document.getElementById("nasPortsStatus")) {
        const ssh = data.ssh_port_open ? "SSH: OK" : "SSH: Pending";
        const web = data.web_port_open ? "HTTP: 200" : "HTTP: Off";
        document.getElementById("nasPortsStatus").textContent = `${ssh} · ${web}`;
      }
    }
  } catch (err) {
    console.error("NAS telemetry error:", err);
  }
}

// ─── NAS & Cloudflare Tab Actions ─────────────────────────────────────────────
document.getElementById("btnCheckNas").addEventListener("click", () => {
  showToast("Refreshing NAS telemetry...");
  refreshNasTelemetry();
});

document.getElementById("btnOpenNasWeb").addEventListener("click", () => {
  window.api.openExternal("http://192.168.1.42:3000");
});

document.getElementById("btnSyncNasDry").addEventListener("click", async () => {
  showToast("Running NAS DB dry run check...");
  switchToTerminal();
  await window.api.syncNas(true);
});

document.getElementById("btnSyncNasFull").addEventListener("click", async () => {
  showToast("Initiating full database transfer to NAS...");
  switchToTerminal();
  await window.api.syncNas(false);
});

document.getElementById("btnDeployImages").addEventListener("click", async () => {
  showToast("Building & deploying images to NAS...");
  switchToTerminal();
  await window.api.deployNasImages();
});

document.getElementById("btnSaveCfToken").addEventListener("click", async () => {
  const token = document.getElementById("cfTokenInput").value;
  if (!token.trim()) {
    showToast("Please enter a valid Cloudflare Tunnel Token.");
    return;
  }
  showToast("Saving Cloudflare token to NAS .env and restarting tunnel...");
  switchToTerminal();
  const res = await window.api.saveCloudflareToken(token);
  if (res.ok) {
    showToast("Cloudflare Tunnel updated successfully!");
  } else {
    showToast(`Error saving token: ${res.error || "Failed"}`);
  }
});

// ─── PGET & Labs Actions ──────────────────────────────────────────────────────
document.getElementById("btnOpenPgetApp").addEventListener("click", () => {
  openEmbedded("http://192.168.1.42:3000/#/exams");
});

document.getElementById("btnOpenSeedStudio").addEventListener("click", () => {
  openEmbedded("http://localhost:8770");
});

document.getElementById("btnOpenRagLab").addEventListener("click", () => {
  openEmbedded("http://localhost:8765");
});

document.getElementById("btnOpenShowcase").addEventListener("click", () => {
  openEmbedded("http://localhost:8080");
});

// ─── Embedded Viewer Controls ─────────────────────────────────────────────────
const setFrame = (url) => {
  const frame = document.getElementById("embeddedFrame");
  if (frame) {
    frame.src = url;
    state.activeWebviewUrl = url;
  }
};

document.getElementById("wvLocalApp").addEventListener("click", () => setFrame("http://localhost:3000"));
document.getElementById("wvNasApp").addEventListener("click", () => setFrame("http://192.168.1.42:3000"));
document.getElementById("wvSeedStudio").addEventListener("click", () => setFrame("http://localhost:8770"));
document.getElementById("wvRagLab").addEventListener("click", () => setFrame("http://localhost:8765"));
document.getElementById("wvSite").addEventListener("click", () => setFrame("http://localhost:8080"));

document.getElementById("wvReload").addEventListener("click", () => {
  const frame = document.getElementById("embeddedFrame");
  if (frame) frame.src = frame.src;
});

document.getElementById("wvPopout").addEventListener("click", () => {
  window.api.openExternal(state.activeWebviewUrl || "http://192.168.1.42:3000");
});

// ─── Live Terminal Feed ───────────────────────────────────────────────────────
const terminalFeed = document.getElementById("terminalFeed");
const logFilter = document.getElementById("logFilter");

logFilter.addEventListener("change", (e) => {
  state.logFilter = e.target.value;
  updateTerminalFeed();
});

document.getElementById("btnClearLogs").addEventListener("click", () => {
  terminalFeed.textContent = "Feed cleared.\n";
});

document.getElementById("btnCopyLogs").addEventListener("click", () => {
  navigator.clipboard.writeText(terminalFeed.textContent);
  showToast("Logs copied to clipboard!");
});

function updateTerminalFeed() {
  if (!terminalFeed) return;
  const filter = state.logFilter;
  let lines = [];

  if (filter === "all") {
    for (const [key, list] of Object.entries(state.logs)) {
      if (Array.isArray(list)) {
        lines.push(...list.map((l) => `[${key.toUpperCase()}] ${l}`));
      }
    }
  } else {
    lines = (state.logs[filter] || []).map((l) => `[${filter.toUpperCase()}] ${l}`);
  }

  if (lines.length) {
    terminalFeed.textContent = lines.slice(-200).join("\n");
    terminalFeed.scrollTop = terminalFeed.scrollHeight;
  }
}

function switchToTerminal() {
  const tabBtn = document.querySelector('[data-tab="terminal"]');
  if (tabBtn) tabBtn.click();
}

function showToast(msg) {
  console.log("[TOAST]", msg);
  const headerPill = document.getElementById("nasHeaderStatus");
  if (headerPill) {
    const old = headerPill.innerHTML;
    headerPill.innerHTML = `<span class="meta-dot" style="background:var(--cyan);"></span> ${msg}`;
    setTimeout(() => {
      headerPill.innerHTML = old;
    }, 4000);
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ─── Initialization ───────────────────────────────────────────────────────────
refreshStatus();
refreshNasTelemetry();
setInterval(refreshStatus, 2500);
setInterval(refreshNasTelemetry, 8000);

