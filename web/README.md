# Support portal (web)

Next.js 15 (App Router) customer portal: chat over the resumable event stream, anonymous / one-time-code
sign-in, citation drawer and the tier-filtered "how this was answered" trace.
Mockups: `docs/design/mockups/`.

```sh
npm ci
API_ORIGIN=http://127.0.0.1:8000 npm run dev   # /v1 is proxied to the API; open http://127.0.0.1:3000
npm test                                      # Vitest: client, SSE parser, reducer
npm run e2e                                   # Playwright: production build, /v1 stubbed, strict CSP
```

`/t/<tenant>` opens the portal for another tenant (default `NEXT_PUBLIC_DEFAULT_TENANT`, else `t_us`).
`NEXT_PUBLIC_HELP_CENTER_URL` shows the Help center link. In development the one-time code is printed in the
API log. `tests/web/test_web.py` runs the real API behind `next start`.

`/staffs` is a **demo staff console** with its own look (`app/staffs`, `components/staff`, `lib/staff.ts`):
tickets with filters, moves, refund approval, replies and internal notes; customers; every service with
stop / start / restart / scale; hand-offs. It calls `/v1/staff/*` (`src/channels/api/staff.py`), which has
**no sign-in**: anyone with the URL reads and changes every customer's data (register S20). Pages poll only
while visible, at 10-15 s.

## Deploy (Vercel)

- Set `API_ORIGIN` in the project settings; it is read at build time and the build fails without it.
- `/v1/*` is rewritten server-side, so the browser only talks to the portal's origin over HTTPS. The hop from
  Vercel to the API is whatever `API_ORIGIN` says: use an HTTPS API origin (or a tunnel) in production.
- `middleware.ts` sets a per-request nonce Content-Security-Policy (no `unsafe-inline` scripts); pages render
  per request for that reason.

## Trust panels and tiers

The server filters the trace by assurance tier (`src/channels/api/trace.py`): T0 gets the summary and the
agent outline, T1 / T2 also get tool calls, their arguments and the shared state. No tier sees scores,
confidences or model ids. The agent names (Coordinator, Knowledge Agent, Output Reviewer) label stages of the
single turn loop until the coordinator topology is wired.
