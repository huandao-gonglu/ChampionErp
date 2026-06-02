// @ts-check
const { defineConfig } = require("@playwright/test");

const PYTHON = process.env.CODEX_PYTHON || "C:\\Users\\miami\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe";
const PORT = process.env.ERP_E2E_PORT || "5017";

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `"${PYTHON}" erp_web_app.py`,
    cwd: __dirname,
    env: {
      ...process.env,
      ERP_NO_BROWSER: "1",
      ERP_PORT: PORT,
    },
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [
    {
      name: "chrome",
      use: { channel: "chrome" },
    },
  ],
});
