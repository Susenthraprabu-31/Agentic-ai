#!/usr/bin/env node
/** Run electron-builder, using a fresh output dir if win-unpacked is locked. */

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const desktopDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopDir, "..");
const defaultOut = path.join(repoRoot, "dist-desktop");

const packagedPy = path.join(repoRoot, "installer", "packaged-python", "embed", "Scripts", "python.exe");
if (!fs.existsSync(packagedPy)) {
  console.error(
    "\n[dist] Missing bundled Python. Run from desktop:\n  npm run predist\n  (requires backend .venv + requirements.txt)\n"
  );
  process.exit(1);
}
const winUnpacked = path.join(defaultOut, "win-unpacked");

function isLockedDir(dir) {
  if (!fs.existsSync(dir)) {
    return false;
  }
  try {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 1, retryDelay: 100 });
    return false;
  } catch (err) {
    return err && (err.code === "EBUSY" || err.code === "EPERM");
  }
}

let outputDir = defaultOut;
if (isLockedDir(winUnpacked)) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  outputDir = path.join(repoRoot, "dist-desktop", `pack-${stamp}`);
  fs.mkdirSync(outputDir, { recursive: true });
  console.warn(
    "[dist] Previous dist-desktop\\win-unpacked is locked; writing to:\n",
    outputDir,
    "\nTo reuse dist-desktop, stop Electron / npm run dev and close Explorer on win-unpacked.\n"
  );
}

const result = spawnSync(
  "npx",
  [
    "electron-builder",
    "--win",
    "-c.directories.output",
    path.relative(desktopDir, outputDir).replace(/\\/g, "/"),
  ],
  { cwd: desktopDir, stdio: "inherit", shell: true }
);

process.exit(result.status ?? 1);