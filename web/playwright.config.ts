import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:18081",
    viewport: { width: 1440, height: 1024 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "../.venv/bin/python ../tests/e2e_server.py",
    url: "http://127.0.0.1:18081/healthz",
    timeout: 30000,
    reuseExistingServer: false,
  },
});
