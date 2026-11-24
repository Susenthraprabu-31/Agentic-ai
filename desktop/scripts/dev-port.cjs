/** Resolve a free localhost port for desktop dev (avoids installed app on 18765). */
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

const desktopDir = path.resolve(__dirname, "..");
const PORT_FILE = path.join(desktopDir, ".dev-backend-port");
const DEFAULT_PORT = Number(process.env.TITLESEARCH_BACKEND_PORT_DEFAULT || 18765);
const HOST = "127.0.0.1";

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.on("error", () => resolve(false));
    server.listen({ port, host: HOST }, () => {
      server.close(() => resolve(true));
    });
  });
}

function probeHealth(port, timeoutMs = 1200) {
  return new Promise((resolve) => {
    const req = http.get(
      {
        host: HOST,
        port,
        path: "/api/v1/health/live",
        timeout: timeoutMs,
      },
      (res) => {
        res.resume();
        resolve(res.statusCode >= 200 && res.statusCode < 300);
      }
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

/**
 * Prefer `startPort`. If taken, scan up to `maxTries` consecutive ports.
 * (Do not reuse an occupied port even when /health/live responds — the installed
 * desktop app often holds 18765 and is not this dev backend.)
 */
async function pickDevBackendPort(startPort = DEFAULT_PORT, maxTries = 24) {
  const first = Number(startPort) || DEFAULT_PORT;
  if (await isPortFree(first)) {
    return first;
  }

  console.warn(
    `[desktop] Port ${first} is in use (often the installed Title Search app). Picking another port…`
  );
  for (let i = 1; i < maxTries; i += 1) {
    const candidate = first + i;
    if (await isPortFree(candidate)) {
      return candidate;
    }
  }
  throw new Error(
    `No free backend port found in range ${first}–${first + maxTries - 1}`
  );
}

function writeDevBackendPort(port) {
  fs.writeFileSync(PORT_FILE, String(port), "utf8");
}

function readDevBackendPort(fallback = DEFAULT_PORT) {
  try {
    const raw = fs.readFileSync(PORT_FILE, "utf8").trim();
    const n = Number(raw);
    if (Number.isFinite(n) && n > 0) return n;
  } catch {
    /* missing file */
  }
  return fallback;
}

module.exports = {
  DEFAULT_PORT,
  PORT_FILE,
  pickDevBackendPort,
  writeDevBackendPort,
  readDevBackendPort,
  isPortFree,
  probeHealth,
};