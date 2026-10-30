const path = require('path')
const { defineConfig } = require('@playwright/test')
const root = path.resolve(__dirname, '../..')
const python = process.env.E2E_PYTHON || 'python'
module.exports = defineConfig({
  testDir: './tests', workers: 1, retries: 0, timeout: 60000,
  use: { headless: true, launchOptions: process.env.E2E_CHROME ? {executablePath: process.env.E2E_CHROME} : {} },
  webServer: [
    { command: `"${python}" tests/e2e_server.py`, cwd: path.join(root, 'backend'), port: 18766, reuseExistingServer: false, timeout: 60000 },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5175 --strictPort', cwd: path.join(root, 'frontend/player'), port: 5175, reuseExistingServer: false, env: { VITE_API_BASE: 'http://127.0.0.1:18766/api/v1' }, timeout: 60000 },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5176 --strictPort', cwd: path.join(root, 'frontend/admin'), port: 5176, reuseExistingServer: false, env: { VITE_API_BASE: 'http://127.0.0.1:18766/api/v1' }, timeout: 60000 }
  ]
})
