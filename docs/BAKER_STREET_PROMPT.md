# Build prompt: Baker Street, the evidence-board dashboard

Read `CLAUDE.md` and `docs/TEAM_HANDOFF.md` first. Then read `docs/API.md` section 4 and the files in `frontend/src/`.

## 1. Goal

Replace the dashboard pages of the React app (`frontend/`, served at `http://127.0.0.1:8010/app/`) with a new design named **Baker Street** (after 221B Baker Street). The design is a detective evidence board: a pinboard with postcards, paper clippings, and red string between them.

The data and its meaning do not change. Only the dashboard's look, layout, and navigation change.

## 2. Hard limits

1. **Do not change the backend.** Do not edit `dw/`, `static/`, `scripts/`, `tests/`, `config.yaml`, or `docs/API.md`. Use only the watchdog endpoints that `docs/API.md` section 4 gives. Add no endpoint and no field.
2. **Do not change the other sites.** The same React build is served on four ports. The traffic generator pages (8001, 8002: `pages/Traffic.tsx`) and the agent chat page (8003: `pages/AgentChat.tsx`) must look and work exactly as they do now. The plain-HTML pages at `/` on each port also stay.
3. **The other sites share code with the dashboard:** the sidebar and shell in `App.tsx`, the tokens in `index.css`, and `components/Notes`, `PageHeader`, `Panel`, `ui/button`, `ui/select`. Do not restyle or change the behavior of shared code. Instead:
   - Put all new code in `frontend/src/baker/`.
   - Put all new styles in one stylesheet whose rules apply only below a root class (for example `.baker`). Do not edit the `:root` tokens.
   - In `App.tsx`, make one branch: if the role has the dashboard (`hasDashboard(role)`) and the route is a dashboard route, show the Baker Street shell with no sidebar. In all other cases, show the current shell unchanged.
   - Delete the old dashboard pages and components only if no other site imports them.
4. **Load nothing from other hosts.** Fonts, textures, and images come from npm packages or from files in the repository. `tests/test_app_frontend.py` checks this and checks that the client calls only documented endpoints.
5. **Keep the project rules:** never commit `data/`, `cache/`, `models/`, or `.env`. Never show persona names or data sources. Message text stays in the browser; send it to no external service.
6. Stop processes with `scripts/stop.sh` or by PID. Do not use `pkill -f`.

## 3. The board (first screen)

When the page loads, the person sees a pinboard that fills the window. There is no sidebar and no page scroll at 1280×720 and larger.

**Center: the request-rate chart.** A paper sheet pinned in the middle with the live chart of requests served and requests blocked.

- The person selects the window: **1h, 24h, 48h, 1w, 1m** (1m is 30 days). The window is in simulated time; say so on the chart.
- The window is fixed: the time axis always shows the full window that ends at the current simulated time. Where there is no data yet, the chart is empty. The axis does not stretch to fit the data.
- Data source: `rate_history` in the summary (stream event `summary`, one each second). The backend keeps only the last 240 buckets of 60 simulated seconds. For longer windows, the page must keep its own history:
  - Merge each new `rate_history` into a store by bucket time. The newest value of a bucket replaces the old one.
  - Save the store in browser storage so a page reload keeps it.
  - Keep 1-minute buckets for recent data and combine older data into larger buckets, so the store and the chart stay small (a 1m window must not draw 43,200 points).
  - If the simulated time or the `served` counter goes back, the watchdog was reset: clear the store.

**Edges: five postcards.** Put five postcard-like objects around the chart, roughly evenly spaced, each with a small offset and a small rotation so the layout looks hand-made. Each has a pin, a title, and a small live preview (for example a count or the newest line).

| No. | Postcard | Opens |
|---|---|---|
| 1 | Recent activity | Display A |
| 2 | Accounts list | Display B |
| 3 | Suspicious messages | Display C |
| 4 | Active metrics | Display D |
| 5 | Agent graph | Display E |

**Strings.** Red thread connects the pins of these pairs: 1–2, 1–3, 2–3, 2–4, 1–4, 1–5, 3–5.

- Keep the connections in one table in the code (from, to, tight or droopy), so a person can change them without other edits.
- The thread looks like real thread: slightly uneven, with a soft shadow. Some strings are tight (almost straight) and some are droopy (a visible sag). Use SVG paths that are computed from the pin positions, so the strings stay attached when the window size changes.

