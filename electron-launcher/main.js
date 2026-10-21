const { app, BrowserWindow, ipcMain, shell, Menu } = require("electron");
const path = require("path");
const http = require("http");
const { spawn } = require("child_process");

const ROOT_DIR = path.resolve(__dirname, "..");
const LAUNCHER_PORT = 8900;
let mainWindow = null;
let pythonProcess = null;

function findPython() {
  const fs = require("fs");
  const candidates = [
    path.join(ROOT_DIR, ".venv-gpu", "Scripts", "python.exe"),
    path.join(ROOT_DIR, ".venv", "Scripts", "python.exe"),
    path.join(ROOT_DIR, "venv", "Scripts", "python.exe"),
    "python",
  ];
  for (const c of candidates) {
    if (c === "python" || fs.existsSync(c)) {
      return c;
    }
  }
  return "python";
}

function checkLauncherHealth() {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${LAUNCHER_PORT}/api/status`, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(800, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function ensureBackendManager() {
  const alive = await checkLauncherHealth();
  if (alive) {
    console.log("medNAMA Python launcher hub is already online.");
    return;
  }

  const pyExe = findPython();
  console.log(`Starting background medNAMA python manager with ${pyExe}...`);
  pythonProcess = spawn(pyExe, ["launcher.py", "--web", "--port", String(LAUNCHER_PORT)], {
    cwd: ROOT_DIR,
    stdio: "ignore",
    detached: false,
    windowsHide: true,
  });

  pythonProcess.on("error", (err) => {
    console.error("Failed to start python launcher:", err);
  });

  // Wait for it to become ready
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 300));
    if (await checkLauncherHealth()) {
      console.log("Python manager is now online!");
      break;
    }
  }
}

function fetchJson(urlPath, method = "GET", postData = null) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: "127.0.0.1",
      port: LAUNCHER_PORT,
      path: urlPath,
      method: method,
      headers: postData ? { "Content-Type": "application/json" } : {},
      timeout: 5000,
    };

    const req = http.request(options, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve({ raw: body });
        }
      });
    });

    req.on("error", (e) => resolve({ error: e.message }));
    req.on("timeout", () => {
      req.destroy();
      resolve({ error: "Request timeout" });
    });

    if (postData) {
      req.write(JSON.stringify(postData));
    }
    req.end();
  });
}

function createWindow() {
  Menu.setApplicationMenu(null);

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 1024,
    minHeight: 700,
    frame: false,
    backgroundColor: "#070a12",
    title: "medNAMA Control Hub",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "index.html"));

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// IPC Handlers
ipcMain.handle("get-status", async () => {
  return await fetchJson("/api/status");
});

ipcMain.handle("start-service", async (_, id) => {
  return await fetchJson(`/api/service/start?id=${encodeURIComponent(id)}`, "POST");
});

ipcMain.handle("stop-service", async (_, id) => {
  return await fetchJson(`/api/service/stop?id=${encodeURIComponent(id)}`, "POST");
});

ipcMain.handle("start-core", async () => {
  return await fetchJson("/api/start-core", "POST");
});

ipcMain.handle("start-all", async () => {
  return await fetchJson("/api/start-all", "POST");
});

ipcMain.handle("stop-all", async () => {
  return await fetchJson("/api/stop-all", "POST");
});

ipcMain.handle("sync-nas", async (_, dryRun) => {
  return await fetchJson(`/api/nas-sync?dry_run=${Boolean(dryRun)}`, "POST");
});

ipcMain.handle("deploy-nas-images", async () => {
  return await fetchJson("/api/nas-deploy-images", "POST");
});

ipcMain.handle("get-nas-status", async () => {
  return await fetchJson("/api/nas-status");
});

ipcMain.handle("get-logs", async () => {
  return await fetchJson("/api/logs");
});

ipcMain.handle("launch-exe", async (_, appName) => {
  const fs = require("fs");
  const exePath = path.join(ROOT_DIR, "app-template", "dist", appName, `${appName}.exe`);
  if (fs.existsSync(exePath)) {
    spawn(exePath, [], { detached: true, stdio: "ignore" }).unref();
    return { ok: true, path: exePath };
  }
  const pyExe = findPython();
  const scriptMap = {
    "Launch-medNAMA": "launch_mednama.py",
    "Launch-RAG-Lab": "launch_rag_lab.py",
    "Launch-Seed-Studio": "launch_seed_studio.py",
    "Transfer-to-NAS": "transfer_to_nas.py",
  };
  const scriptName = scriptMap[appName];
  if (scriptName) {
    const scriptPath = path.join(ROOT_DIR, "app-template", scriptName);
    spawn(pyExe, [scriptPath], { detached: true, stdio: "ignore" }).unref();
    return { ok: true, fallback: true, path: scriptPath };
  }
  return { ok: false, error: "App not found" };
});

ipcMain.handle("open-external", async (_, url) => {
  if (url && typeof url === "string") {
    await shell.openExternal(url);
    return true;
  }
  return false;
});

ipcMain.handle("window-minimize", () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.handle("window-maximize", () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.handle("window-close", () => {
  if (mainWindow) mainWindow.close();
});

ipcMain.handle("get-cloudflare-status", async () => {
  return {
    tunnelConfigured: true,
    serviceTarget: "http://frontend:3000",
    nasHost: "192.168.1.42",
    networkAlias: "frontend / mednama-frontend",
  };
});

ipcMain.handle("save-cloudflare-token", async (_, token) => {
  try {
    const pyExe = findPython();
    const scriptPath = path.join(ROOT_DIR, "_local", "nas_ssh.py");
    const cmd = `sed -i "s|^CLOUDFLARE_TUNNEL_TOKEN=.*|CLOUDFLARE_TUNNEL_TOKEN=${token.trim()}|" /DATA/mednama/app/.env && cd /DATA/mednama/app && docker compose --profile cloudflare up -d cloudflared`;
    
    return new Promise((resolve) => {
      const proc = spawn(pyExe, [scriptPath, cmd], { cwd: ROOT_DIR });
      let out = "";
      proc.stdout.on("data", (d) => (out += d.toString()));
      proc.on("close", (code) => {
        resolve({ ok: code === 0, output: out });
      });
    });
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

app.whenReady().then(async () => {
  await ensureBackendManager();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (pythonProcess) {
    try {
      pythonProcess.kill();
    } catch {}
  }
  if (process.platform !== "darwin") {
    app.quit();
  }
});

