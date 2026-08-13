import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:3000", ws: true },
      "/health": "http://127.0.0.1:3000",
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["e2e/**", "node_modules/**"],
    coverage: {
      provider: "v8",
      reporter: ["text"],
      include: [
        "src/api.ts",
        "src/markdown.ts",
        "src/presentation.ts",
        "src/timeline.ts",
        "src/App.tsx",
      ],
      thresholds: { statements: 85, branches: 85, functions: 85, lines: 85 },
    },
  },
});
