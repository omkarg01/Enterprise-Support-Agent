// Browser smoke of the portal against a stubbed /v1 (e2e/portal.spec.ts routes every API call), so it needs
// no API. It runs the production build, so the strict Content-Security-Policy is the one under test.
// tests/web/test_web.py covers the real API end to end.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://127.0.0.1:3100" },
  // Its own build directory: building into .next would pull the chunks from under a running `next dev`.
  webServer: { command: "npx next build && npx next start -p 3100", url: "http://127.0.0.1:3100", timeout: 240_000,
    env: { NEXT_DIST_DIR: ".next-e2e" } },
});
