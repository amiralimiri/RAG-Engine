# ADR 0003: Modernized Frontend Architecture, Zero-Build Asset Pipeline, and Observability UI

## Status
Accepted

## Context
Following the implementation of ADR 0001 (Langfuse Observability & Evaluation) and ADR 0002 (Structured Logging & Context Propagation), the backend delivers comprehensive observability:
- Every chat response emits `trace_id`, execution `trace` steps, and `source_used`.
- An asynchronous evaluation endpoint (`POST /api/feedback`) ingests human feedback (`score: 0.0 - 1.0`, optional `comment`).
- Multi-turn conversational correlation is supported via `session_id` and `user_id` in `POST /api/chat`.
- Multiple graph backends (`fix_single_llm` and `fix_single_jev`) are exposed via `GET /api/workflows`.

However, the existing user interface (`templates/index.html`, `static/js/app.js`, `static/css/style.css`) exhibits major operational and technical limitations:
1. **Loss of Historical Audit Trail**: A fixed 3-column desktop layout displays execution traces exclusively in a single global right sidebar. Every new query overwrites this panel, erasing the trace history of preceding conversational turns.
2. **Missing Telemetry & Feedback Integration**: The UI provides no mechanism for users to rate answers (thumbs up/down) or inspect/copy the Langfuse `trace_id` for troubleshooting.
3. **Prompt Injection & XSS Vulnerability**: Naive plaintext formatting (`\n` to `<br>`) lacks Markdown rendering and code highlighting. Introducing arbitrary Markdown parsing without HTML sanitization would expose the client to Cross-Site Scripting (XSS) from ingested company documents or live Tavily web search results.
4. **Ephemeral Session State**: Refreshing or navigating away wipes chat history and generates no persistent `session_id`, breaking multi-turn Langfuse trace correlation.
5. **No Visual Workflow Feedback**: During multi-step agent execution (which may take 2–6 seconds for retrieval, grading, and web fallbacks), users receive no progress feedback.
6. **Rigid Layout & Styling**: Fixed desktop-only CSS grid, lack of mobile responsiveness, and absence of light/dark theme toggling.

---

## Decisions

### 1. Zero-Build Vendored Asset Pipeline & Native Modern CSS Tokens
To maintain extreme simplicity, instant page load speeds, and air-gapped/offline resilience without introducing Node.js, npm, or complex frontend build bundlers (Webpack, Vite):
- **Vendored Core Libraries**: Self-host pre-minified libraries in `static/vendor/`:
  - `marked.min.js`: High-speed Markdown parser.
  - `purify.min.js`: Industry-standard HTML sanitizer (DOMPurify).
  - `highlight.min.js` + theme stylesheet: Syntax highlighting for code blocks.
- **Pure Modern CSS with Design Tokens**:
  - Implement a complete CSS custom property system (`--bg-primary`, `--bg-surface`, `--text-primary`, `--border`, `--accent`, `--accent-glow`, etc.).
  - Avoid runtime CDN build tools (such as Tailwind Play CDN) that degrade performance and require external internet connectivity.
- **Theme Mechanics**:
  - Support Dark and Light themes via `[data-theme="dark"]` and `[data-theme="light"]` attributes on the root `<html>` element.
  - Default to system color scheme (`window.matchMedia('(prefers-color-scheme: dark)')`) with persistent override in `localStorage.getItem('rag_theme')`.
  - Accessible theme toggle button in the top bar.

### 2. Mandatory Defense-in-Depth DOM Sanitization
Because RAG pipelines ingest external untrusted text from web searches and third-party documents:
- All Markdown parsed by `marked` **must** pass through `DOMPurify.sanitize()` before being injected into the DOM.
- Code blocks are processed through `highlight.js` safely via `marked` custom renderers before sanitization.
- Direct unsanitized `innerHTML` assignments are prohibited across the frontend codebase.

### 3. Inline Per-Message Reasoning & Audit Accordion
To preserve conversational auditability across multiple turns:
- Each assistant message bubble directly encapsulates its own collapsible `<details>` / custom accordion: **"Reasoning & Audit Steps"**.
- The accordion displays:
  - Source attribution badge (`vectorstore` vs `web_search`).
  - Active workflow tag (`fix_single_llm` vs `fix_single_jev`).
  - Chronological LangGraph node execution steps (`retrieve` -> `grade_evidence` -> `generate`).
  - Interactive Langfuse `trace_id` chip with one-click clipboard copying and toast notification.
