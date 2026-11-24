#!/usr/bin/env node
const { spawnSync } = require("child_process");
const path = require("path");

const script = path.join(__dirname, "..", "..", "installer", "scripts", "prepare-packaged-python.ps1");
const ps = spawnSync(
  "powershell.exe",
  ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
  { stdio: "inherit" }
);
process.exit(ps.status ?? 1);