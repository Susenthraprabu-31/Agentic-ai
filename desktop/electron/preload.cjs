const { contextBridge, ipcRenderer } = require("electron");

function readArg(prefix) {
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : "";
}

const apiFromArgs = readArg("--api-base=");
const dataFromArgs = readArg("--data-dir=");

contextBridge.exposeInMainWorld("electronAPI", {
  getApiBaseUrl: () =>
    ipcRenderer
      .invoke("get-api-base-url")
      .catch(() => apiFromArgs || "http://127.0.0.1:18765/api/v1"),
  getDataDir: () => ipcRenderer.invoke("get-data-dir").catch(() => dataFromArgs),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  isDesktop: true,
});