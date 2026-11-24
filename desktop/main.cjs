const { app, BrowserWindow, shell, ipcMain } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");

const DEV_BACKEND_PORT = 8765;
const isDev = Boolean(process.env.TITLESEARCH_DEV);

let mainWindow = null;
let backendProcess = null;
let backendPort = DEV_BACKEND_PORT;

function backendRoot() {
  return path.resolve(__dirname, "..", "..", "backend");
}

function resolvePython() {
  const venvWin = path.join(backendRoot(), ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvWin)) return venvWin;
  return process.env.TITLESEARCH_PYTHON || "python";
}

function dataDir() {
  if (process.env.TITLESEARCH_DATA_DIR) {
    return path.resolve(process.env.TITLESEARCH_DATA_DIR);
  }
  const local = process.env.LOCALAPPDATA || path.join(app.getPath("home"), "AppData", "Local");
  return path.join(local, "TitleSearch");
}

function healthUrl(port) {
  return `http://127.0.0.1:${port}/api/v1/health/live`;
}

function waitForHealth(port, attempts = 60) {
  return new Promise((resolve, reject) => {
    let left = attempts;
    const tick = () => {
      const req = http.get(healthUrl(port), (res) => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else if (--left > 0) setTimeout(tick, 500);
        else reject(new Error("Backend health check failed"));
      });
      req.on("error", () => {
        if (--left > 0) setTimeout(tick, 500);
        else reject(new Error("Backend did not start"));
      });
      req.setTimeout(1500, () => {
        req.destroy();
        if (--left > 0) setTimeout(tick, 500);
        else reject(new Error("Backend health timeout"));
      });
    };
    tick();
  });
}

function startBackend() {
  if (isDev) {
    return Promise.resolve();
  }
  const python = resolvePython();
  const env = {
    ...process.env,
    DESKTOP_MODE: "1",
    TITLESEARCH_DATA_DIR: dataDir(),
  };
  backendProcess = spawn(
    python,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", `--port=${backendPort}`],
    {
      cwd: backendRoot(),
      env,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  backendProcess.stdout.on("data", (d) => console.log(String(d)));
  backendProcess.stderr.on("data", (d) => console.error(String(d)));
  return waitForHealth(backendPort);
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill("SIGTERM");
    backendProcess = null;
  }
}

function createWindow() {
  const apiBaseUrl = `http://127.0.0.1:${backendPort}/api/v1`;
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--api-base=${apiBaseUrl}`, `--data-dir=${dataDir()}`],
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());

  if (isDev) {
    mainWindow.loadURL("http://127.0.0.1:5173");
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    const indexHtml = path.join(process.resourcesPath, "frontend", "index.html");
    mainWindow.loadFile(indexHtml);
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(async () => {
  try {
    if (!isDev) {
      fs.mkdirSync(dataDir(), { recursive: true });
      await startBackend();
    } else {
      await waitForHealth(DEV_BACKEND_PORT);
      backendPort = DEV_BACKEND_PORT;
    }
    createWindow();
  } catch (err) {
    console.error(err);
    app.quit();
  }
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => stopBackend());

ipcMain.handle("get-api-base-url", () => `http://127.0.0.1:${backendPort}/api/v1`);
ipcMain.handle("get-data-dir", () => dataDir());
ipcMain.handle("open-external", (_evt, url) => {
  if (typeof url === "string" && /^https?:\/\//i.test(url)) {
    return shell.openExternal(url);
  }
  return false;
});