**Small items on the board:**

- A **folder** that opens the Evaluation display (Display F). It is not a postcard and has no string.
- Three **luggage tags** (or equivalent small props) that link to the other sites: normal traffic generator, attack traffic generator, agent chat. Use `generatorSite()` and `agentSite()` from `lib/origin.ts`.
- A small **status line**: connection state (Live, Connecting, Offline) and the simulated date and time.
- A title plate with the name **Baker Street**. Also set the browser tab title to "Baker Street" on the dashboard only; the other sites keep their title.
- Search stays on `Ctrl K` (accounts and campaigns), restyled to the theme.

## 4. Zoom and navigation

- A click on a postcard (or `Enter` when it has focus) zooms into its display. The animation starts at the postcard's position and size and ends at the full display, so the person sees the card grow. The board moves out of view behind it.
- Each display has a **Back** button at the same position. Back, the `Esc` key, and the browser Back button all zoom out to the board with the reverse animation.
- Each display has a hash route, so a reload and a shared link open the same display. Keep the current hashes working: `#/live` (board), `#/accounts/<id>` (account dossier), `#/campaigns` and `#/campaigns/<id>` (Display E), `#/eval` (Display F).
- Animations must be smooth: animate only `transform` and `opacity`, aim for 60 frames each second, and use durations of 300 to 500 ms. Live data updates must not interrupt an animation.
- If the person's system asks for reduced motion, replace the zoom with a short fade.

## 5. The displays

Each display is a "desk" surface with paper objects. Keep all functions that the current pages have unless this prompt says different.

**A. Recent activity.** The current Activity section of `pages/Live.tsx`: flags, blocks, and new campaigns, newest first, from the stream (`useStream`). Show each entry as a short telegram or newspaper clipping: who, what, score, simulated time, reason. A click on an account entry opens the account dossier. A click on a campaign entry opens Display E with that campaign selected. Note: the feed has only the events that arrived while the page was open. When it is empty, say so.

**B. Accounts list.** The current Accounts table: account name, state, score, score history, requests, campaign, top reasons. Keep the state filter with counts, the three sort orders, and the `J` / `K` / `Enter` keys. Show it as a ledger or notebook page. A click on a row opens the **account dossier**: a folder with everything that `components/AccountView.tsx` shows now (features, contributions, evidence list, score history, case file button). A click on an evidence item shows the exact message, as `EvidenceDialog` does now.

**C. Suspicious messages.** A list of the messages that the watchdog flagged. No endpoint gives this list, so build it in the browser:

1. Get the accounts in the states `block` and `review` (`GET /api/accounts?state=`).
2. For each account, get its evidence (`GET /api/accounts/{id}`), and take the items with the highest weight.
3. For each item, get the message (`GET /api/evidence/{id}`).
4. Group entries that have the same message text. One entry shows the text (shortened), the number of accounts that sent it, the detector in plain words, and the color of the most severe account. Sort by the number of accounts, largest first, so common messages are at the top.

Set limits on the number of accounts and items, run only a small number of requests at a time, keep the results in the query cache, and show the entries as they arrive. A click on an entry shows the full message and the accounts that sent it. Show the entries as torn paper clippings or typed notes.

**D. Active metrics.** A board of live numbers from the summary: requests each second, requests served, requests blocked, the share that was blocked, accounts in each state, campaigns found, queue depth, events dropped, and the simulated time. Show them as index cards, a tally sheet, or a telegraph counter. Numbers update in place without a layout shift.

**E. Agent graph.** The account graph from `GET /api/graph`, with the campaign list and campaign detail from the current `pages/Campaigns.tsx`.

- **Highlight** an account only if it has a campaign ID **and** one or more links of the type `shared_task` (two accounts sent the same task text). Draw these as pinned photographs or pins in the state color, with red thread for their shared-task links.
- All other accounts and links stay on the graph but are faded and thin.
- A click on a highlighted account shows its name, state, score, and a link to its dossier. A click on a link shows why the two accounts are linked (`evidence` text of the edge in `GET /api/campaigns/{id}`).
- Beside the graph, list the campaigns (ID, size, score, state, main link types in plain words). A selected campaign shows its members, its evidence, and the case file button.
- You can keep Cytoscape (`components/CampaignGraph.tsx`) or draw the graph with SVG. The layout must stay stable when the data refreshes: nodes must not jump every 4 seconds.

