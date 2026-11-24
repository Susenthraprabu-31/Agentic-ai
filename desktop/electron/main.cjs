const { app, BrowserWindow, shell, ipcMain, dialog } = require("electron");
const path = require("path");
const { spawn, execFileSync } = require("child_process");
const http = require("http");
const fs = require("fs");

const DEV_BACKEND_PORT = Number(process.env.TITLESEARCH_BACKEND_PORT || 18765);
const isDev = Boolean(process.env.TITLESEARCH_DEV);

let mainWindow = null;
let backendProcess = null;
let backendPort = DEV_BACKEND_PORT;

function backendRoot() {
  if (isDev) {
    return path.resolve(__dirname, "..", "..", "backend");
  }
  return path.join(process.resourcesPath, "backend");
}

function dataDir() {
  if (process.env.TITLESEARCH_DATA_DIR) {
    return path.resolve(process.env.TITLESEARCH_DATA_DIR);
  }
  const local = process.env.LOCALAPPDATA || path.join(app.getPath("home"), "AppData", "Local");
  return path.join(local, "TitleSearch");
}

function packagedEmbedRoot() {
  return path.join(process.resourcesPath, "python", "embed");
}

function bundledEmbedPython() {
  const scriptsPy = path.join(packagedEmbedRoot(), "Scripts", "python.exe");
  if (fs.existsSync(scriptsPy)) return scriptsPy;
  const basePy = path.join(packagedEmbedRoot(), "python-base", "python.exe");
  if (fs.existsSync(basePy)) return basePy;
  return path.join(packagedEmbedRoot(), "python.exe");
}

/** venv pyvenv.cfg uses relative home; patch to absolute path under install dir. */
function fixPackagedVenvConfig() {
  const embedRoot = packagedEmbedRoot();
  const cfgPath = path.join(embedRoot, "pyvenv.cfg");
  const baseHome = path.join(embedRoot, "python-base");
  const scriptsPy = path.join(embedRoot, "Scripts", "python.exe");
  if (!fs.existsSync(cfgPath) || !fs.existsSync(baseHome) || !fs.existsSync(scriptsPy)) {
    return;
  }
  let version = "3.13.0";
  try {
    const cfg = fs.readFileSync(cfgPath, "utf8");
    const m = cfg.match(/^version\s*=\s*(.+)$/m);
    if (m) version = m[1].trim();
  } catch {
    /* ignore */
  }
  const lines = [
    `home = ${baseHome}`,
    "include-system-site-packages = false",
    `version = ${version}`,
    `executable = ${scriptsPy}`,
  ];
  fs.writeFileSync(cfgPath, `${lines.join("\n")}\n`, "utf8");
}

function runtimeVenvPython() {
  return path.join(dataDir(), "runtime", "venv", "Scripts", "python.exe");
}

function resolveDevPython() {
  const venvWin = path.join(backendRoot(), ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvWin)) return venvWin;
  return process.env.TITLESEARCH_PYTHON || "python";
}

function findSystemPython() {
  const found = [];
  const local = process.env.LOCALAPPDATA;
  if (local) {
    const prog = path.join(local, "Programs", "Python");
    if (fs.existsSync(prog)) {
      for (const name of fs.readdirSync(prog)) {
        found.push(path.join(prog, name, "python.exe"));
      }
    }
  }
  const programFiles = process.env.ProgramFiles;
  if (programFiles) {
    const pfPy = path.join(programFiles, "Python313", "python.exe");
    if (fs.existsSync(pfPy)) found.push(pfPy);
  }
  for (const cmd of ["py", "python", "python3"]) {
    try {
      const line = execFileSync("where.exe", [cmd], { encoding: "utf8" })
        .trim()
        .split(/\r?\n/)[0];
      if (line) found.push(line);
    } catch {
      /* not on PATH */
    }
  }
  for (const candidate of found) {
    if (candidate && fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function ensureProductionRuntime() {
  fixPackagedVenvConfig();
  const bundled = bundledEmbedPython();
  if (fs.existsSync(bundled)) {
    return bundled;
  }

  const venvPy = runtimeVenvPython();
  if (fs.existsSync(venvPy)) {
    return venvPy;
  }

  const systemPy = findSystemPython();
  if (!systemPy) {
    throw new Error(
      "Python 3.11+ is required but was not found on this PC.\n\n" +
        "Install from https://www.python.org/downloads/ (check \"Add python.exe to PATH\"), " +
        "then open Title Search again."
    );
  }

  const bootstrap = path.join(process.resourcesPath, "scripts", "bootstrap-backend.ps1");
  if (!fs.existsSync(bootstrap)) {
    throw new Error(`Installer script missing: ${bootstrap}`);
  }
  const backendSource = backendRoot();
  const venvDir = path.join(dataDir(), "runtime", "venv");
  fs.mkdirSync(path.dirname(venvDir), { recursive: true });

  execFileSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      bootstrap,
      "-BackendSource",
      backendSource,
      "-VenvDir",
      venvDir,
      "-SystemPython",
      systemPy,
    ],
    { stdio: "inherit", windowsHide: false }
  );

  if (!fs.existsSync(venvPy)) {
    throw new Error("Backend setup did not complete. Check your network connection and try again.");
  }
  return venvPy;
}

function resolveProductionPython() {
  try {
    return ensureProductionRuntime();
  } catch (err) {
    dialog.showErrorBox("Title Search — setup required", String(err.message || err));
    throw err;
  }
}

function healthUrl(port) {
  return `http://127.0.0.1:${port}/api/v1/health/live`;
}

function waitForHealth(port, attempts = 120) {
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

  const python = resolveProductionPython();
  const logsDir = path.join(dataDir(), "logs");
  fs.mkdirSync(logsDir, { recursive: true });
  const logPath = path.join(logsDir, "backend.log");
  const logStream = fs.createWriteStream(logPath, { flags: "a" });
  logStream.write(`\n--- backend start ${new Date().toISOString()} ---\n`);

  const env = {
    ...process.env,
    DESKTOP_MODE: "1",
    DESKTOP_USE_SQLITE: process.env.DESKTOP_USE_SQLITE || "1",
    TITLESEARCH_DATA_DIR: dataDir(),
    TITLESEARCH_BACKEND_PORT: String(backendPort),
    TITLESEARCH_UVICORN_RELOAD: "0",
    PYTHONNOUSERSITE: "1",
    PLAYWRIGHT_BROWSERS_PATH: path.join(dataDir(), "ms-playwright"),
  };

  return new Promise((resolve, reject) => {
    backendProcess = spawn(python, ["-m", "app.desktop_server"], {
      cwd: backendRoot(),
      env,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    backendProcess.on("error", (err) => {
      dialog.showErrorBox(
        "Title Search — backend error",
        err.code === "ENOENT"
          ? `Could not run Python at:\n${python}`
          : String(err.message || err)
      );
      reject(err);
    });

    backendProcess.stdout.on("data", (d) => {
      const s = String(d);
      console.log(s);
      logStream.write(s);
    });
    backendProcess.stderr.on("data", (d) => {
      const s = String(d);
      console.error(s);
      logStream.write(s);
    });
    backendProcess.on("exit", (code) => {
      if (code && code !== 0) {
        console.error("Backend exited with code", code);
      }
    });

    waitForHealth(backendPort).then(resolve).catch((err) => {
      dialog.showErrorBox(
        "Title Search — backend did not start",
        `${err.message}\n\nLog: ${logPath}`
      );
      reject(err);
    });
  });
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
    const viteUrl = process.env.TITLESEARCH_VITE_URL || "http://127.0.0.1:5173";
    mainWindow.loadURL(viteUrl);
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