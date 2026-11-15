import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const watchdog = process.env.DW_WATCHDOG_URL ?? "http://127.0.0.1:8010";
const agent = process.env.DW_AGENT_URL ?? "http://127.0.0.1:8003";
const normalGen = process.env.DW_NORMAL_GEN_URL ?? "http://127.0.0.1:8001";
const attackGen = process.env.DW_ATTACK_GEN_URL ?? "http://127.0.0.1:8002";

export default defineConfig({
  // Relative asset paths: the same build is served at /app on the watchdog and on the agent chat.
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(here, "src") } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: watchdog, changeOrigin: true },
      "/reports": { target: watchdog, changeOrigin: true },
      "/agent-api": {
        target: agent,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/agent-api/, "/api"),
        // The agent rejects a POST whose Origin is not its own page. In dev the page is on the Vite port,
        // so the proxy sends the agent's own origin.
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => proxyReq.setHeader("origin", agent));
        },
      },
      "/normal-api": { target: normalGen, changeOrigin: true, rewrite: (p) => p.replace(/^\/normal-api/, "/api") },
      "/attack-api": { target: attackGen, changeOrigin: true, rewrite: (p) => p.replace(/^\/attack-api/, "/api") },
    },
  },
});
