import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const devApiProxy = process.env.VITE_DEV_API_PROXY || "http://127.0.0.1:8000";
const devPort = Number(process.env.VITE_DEV_PORT || 5173);

export default defineConfig({
  base: process.env.ELECTRON_BUILD ? "./" : "/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "127.0.0.1",
    port: devPort,
    strictPort: process.env.VITE_DESKTOP_DEV === "1",
    allowedHosts: ["13.126.130.128"],
    proxy: {
      "/api": {
        target: devApiProxy,
        changeOrigin: true,
      },
    },
  },
});
