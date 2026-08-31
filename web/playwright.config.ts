import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const localPython = resolve("../.venv/Scripts/python.exe");
const python = existsSync(localPython) ? `"${localPython}"` : "python";

export default defineConfig({
  testDir: "./e2e",
  webServer: {
    command:
      `${python} -m local_harness.interfaces.web.server --control-workspace .. ` +
      "--catalog ../.harness/e2e-workspaces.json --static-dir dist --port 3100",
    url: "http://127.0.0.1:3100/health",
    reuseExistingServer: false,
    timeout: 60_000,
  },
  use: {
    baseURL: "http://127.0.0.1:3100",
    viewport: { width: 1200, height: 800 },
  },
  expect: { timeout: 5000 },
});
