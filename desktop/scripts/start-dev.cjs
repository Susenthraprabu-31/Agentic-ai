#!/usr/bin/env node
/** Pick dev backend port once, then run backend + frontend + electron with matching URLs. */

const path = require("path");
const concurrently = require("concurrently");
const {
  pickDevBackendPort,
  writeDevBackendPort,
  DEFAULT_PORT,
} = require("./dev-port.cjs");

const desktopDir = path.resolve(__dirname, "..");

async function main() {
  const preferred = Number(
    process.env.TITLESEARCH_BACKEND_PORT || DEFAULT_PORT
  );
  let port;
  try {
    port = await pickDevBackendPort(preferred);
  } catch (err) {
    console.error("[desktop]", err.message);
    process.exit(1);
  }
  writeDevBackendPort(port);

  const origin = `http://127.0.0.1:${port}`;
  const api = `${origin}/api/v1`;
  console.log("[desktop] dev stack using backend", origin);

  const env = {
    ...process.env,
    TITLESEARCH_BACKEND_PORT: String(port),
  };

  const frontendCmd = `cross-env VITE_DESKTOP_DEV=1 VITE_DEV_API_PROXY=${origin} VITE_API_URL=${api} npm run dev --prefix ../frontend`;
  const electronCmd = `wait-on http-get://127.0.0.1:${port}/api/v1/health/live http-get://127.0.0.1:5173/ && cross-env TITLESEARCH_DEV=1 TITLESEARCH_BACKEND_PORT=${port} TITLESEARCH_VITE_URL=http://127.0.0.1:5173 electron .`;

  const { result } = concurrently(
    [
      { command: "npm run dev:backend", name: "backend", cwd: desktopDir },
      { command: frontendCmd, name: "frontend", cwd: desktopDir },
      { command: electronCmd, name: "electron", cwd: desktopDir },
    ],
    {
      cwd: desktopDir,
      env,
      killOthersOn: "failure",
      raw: false,
    }
  );

  try {
    await result;
    process.exit(0);
  } catch {
    process.exit(1);
  }
}

main();