**F. Evaluation (the folder).** The same content as the current `pages/Evaluation.tsx`, shown as a case report: typed pages with the tables, and the report images as photographs.

**All displays** need these states, written in the theme: loading, empty ("No requests yet. Start a traffic generator."), and watchdog offline (the page tries again by itself).

## 6. Design

**Mood.** Sherlock Holmes, Murder on the Orient Express, an early-1900s detective's study. Aged paper, cork, wood, brass. Calm and readable first; decoration second.

**Color.** Use these and no others:

- Base colors (4): a **reddish brown** (wood, leather, frames), a **beige** (cork, aged paper), an **off-white** (clean paper, cards), and a **dark ink** for text. Reddish brown, beige, and off-white are required.
- Signal colors (3): **red** for blocked and for all string; **yellow** (mustard, not neon) for suspicious or pending; **gray** for OK.
- Shades of the same color (for a shadow or a border) are permitted. New hues are not.
- Make sure text has sufficient contrast on each paper color.

**Account states.** Five backend states map to three colors. Always show the word with the color; never use color alone.

| Backend state | Color | Word on the page |
|---|---|---|
| `block` | Red | Blocked |
| `review` | Yellow | Suspicious |
| `normal` | Gray | OK |
| `insufficient` | Gray | Too few requests |
| `disclosed` | Gray | Declared |

Show the word as a rubber-stamp mark where it fits. In charts and on the graph, use the same three colors.

**Fonts.** Use a maximum of two families, installed from npm (`@fontsource`), and remove none of the fonts the other sites use.

- One period serif for titles and body text, and one typewriter face for data, labels, and numbers. Good candidates: Libre Caslon Text, Old Standard TT, or IM Fell English for the serif; Courier Prime for the typewriter face.
- Do not use Inter, Roboto, Poppins, Montserrat, Space Grotesk, or other fonts that are typical of generated sites. Do not use novelty fonts: no blackletter, no fake handwriting, no heavily distressed typewriter faces.
- Use one small size scale (four sizes at most) for the whole dashboard. Body text is 15 px or larger. Numbers in tables use tabular figures.

**Objects.** Most content sits on paper objects: newspaper clippings, postcards, polaroid photographs, notebook and ledger pages, index cards, telegrams, folders. Use pins, tape, paper clips, soft shadows, and small rotations (2 degrees at most for objects with body text).

- Do not round the corners of boxes unless the object needs it (a pin head, a stamp, a lens).
- Make textures with CSS or SVG, or with small local image files. Keep them subtle so text stays easy to read.
- Add period props where they make sense and do not cover content: a magnifying glass, a typewriter, a pipe, a pocket watch, a brass lamp, a wax seal, an ink stamp, a railway ticket. A prop can have a function (the magnifying glass can open search). Draw props as SVG. Do not use emoji or modern icons on the board.
- Hover and focus states must be visible: a postcard lifts a little, and keyboard focus shows a clear outline.

**Window sizes.** Design for desktop: 1280×720 to 1920×1080. Below 900 px wide, show the chart and the five postcards as a simple stacked list with no strings; all displays must still work.

## 7. Text style

- Write all text on the page for a person who is not an engineer. Use ASD-STE100 (Simplified Technical English) where it applies: short sentences, one meaning for each word, active voice, present tense.
- Limit jargon. Do not show internal names such as `d4_elicit_mean` or `shared_task` without plain words. Use the plain names in `lib/states.ts` (for example "Redundancy: the same conversation state is sent many times") and write plain words for the link types: "same task text", "same timing", "same rare tool set".
- Use one word for one thing on all displays:

| Thing | Word |
|---|---|
| One caller identity | Account |
| A group of linked accounts | Campaign |
| State `block` / `review` / `normal` | Blocked / Suspicious / OK |
| One item that supports a decision | Evidence |
| The written summary of a case | Case file |
| The simulated clock | Simulated time |

- The postcard titles are fixed: Recent activity, Accounts list, Suspicious messages, Active metrics, Agent graph.
- Period flavor is welcome in titles and small labels only ("Case file", "Telegram"). Do not write instructions or data labels in old-style language.

