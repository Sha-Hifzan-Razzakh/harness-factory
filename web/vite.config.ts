import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built into the Python package, served by the API at /app (src/harness_factory/api/web.py).
// `npm run dev` proxies /api to a running `hfx serve` (HF_API_URL, default http://127.0.0.1:8765).
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "../src/harness_factory/web_dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": process.env.HF_API_URL ?? "http://127.0.0.1:8765" },
  },
});