- Users can inspect historical traces for any prior message without state collisions.

### 4. Asynchronous Telemetry & Human Feedback Flow
To close the RLHF and continuous evaluation loop with Langfuse v5:
- Each assistant bubble includes Thumbs Up (score `1.0`) and Thumbs Down (score `0.0`) action buttons.
- **Optimistic Locking**: Clicking immediately applies an active visual state and locks the buttons to prevent redundant submissions.
- **API Dispatch**: Sends an asynchronous request to `POST /api/feedback` with `{ trace_id, score, name: "user_feedback", comment }`.
- **Micro-Popover**: Thumbs down (or an explicit "Add note" trigger) offers an optional micro-popover to submit context comments without disrupting conversational flow.

### 5. Client Session Lifecycle & Multi-Session Catalog
To power multi-turn conversational tracing in Langfuse and FastAPI contextvars:
- **Session Identification**: On initial load, a persistent UUID v4 `session_id` is generated and saved in `localStorage.getItem('rag_current_session_id')`.
- **Payload Correlation**: All queries to `POST /api/chat` transmit `{ question, workflow, session_id }`.
- **Conversation Persistence**: Chat message transcripts are cached in `localStorage` under `rag_session_<id>`.
- **Recent Chats Catalog**: A lightweight catalog (`rag_sessions_index`) tracks `[{ id, title, updatedAt, messageCount }]` in the sidebar, enabling users to switch between past chats or delete them.
- **"New Chat" Action**: Generates a new UUID v4, clears the active conversation viewport, and adds the previous session to the recent chats catalog.

### 6. Interactive Agentic "Thinking" State
During API calls to `POST /api/chat`:
- An animated in-feed placeholder displays a pulsing radar indicator, active workflow badge, and an elapsed-time counter (`Thinking & searching KB... 3s`).
- The placeholder smoothly transitions into the completed assistant bubble and reasoning accordion upon response arrival.
- Composer input and workflow selector are disabled during processing to prevent overlapping requests.

### 7. Code Block Usability
- Code blocks rendered within Markdown automatically feature a header bar with:
  - Detected language identifier (e.g. `bash`, `python`, `json`).
  - A dedicated "Copy Code" button with immediate "Copied!" visual confirmation for 2 seconds.

### 8. Responsive Layout Architecture
- Desktop: Collapsible navigation sidebar + centralized conversational canvas with sticky composer.
- Mobile (< 1024px): Responsive hamburger drawer for navigation, document upload, and workflow switching.

---

## Technical Architecture & File Layout

```
static/
├── vendor/
│   ├── marked.min.js          # Markdown compiler
│   ├── purify.min.js          # DOMPurify HTML sanitizer
│   ├── highlight.min.js       # Core syntax highlighter
│   └── highlight-theme.css    # Dark/light adaptive code theme
├── css/
│   └── style.css              # Custom tokenized CSS (Variables, Theme, Layout, Components)
└── js/
    └── app.js                 # Vanilla modular architecture (State, Storage, Chat, UI)
templates/
└── index.html                 # Clean semantic HTML5 layout with dark/light shell
```

---

## Consequences

### Positive
- **Zero Build Toolchain**: No Node.js dependencies, npm install steps, or compilation stages required to serve or develop the frontend.
- **Robust Security**: Enforced `DOMPurify` prevents prompt-injection XSS from untrusted web or document sources.
- **Complete Audit Trail**: Every turn retains its exact execution trace, source, and trace ID independently.
- **Direct Telemetry Bridge**: Seamless integration with backend Langfuse feedback API without additional roundtrips.
- **Superior User Experience**: Dark/light theming, syntax highlighting, code copying, session continuity, and responsive layout across all device viewports.

### Negative & Mitigations
- **Vendored File Management**: Vendored JS/CSS files must be committed directly to source control. *Mitigation: These files are lightweight (<150KB combined) and rarely require updates.*
- **Client Storage Quotas**: Storing extensive chat histories in `localStorage` has a ~5MB origin limit. *Mitigation: The session catalog can prune conversations older than 30 days or cap stored messages per session if storage boundaries are approached.*