## 8. Build and test

Work on a new git branch. Do not push.

1. **Before you change code**, start the demo (`scripts/demo.sh`), start some traffic on each generator, and take reference screenshots of the generator pages (`:8001/app/`, `:8002/app/`) and the agent chat page (`:8003/app/`) at 1440×900.
2. Build with `cd frontend && npm run build`. The watchdog serves `frontend/dist` at `/app`. `npm run typecheck` must pass.
3. **Test with rendered pages, not only with code review.** Use a headless browser (Playwright with Chromium is installed in `~/.cache/ms-playwright`; add `?nostream` to the address for stable screenshots). Look at each screenshot yourself and correct what is wrong. Check:
   - the board at 1280×720, 1440×900, and 1920×1080: no overlap, no cut text, strings attached to pins, no page scroll;
   - the stacked layout at 800 px wide;
   - each of the six displays and the account dossier, with data, with no data (after `POST /admin/reset`, or before traffic starts), and with the watchdog stopped;
   - the five chart windows, after the demo has run for some minutes, and after a page reload (the history must still be there);
   - the zoom in and zoom out for each postcard: capture frames during the animation, and confirm that there is no jump, flash, or layout shift, and that Back, `Esc`, and the browser Back button all work;
   - a blocked, a suspicious, and an OK account in each place where state shows;
   - keyboard use: `Tab` reaches each postcard and control, focus is visible, `Ctrl K` opens search;
   - the browser console: no errors and no requests to other hosts.
4. **Prove that the other sites did not change:** take the screenshots of step 1 again and compare them with the references. They must be equal, apart from live numbers.
5. Run `OMP_NUM_THREADS=1 .venv/bin/pytest -q -p no:cacheprovider` and `scripts/smoke.sh` (run `scripts/stop.sh` first; they use the same ports as the demo). Both must pass.
6. Confirm with `git status` and `git diff --stat` that nothing outside `frontend/` changed, apart from a short note in the documents if you add one.

## 9. Done when

- `/app/` on port 8010 opens the Baker Street board with the live chart, five postcards, seven strings, the folder, and the three links.
- All six displays and the account dossier work with live data and zoom in and out smoothly.
- The pages on ports 8001, 8002, and 8003 and the plain-HTML pages are unchanged, with screenshots as proof.
- The tests and the smoke check pass, and no backend file changed.
- Your report gives: what you built, the colors and fonts you selected, the screenshots you checked, each limit you set (for example the number of accounts read for Suspicious messages), and anything that does not work yet.

## 10. Changes after the first build

The owner reviewed the first build. These decisions replace the text above where they differ.

- **Wood:** oak (the board frame and the desk behind each display). The reddish brown is only an accent for links and the selected control.
- **Paper:** one flat color. No gradient on a box. No tape; the chart sheet has two pins.
- **Title:** "221B Baker Street" in dark ink on the cork, with no box.
- **Fonts:** EB Garamond for titles and text, Courier Prime for data. Sizes: 13, 17, 24, and 38 px. On the board the sizes follow the board size, so no postcard cuts its text.
- **Text on the wood:** not permitted. Titles, notes, and section headings of a display are on paper labels.
- **Perforated edges:** on some paper objects (title labels, telegrams, the status slip).
- **Information:** each postcard and the chart have an "i" button that opens a short note about the panel.
- **Magnifying glass:** removed. A small "Search Ctrl K" slip opens the search.
- **Postcards:** two pins each. A string uses the two pins that are nearest to each other. The order around the board is 1, 4, 2, 3, 5.
- **Agent graph:** it highlights each account that is in a campaign. The demo traffic makes no "same task text" links, so the first rule (campaign and same task text) highlighted nothing. The setting is `HIGHLIGHT` in `frontend/src/baker/AgentGraph.tsx`. The pointer over an account shows its name, state, score, and links.
- **Suspicious messages:** all clippings have one look, open or closed. Actions are underlined text.
- **Shared components** (account dossier, evaluation, evidence dialog, case file) get the Baker Street colors from the token block at the top of `baker.css`. Check them with the light and the dark setting saved in the browser.
- **Textures** are two small image files (`oak.jpg`, `cork.jpg`). An SVG filter on the board or on a display makes the zoom slow.
