## B16. React stage (DO NOT BUILD; copy this section to `docs/REACT_STAGE.md`)

> **STOP.** Do not run this stage during the one-shot build. Copy this section verbatim to `docs/REACT_STAGE.md`. Build it only when a person writes "build the React stage".

When a person asks:

1. Make `frontend/` with Vite + React + TypeScript.
2. Dependencies: `@tanstack/react-query` (data), `recharts` (charts), `cytoscape` with `react-cytoscapejs` (graph). No other UI framework.
3. Use **only** the endpoints in `docs/API.md`. Do not change the API. If the React app needs a new field, add it to the API and to the HTML pages at the same time.
4. Make one typed client `src/api.ts` from `docs/API.md`. Make one hook for the SSE stream.
5. Pages: Live, Account, Campaigns, Evaluation (from the watchdog), and Agent chat (from the agent chat process). Same content as the HTML pages.
6. `npm run build` writes to `frontend/dist`. The watchdog serves it at `/app`. Keep the HTML pages at their current paths.
7. Do not remove the HTML pages. The demo must still work if the React build fails.
8. Time box: 90 minutes. If the time box ends, keep the HTML pages for the demo.

