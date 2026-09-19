// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev: Vite on :5173 proxies /api to the FastAPI process on :8080.
// Prod: `vite build` output is served by FastAPI itself from web/dist.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
  test: {
    environment: "node",
  },
});
