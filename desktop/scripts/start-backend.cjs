#!/usr/bin/env node
/** Spawn the FastAPI backend for desktop dev (Windows-safe paths). */

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const {
  pickDevBackendPort,
  writeDevBackendPort,
  readDevBackendPort,
  DEFAULT_PORT,
} = require("./dev-port.cjs");

const desktopDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopDir, "..");
const backendDir = path.join(repoRoot, "backend");
const dataDir = path.join(repoRoot, "local_data");

const venvPython = path.join(backendDir, ".venv", "Scripts", "python.exe");
const python = fs.existsSync(venvPython) ? venvPython : process.env.TITLESEARCH_PYTHON || "python";

async function resolvePort() {
  if (process.env.TITLESEARCH_BACKEND_PORT) {
    return String(process.env.TITLESEARCH_BACKEND_PORT);
  }
  const fromFile = readDevBackendPort(0);
  if (fromFile > 0) {
    return String(fromFile);
  }
  const picked = await pickDevBackendPort(DEFAULT_PORT);
  writeDevBackendPort(picked);
  return String(picked);
}

function baseEnv(backendPort) {
  return {
    ...process.env,
    DESKTOP_MODE: "1",
    DESKTOP_USE_SQLITE: process.env.DESKTOP_USE_SQLITE || "0",
    TITLESEARCH_DATA_DIR: process.env.TITLESEARCH_DATA_DIR || dataDir,
    TITLESEARCH_BACKEND_PORT: backendPort,
  };
}

async function run() {
  const port = await resolvePort();
  writeDevBackendPort(Number(port));
  const env = baseEnv(port);

  console.log("[desktop] backend cwd:", backendDir);
  console.log("[desktop] python:", python);
  console.log("[desktop] data dir:", env.TITLESEARCH_DATA_DIR);
  console.log("[desktop] port:", port);

  const child = spawn(python, ["-m", "app.desktop_server"], {
    cwd: backendDir,
    env,
    stdio: "inherit",
    shell: false,
  });

  child.on("error", (err) => {
    console.error("[desktop] failed to start backend:", err.message);
    process.exit(1);
  });

  child.on("exit", (code) => {
    process.exit(code ?? 0);
  });
}

run().catch((err) => {
  console.error("[desktop] backend port resolution failed:", err.message);
  process.exit(1);
});