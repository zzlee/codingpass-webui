import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5183',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-gpu',
          ],
        },
      },
    },
  ],
  webServer: [
    {
      command: 'cd ../backend && ./venv/bin/uvicorn main:app --port 8010',
      url: 'http://127.0.0.1:8010/api/health',
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: 'npm run dev -- --port 5183',
      url: 'http://127.0.0.1:5183',
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
})
