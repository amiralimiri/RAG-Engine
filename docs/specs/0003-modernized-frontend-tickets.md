# Work Packages / Implementation Tickets: Modernized Frontend Architecture and Observability UI

- **Specification Reference:** [`docs/specs/0003-modernized-frontend-spec.md`](./0003-modernized-frontend-spec.md)
- **ADR Reference:** [`docs/adr/0003-modernized-frontend-architecture-and-observability-ui.md`](../adr/0003-modernized-frontend-architecture-and-observability-ui.md)
- **Status:** Ready for Implementation

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│  Ticket 1: Vendored Static Dependencies & Theme Asset  │
│  (static/vendor/*.{js,css})                            │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 2: Design System & CSS Token Architecture      │
│  (static/css/style.css)                                │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 3: Semantic HTML5 Shell & Responsive Layout    │
│  (templates/index.html)                                │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 4: Client State, Session Lifecycle & Theme     │
│  (static/js/app.js - State/Storage Engine)             │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 5: Markdown Pipeline, Security & Thinking State│
│  (static/js/app.js - DOMPurify, Highlight, Thinking)   │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 6: Observability Accordion & Telemetry Feedback│
│  (static/js/app.js - Accordion, Feedback, Toasts)      │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 7: Automated Backend & Static Integration Tests│
│  (tests/test_frontend_integration.py)                  │
└────────────────────────────────────────────────────────┘
```

---

## Ticket 1: Vendored Static Dependencies & Theme Asset

- **Title:** Vendor Marked.js, DOMPurify, Highlight.js, and theme styles into `static/vendor/`
- **Label:** `ready-for-agent`
- **Blockers:** None
- **Files Created:**
  - `static/vendor/marked.min.js`
  - `static/vendor/purify.min.js`
  - `static/vendor/highlight.min.js`
  - `static/vendor/highlight.theme.css`

### Description
Establish the local vendor asset pipeline to support zero-build, air-gapped, and offline execution without reliance on external public CDN script tags. Download/vendor pre-minified releases of Marked.js, DOMPurify, and Highlight.js into `static/vendor/`. Provide an adaptive CSS stylesheet (`highlight.theme.css`) designed to work smoothly with both light and dark UI themes.

### Acceptance Criteria
- [x] `static/vendor/` directory is created.
- [x] `static/vendor/marked.min.js` is present and exports the global `marked` library.
- [x] `static/vendor/purify.min.js` is present and exports the global `DOMPurify` library.
- [x] `static/vendor/highlight.min.js` is present and exports the global `hljs` library.
- [x] `static/vendor/highlight.theme.css` provides syntax highlighting tokens for `.hljs-keyword`, `.hljs-string`, `.hljs-comment`, `.hljs-function`, etc., adhering to CSS variables.
- [x] FastAPI static file mount serves all 4 files with appropriate MIME types (`application/javascript` or `text/javascript`, `text/css`).

---

## Ticket 2: Design System & CSS Token Architecture

- **Title:** Implement tokenized modern CSS design system and responsive layout in `static/css/style.css`
- **Label:** `completed`
- **Files Modified:**
  - `static/css/style.css`

### Description
Rewrite `static/css/style.css` to implement a modern, responsive, and tokenized design system. Define CSS custom properties for dark (`:root`, `[data-theme="dark"]`) and light (`[data-theme="light"]`) themes covering backgrounds, borders, typography, accent highlights, badge statuses, and code blocks. Implement modern layout grids with a collapsible off-canvas navigation drawer for viewports under `1024px`, conversational bubble geometry, code block wrappers with header bars, collapsible reasoning accordions, telemetry buttons, and floating toast notifications.

### Acceptance Criteria
- [x] CSS custom properties defined for both `[data-theme="dark"]` and `[data-theme="light"]`.
- [x] Responsive grid structure:
  - Desktop (≥1024px): 280px left sidebar + flex center chat pane.
  - Mobile (<1024px): 100% viewport chat pane with off-canvas sidebar drawer (`.sidebar.open`).
- [x] User and assistant message bubble styling with avatars, clear visual hierarchy, and smooth typography.
- [x] Styling for code block wrapper with header bar (`.code-block-header`, `.code-lang`, `.copy-code-btn`).
- [x] Collapsible reasoning accordion styling (`details.reasoning-accordion`, summary styling, badges, trace steps list).
- [x] Thumbs up/down feedback bar styling with hover effects, disabled state, and selected state.
- [x] Micro-popover styling for comment input.
- [x] Pulsing agent "Thinking" radar animation and toast notification animations.

---

## Ticket 3: Semantic HTML5 Shell & Responsive Layout

- **Title:** Overhaul `templates/index.html` with semantic HTML5 layout and theme controls
- **Label:** `completed`
- **Blockers:** Ticket 2
- **Files Modified:**
  - `templates/index.html`

### Description
Update `templates/index.html` to integrate the modern layout and load all vendored assets in the correct order. Replace the old fixed 3-column structure with a sleek dual-pane layout featuring a responsive topbar (brand badge, agent status indicator, mobile hamburger toggle, theme switch button), a feature-rich sidebar (workflow card, "New Chat" button, recent chats container `#sessionsList`, document upload trigger), a centralized conversational canvas with welcome greeting, and a sticky composer bar with workflow switcher, example question pills, auto-expanding textarea, and send button.

### Acceptance Criteria
- [x] Includes vendored scripts (`marked.min.js`, `purify.min.js`, `highlight.min.js`), stylesheets (`highlight.theme.css`, `style.css`), and `app.js` in correct execution order.
- [x] Topbar includes:
  - Mobile hamburger toggle (`#sidebarToggleBtn` / `#hamburgerBtn`).
  - Brand title and active status dot.
  - Theme toggle button (`#themeToggleBtn` / `#themeToggle`) with sun/moon SVG iconography.
  - Workflow backend selector (`#workflowSelect`) and document ingestion trigger (`#openIngestModalBtn`).
- [x] Sidebar includes:
  - "New Chat" primary button (`#newChatBtn`).
  - Recent chats container (`#sessionsList` / `#recentChatsList`) and clear history action (`#clearHistoryBtn`).
  - Document upload trigger button (`#openUpload`).
  - Mobile backdrop overlay (`#sidebarBackdrop`).
- [x] Chat area (`#chat` / `#chatMessages`) with initial welcome card (`#welcomeCard`) and animated thinking placeholder markup (`#thinkingBubble`).
- [x] Composer area (`#chatForm`) with `#workflowSelect`, example query pills (`.example`), `#messageInput` / `#question` expanding textarea, `#sendBtn`, and keyboard hint footer.
- [x] Toast notification container (`#toastContainer`).
- [x] Document ingestion modal (`#uploadModal` / `#ingestModal`) maintained and styled to match theme.

---

## Ticket 4: Client State, Session Lifecycle & Theme Switcher

- **Title:** Implement localStorage session management, UUID generation, and theme switcher in `static/js/app.js`
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 3
- **Files Modified:**
  - `static/js/app.js`

### Description
Implement the client state and session management module in `static/js/app.js`. Ensure each conversation session is assigned a persistent UUID v4 session ID (`rag_current_session_id`). Persist transcripts in `localStorage` under `rag_session_<id>` and maintain a session catalog in `rag_sessions_index`. Implement the "New Chat" action to generate a fresh session, and wire the sidebar recent chats catalog to allow switching and deleting past conversations. Implement the theme switcher reading from `localStorage.getItem('rag_theme')` with fallback to `window.matchMedia('(prefers-color-scheme: dark)')`.

### Acceptance Criteria
- [ ] Session ID initialization: Checks `localStorage.getItem('rag_current_session_id')`; if absent, generates a valid UUID v4 via `crypto.randomUUID()` and persists it.
- [ ] "New Chat" button creates a new UUID v4, clears active messages from DOM, displays welcome greeting, and refreshes the recent chats sidebar list.
- [ ] Chat catalog (`rag_sessions_index`) correctly updates when a new message is sent (title extracted from first user prompt, timestamp updated).
- [ ] Clicking a past session in `#sessionsList` restores that conversation transcript into `#chat`.
- [ ] Deleting a past session removes it from `localStorage` and DOM; if active, triggers "New Chat".
- [ ] Theme switcher toggles `document.documentElement.dataset.theme` between `"dark"` and `"light"`, saves preference to `localStorage`, and updates toggle button icon.
- [ ] System theme auto-detection initializes properly on first load.

---

## Ticket 5: Markdown Pipeline, Security Sanitization & In-Feed Thinking State

- **Title:** Implement DOMPurify sanitization, Marked code highlighting, copy button, and thinking animation
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 4
- **Files Modified:**
  - `static/js/app.js`

### Description
Implement the rich text rendering and security pipeline in `static/js/app.js`. Configure `marked` with custom code block rendering to attach header bars with detected language tags and "Copy Code" buttons. Pass all parsed Markdown through `DOMPurify.sanitize()` using strict profiles to prevent prompt injection XSS. Implement the in-feed animated agent "Thinking & Retrieving" placeholder showing elapsed seconds ticker and active workflow badge while `POST /api/chat` is processing.

### Acceptance Criteria
- [ ] `renderMarkdown(text)` safely parses Markdown and runs output through `DOMPurify.sanitize()`.
- [ ] Code blocks render inside `.code-block-wrapper` with language chip and `.copy-code-btn`.
- [ ] Clicking "Copy Code" copies text to clipboard and toggles button text to "Copied!" for 2000ms.
- [ ] Attempted XSS payloads (`<script>`, `<img onerror=...>`, `javascript:...`) are stripped or sanitized.
- [ ] Before `POST /api/chat` executes, `#thinkingBubble` is inserted into `#chat` showing radar animation, workflow badge, and an active elapsed timer ticking every 100ms.
- [ ] Composer input and send button are disabled while thinking.
- [ ] Upon API response or error, `#thinkingBubble` is cleanly removed from `#chat`.

---

## Ticket 6: Observability Reasoning Accordion, Telemetry Feedback & Toast Notifications

- **Title:** Implement per-message reasoning accordion, thumbs up/down voting, and toast notifications
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 5
- **Files Modified:**
  - `static/js/app.js`

### Description
Implement per-message observability components and user feedback interactions in `static/js/app.js`. For every assistant message, render a collapsible `<details class="reasoning-accordion">` displaying `source_used` badge, workflow chip, interactive `trace_id` chip, and step-by-step LangGraph node executions. Implement one-click clipboard copying for `trace_id` with a floating toast notification. Implement Thumbs Up (`1.0`) and Thumbs Down (`0.0`) feedback buttons with optimistic locking and asynchronous submission to `POST /api/feedback`, accompanied by an optional comment micro-popover.

### Acceptance Criteria
- [ ] Each assistant message bubble includes a collapsible `details.reasoning-accordion`.
- [ ] Accordion displays:
  - Source badge (`vectorstore` vs `web_search`).
  - Active workflow tag.
  - Trace steps list (`step-item` elements).
  - Trace ID chip with click-to-copy trigger.
- [ ] Clicking `trace_id` copies the exact ID to clipboard and triggers `showToast("Trace ID copied!", "success")`.
- [ ] Toast notification appears in bottom-right, stacks cleanly, and auto-dismisses after 2500ms.
- [ ] Feedback bar includes Thumbs Up (`data-score="1.0"`) and Thumbs Down (`data-score="0.0"`).
- [ ] Clicking a feedback button:
  - Sets optimistic active state and locks buttons.
  - Dispatches `POST /api/feedback` with `{ trace_id, score, name: "user_feedback", comment }`.
  - Shows success confirmation text.
- [ ] "Add Note" button opens a comment popover allowing user to submit detailed feedback to `POST /api/feedback`.
- [ ] Historical trace and feedback state persist across reloads within `rag_session_<id>`.

---

## Ticket 7: Automated Backend & Static Integration Test Suite

- **Title:** Implement automated integration tests for static assets, endpoint contracts, and HTML shell
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 6
- **Files Created:**
  - `tests/test_frontend_integration.py`

### Description
Create an automated test suite in `tests/test_frontend_integration.py` using pytest and FastAPI's `TestClient`. Verify that the HTML template renders with the required semantic elements, all vendored static assets (`marked.min.js`, `purify.min.js`, `highlight.min.js`, `highlight.theme.css`) and application files (`style.css`, `app.js`) are served with HTTP 200 and correct MIME types, and all backend API endpoints (`/api/workflows`, `/api/chat`, `/api/feedback`) adhere to the frontend data contracts.

### Acceptance Criteria
- [ ] `tests/test_frontend_integration.py` tests pass with `pytest`.
- [ ] `GET /` returns HTTP 200 with HTML content containing required elements (`#chat`, `#chatForm`, `#themeToggle`, `#newChatBtn`, `#sessionsList`).
- [ ] Static asset route tests verify HTTP 200 for:
  - `/static/vendor/marked.min.js`
  - `/static/vendor/purify.min.js`
  - `/static/vendor/highlight.min.js`
  - `/static/vendor/highlight.theme.css`
  - `/static/css/style.css`
  - `/static/js/app.js`
- [ ] API integration tests verify `/api/workflows`, `/api/chat`, and `/api/feedback` responses match the expected schemas.

---

## Summary Matrix

| Ticket | Scope / Layer | Primary Files | Blockers | Target Status |
|---|---|---|---|---|
| **Ticket 1** | Vendored Dependencies | `static/vendor/*.{js,css}` | None | `completed` |
| **Ticket 2** | Design Tokens & Layout CSS | `static/css/style.css` | Ticket 1 | `completed` |
| **Ticket 3** | HTML5 Semantic Shell | `templates/index.html` | Ticket 2 | `completed` |
| **Ticket 4** | Client State & Session Lifecycle | `static/js/app.js` | Ticket 3 | `ready-for-agent` |
| **Ticket 5** | Markdown Pipeline & Security | `static/js/app.js` | Ticket 4 | `ready-for-agent` |
| **Ticket 6** | Observability Accordion & Feedback | `static/js/app.js` | Ticket 5 | `ready-for-agent` |
| **Ticket 7** | Automated Integration Tests | `tests/test_frontend_integration.py` | Ticket 6 | `ready-for-agent` |
