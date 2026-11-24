#!/usr/bin/env node
/**
 * Remove previous win-unpacked output before electron-builder.
 * On Windows, app.asar stays locked if a packaged app, dev Electron, or Explorer is open.
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const repoRoot = path.resolve(__dirname, "..", "..");
const winUnpacked = path.join(repoRoot, "dist-desktop", "win-unpacked");
const asarPath = path.join(winUnpacked, "resources", "app.asar");

function sleepMs(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    /* wait */
  }
}

function tryClosePackagedApp() {
  if (process.platform !== "win32") {
    return;
  }
  const exe = path.join(winUnpacked, "Title Search.exe");
  if (!fs.existsSync(exe)) {
    return;
  }
  try {
    execSync('taskkill /F /IM "Title Search.exe" /T', { stdio: "ignore" });
  } catch {
    /* not running */
  }
}

function removeWithRetries(target, attempts = 8) {
  if (!fs.existsSync(target)) {
    return;
  }
  for (let i = 0; i < attempts; i++) {
    try {
      fs.rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 400 });
      console.log("[pre-dist] removed:", target);
      return;
    } catch (err) {
      const busy =
        err && (err.code === "EBUSY" || err.code === "EPERM" || /locked|busy/i.test(String(err.message)));
      if (!busy || i === attempts - 1) {
        throw err;
      }
      console.warn(
        `[pre-dist] ${path.basename(target)} is locked (attempt ${i + 1}/${attempts}). ` +
          "Close Title Search, stop npm run dev, and close any Explorer window on dist-desktop."
      );
      sleepMs(1500);
    }
  }
}

tryClosePackagedApp();

try {
  if (fs.existsSync(asarPath)) {
    removeWithRetries(asarPath, 3);
  }
  removeWithRetries(winUnpacked);
} catch (err) {
  console.warn("\n[pre-dist] Could not remove old output (still locked):", err.message);
  console.warn(
    "dist will use a fresh output folder under dist-desktop\\pack-* if needed.\n" +
      "To clean dist-desktop\\win-unpacked later: stop npm run dev, close Title Search, close Explorer.\n"
  );
}