const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  // Service management
  getStatus: () => ipcRenderer.invoke("get-status"),
  startService: (id) => ipcRenderer.invoke("start-service", id),
  stopService: (id) => ipcRenderer.invoke("stop-service", id),
  startCore: () => ipcRenderer.invoke("start-core"),
  startAll: () => ipcRenderer.invoke("start-all"),
  stopAll: () => ipcRenderer.invoke("stop-all"),

  // NAS operations
  getNasStatus: () => ipcRenderer.invoke("get-nas-status"),
  syncNas: (dryRun) => ipcRenderer.invoke("sync-nas", dryRun),
  deployNasImages: () => ipcRenderer.invoke("deploy-nas-images"),

  // Cloudflare operations
  getCloudflareStatus: () => ipcRenderer.invoke("get-cloudflare-status"),
  saveCloudflareToken: (token) => ipcRenderer.invoke("save-cloudflare-token", token),

  // Logs & Telemetry
  getLogs: () => ipcRenderer.invoke("get-logs"),

  // System & Window controls
  launchExe: (appName) => ipcRenderer.invoke("launch-exe", appName),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  windowMinimize: () => ipcRenderer.invoke("window-minimize"),
  windowMaximize: () => ipcRenderer.invoke("window-maximize"),
  windowClose: () => ipcRenderer.invoke("window-close"),
});

