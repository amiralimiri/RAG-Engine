# Technical Specification: Modernized Frontend Architecture and Observability UI

- **ADR Reference:** `docs/adr/0003-modernized-frontend-architecture-and-observability-ui.md`
- **Specification Document:** `docs/specs/0003-modernized-frontend-spec.md`
- **Target Version:** `0.3.0`
- **Status:** Approved / Ready for Implementation

---

## 1. System Overview

This specification details the frontend modernization of the Enterprise IT Support Copilot. The overhaul transforms the legacy vanilla template into a high-performance, responsive, zero-build single-page application with deep observability integration (Langfuse v5), interactive telemetry feedback, strict XSS defense-in-depth sanitization, code highlighting with copy affordances, dark/light theme switching, and local multi-session lifecycle management.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          Browser Client                                                │
│                                                                                                        │
│  ┌────────────────────────┐  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │   Responsive Sidebar   │  │                          Chat Canvas                                 │  │
│  │ - Brand & Workflow Info│  │  ┌────────────────────────────────────────────────────────────────┐  │  │
│  │ - "New Chat" Trigger   │  │  │ User Bubble: "How do I connect to the company VPN?"            │  │  │
│  │ - Recent Chats Catalog │  │  └────────────────────────────────────────────────────────────────┘  │  │
│  │   (Switch & Delete)    │  │  ┌────────────────────────────────────────────────────────────────┐  │  │
│  │ - Ingest Document Modal│  │  │ Assistant Bubble: Markdown content with Highlighted Code Block │  │  │
│  └────────────────────────┘  │  │  [bash]  nmcli con up id "CorpVPN"                 [Copy]     │  │  │
│                              │  │ -------------------------------------------------------------- │  │  │
│  ┌────────────────────────┐  │  │ ▼ Reasoning & Audit Steps (Collapsible)                        │  │  │
│  │     Top Header Bar     │  │  │   Source: [vectorstore]  Workflow: [fix_single_jev]            │  │  │
│  │ - Status Dot & Title   │  │  │   Trace:  [retrieve] -> [grade_evidence] -> [generate]         │  │  │
│  │ - Dark/Light Theme Btn │  │  │   Trace ID: [c7f9...3a12] (Click to Copy)                      │  │  │
│  └────────────────────────┘  │  │  [👍 Thumbs Up]  [👎 Thumbs Down]  [💬 Add Note]               │  │  │
│                              │  └────────────────────────────────────────────────────────────────┘  │  │
│                              │  ┌────────────────────────────────────────────────────────────────┐  │  │
│                              │  │ Composer Bar: [Workflow Select] [Prompt Input] [Send Button]   │  │  │
│                              │  └────────────────────────────────────────────────────────────────┘  │  │
│                              └──────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                                    │
                                     HTTP REST / JSON (Fetch API)
                                                    │
                                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                           FastAPI Backend                                              │
│                                                                                                        │
│   GET  /                        -> Serves index.html with app_name context                             │
│   GET  /static/*                -> Serves style.css, app.js, and static/vendor/*.{js,css}              │
│   GET  /api/workflows           -> ["fix_single_jev", "fix_single_llm"]                                │
│   POST /api/chat                -> { question, workflow, session_id } => { answer, trace, trace_id }   │
│   POST /api/feedback            -> { trace_id, score, name, comment } => BackgroundTasks telemetry     │
│   POST /api/ingest              -> Multipart form upload with X-Admin-Key                              │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. File Change Manifest

| File | Action | Description / Purpose |
|---|---|---|
| `static/vendor/marked.min.js` | **Create** | High-performance vendored Markdown parsing engine (~40KB). |
| `static/vendor/purify.min.js` | **Create** | Vendored DOMPurify sanitizer preventing prompt injection XSS (~45KB). |
| `static/vendor/highlight.min.js` | **Create** | Vendored Highlight.js core for code syntax highlighting (~55KB). |
| `static/vendor/highlight.theme.css` | **Create** | Adaptive syntax highlighting stylesheet supporting dark and light themes. |
| `static/css/style.css` | **Overwrite** | Modern CSS token system (CSS custom properties), mobile drawer, rich bubble layouts, accordions, and components. |
| `static/js/app.js` | **Overwrite** | Structured modular vanilla JS client (State, Storage, Markdown Pipeline, Telemetry, UI Rendering). |
| `templates/index.html` | **Modify** | Responsive, semantic HTML5 shell featuring topbar theme toggle, mobile hamburger, in-feed chat, and upload modal. |
| `tests/test_frontend_integration.py` | **Create** | Integration test suite verifying static asset delivery, endpoint schemas, and HTML parsing/security. |

---

## 3. Concrete Specifications

### 3.1 Vendored Dependencies & Asset Delivery
All third-party frontend dependencies must be committed locally under `static/vendor/` to allow completely offline, air-gapped, zero-latency execution:
1. **`marked.min.js`** (v12+): Fast Markdown parser configured with `gfm: true`, `breaks: true`.
2. **`purify.min.js`** (v3+): Safe DOM sanitizer stripping script tags, malicious attributes, and javascript URLs.
3. **`highlight.min.js`** (v11+): Core syntax highlighter with auto-language detection for standard languages (`bash`, `python`, `json`, `yaml`, `markdown`, `powershell`, `sql`).
4. **`highlight.theme.css`**: CSS variables-driven code highlight theme that harmonizes with both dark and light UI themes.

### 3.2 CSS Custom Property Token Architecture (`static/css/style.css`)
Styling is organized around a dual-theme token model implemented directly on `[data-theme="dark"]` (default) and `[data-theme="light"]`:

```css
:root, [data-theme="dark"] {
  --bg-primary: #0a0d12;
  --bg-secondary: #0f141c;
  --bg-surface: #141c26;
  --bg-surface-elevated: #1a2533;
  --bg-composer: #121923;
  
  --border-subtle: #1e293b;
  --border-muted: #273548;
  --border-focus: #3b82f6;
  
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  
  --accent-primary: #10b981;
  --accent-primary-hover: #059669;
  --accent-glow: rgba(16, 185, 129, 0.15);
  
  --tag-bg: #1e293b;
  --tag-text: #cbd5e1;
  
  --badge-vector-bg: #064e3b;
  --badge-vector-text: #6ee7b7;
  --badge-web-bg: #1e3a5f;
  --badge-web-text: #93c5fd;

  --code-bg: #090d14;
  --code-border: #1e293b;
}

[data-theme="light"] {
  --bg-primary: #f8fafc;
  --bg-secondary: #ffffff;
  --bg-surface: #f1f5f9;
  --bg-surface-elevated: #ffffff;
  --bg-composer: #ffffff;
  
  --border-subtle: #e2e8f0;
  --border-muted: #cbd5e1;
  --border-focus: #2563eb;
  
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-muted: #94a3b8;
  
  --accent-primary: #059669;
  --accent-primary-hover: #047857;
  --accent-glow: rgba(5, 150, 105, 0.12);
  
  --tag-bg: #e2e8f0;
  --tag-text: #334155;
  
  --badge-vector-bg: #d1fae5;
  --badge-vector-text: #065f46;
  --badge-web-bg: #dbeafe;
  --badge-web-text: #1e40af;

  --code-bg: #f1f5f9;
  --code-border: #e2e8f0;
}
```

#### Layout Grid & Responsive Topology
- **Desktop (≥ 1024px)**: Dual-pane grid `grid-template-columns: 280px 1fr` with fixed left sidebar and scrolling center chat workspace.
- **Tablet / Mobile (< 1024px)**: Single-column chat workspace with an off-canvas left drawer toggled via a hamburger button in the topbar.

---

### 3.3 Security & Markdown Pipeline Specification
To guarantee complete security against prompt injection and cross-site scripting (XSS):
1. **Parser Pipeline Contract**:
   ```javascript
   function renderMarkdown(rawText) {
     if (!rawText) return "";
     
     // 1. Configure marked with highlight extension
     const rawHtml = marked.parse(rawText, {
       gfm: true,
       breaks: true,
       highlight: (code, lang) => {
         const validLang = hljs.getLanguage(lang) ? lang : "plaintext";
         return hljs.highlight(code, { language: validLang }).value;
       }
     });

     // 2. Strict DOMPurify Sanitization
     return DOMPurify.sanitize(rawHtml, {
       USE_PROFILES: { html: true },
       ADD_ATTR: ["target", "rel", "data-code", "data-lang"],
       FORBID_TAGS: ["style", "script", "iframe", "object", "embed"],
       FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover"]
     });
   }
   ```
2. **Code Block Enhancement**:
   Every `<pre><code>` block parsed is post-processed or emitted with a toolbar:
   - Header with detected language title (e.g. `python`, `bash`, `text`).
   - Copy Code button:
     ```html
     <div class="code-block-wrapper">
       <div class="code-block-header">
         <span class="code-lang">python</span>
         <button class="copy-code-btn" type="button" aria-label="Copy code">
           <svg class="copy-icon">...</svg>
           <span class="copy-label">Copy</span>
         </button>
       </div>
       <pre><code class="hljs language-python">...</code></pre>
     </div>
     ```
   - On click: calls `navigator.clipboard.writeText(codeText)`, updates label to `Copied!` with a green checkmark icon, and resets after 2000ms.

---

### 3.4 Observability & Interactive Components

#### 3.4.1 Per-Message Reasoning & Audit Accordion
Every assistant response bubble renders a dedicated audit block beneath the main answer text:

```html
<details class="reasoning-accordion">
  <summary class="reasoning-summary">
    <div class="summary-left">
      <span class="reasoning-icon">⚡</span>
      <span class="summary-title">Reasoning & Audit Steps</span>
      <span class="source-badge badge-${source_type}">${escapeHtml(source_used)}</span>
    </div>
    <span class="accordion-arrow">›</span>
  </summary>
  <div class="reasoning-content">
    <div class="audit-row">
      <span class="audit-label">Workflow:</span>
      <span class="audit-value workflow-chip">${escapeHtml(workflow)}</span>
    </div>
    <div class="audit-row">
      <span class="audit-label">Langfuse Trace ID:</span>
      <div class="trace-id-chip" data-trace-id="${escapeHtml(trace_id)}" title="Click to copy Trace ID">
        <code>${escapeHtml(trace_id)}</code>
        <button class="copy-trace-btn" type="button" aria-label="Copy Trace ID">⎘</button>
      </div>
    </div>
    <div class="audit-steps-section">
      <div class="audit-label">Execution Trace:</div>
      <ol class="step-list">
        ${traceSteps.map(step => `<li class="step-item"><span class="step-dot"></span>${escapeHtml(step)}</li>`).join("")}
      </ol>
    </div>
  </div>
</details>
```

#### 3.4.2 Interactive Feedback Action Bar
Directly beneath the reasoning accordion, each assistant turn features feedback controls:

```html
<div class="feedback-bar" data-trace-id="${escapeHtml(trace_id)}">
  <button class="feedback-btn thumbs-up" title="Helpful answer" data-score="1.0">
    👍 <span>Helpful</span>
  </button>
  <button class="feedback-btn thumbs-down" title="Not helpful" data-score="0.0">
    👎 <span>Unhelpful</span>
  </button>
  <button class="feedback-btn add-note-btn" title="Add feedback note">
    💬 <span>Add Note</span>
  </button>
  <div class="feedback-status-msg"></div>
</div>
```

**Feedback Submission Contract**:
- Clicking 👍 or 👎 immediately locks the buttons (`btn.disabled = true`), adds `.selected` to the clicked button, and calls:
  ```javascript
  POST /api/feedback
  Content-Type: application/json
  {
    "trace_id": "<trace_id>",
    "score": 1.0, // or 0.0
    "name": "user_feedback",
    "comment": optionalCommentString || null
  }
  ```
- **Micro-Popover**: Clicking "Add Note" opens a floating tooltip containing a textarea and a submit button. Submitting sends the note along with the recorded or current score.

#### 3.4.3 In-Feed Agent "Thinking" Placeholder
When a query is dispatched:
- An animated placeholder element is injected into `#chat`:
  ```html
  <div id="thinkingBubble" class="message assistant thinking-bubble">
    <div class="avatar">AI</div>
    <div class="bubble thinking-content">
      <div class="radar-pulse"></div>
      <div class="thinking-text-wrap">
        <span class="thinking-title">Executing Agentic Workflow</span>
        <span class="thinking-meta">
          <span class="thinking-workflow">${escapeHtml(activeWorkflow)}</span>
          <span class="thinking-timer" id="thinkingTimer">0.0s</span>
        </span>
      </div>
    </div>
  </div>
  ```
- A timer updates `#thinkingTimer` every 100ms (`(Date.now() - startTime) / 1000`).
- Once `/api/chat` responds, `#thinkingBubble` is removed and replaced with the rendered assistant bubble.

#### 3.4.4 Toast Notification Component
A transient toast notification system placed at `#toastContainer` (bottom-right) communicates actions:
- `showToast("Trace ID copied to clipboard!", "success")`
- Automatically vanishes after 2500ms with smooth CSS fade and slide-out.

---

### 3.5 Client State & Session Lifecycle Specification

#### 3.5.1 Storage Schema
Client state is managed in `window.localStorage` with the following keys:
1. `rag_current_session_id`: String (UUID v4), identifies the active conversation.
2. `rag_session_<session_id>`: JSON object representing transcript and metadata:
   ```json
   {
     "id": "e3b0c442-98fc-1c14-9af0-2a8b5d73245f",
     "title": "How do I connect to the company VPN?",
     "createdAt": 1730000000000,
     "updatedAt": 1730000045000,
     "workflow": "fix_single_jev",
     "messages": [
       {
         "role": "user",
         "text": "How do I connect to the company VPN?",
         "timestamp": 1730000000000
       },
       {
         "role": "assistant",
         "text": "To connect to the corporate VPN, use...",
         "source_used": "vectorstore",
         "trace_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
         "trace": ["retrieve", "grade_evidence", "generate"],
         "workflow": "fix_single_jev",
         "citations": [],
         "timestamp": 1730000045000,
         "feedback": { "score": 1.0, "comment": null }
       }
     ]
   }
   ```
3. `rag_sessions_index`: JSON array of session descriptors for the sidebar catalog:
   ```json
   [
     {
       "id": "e3b0c442-98fc-1c14-9af0-2a8b5d73245f",
       "title": "How do I connect to the company VPN?",
       "updatedAt": 1730000045000,
       "messageCount": 2
     }
   ]
   ```
4. `rag_theme`: String (`"dark"` or `"light"`).

#### 3.5.2 Lifecycle Transitions
- **Application Startup**:
  1. Retrieve `localStorage.getItem('rag_current_session_id')`.
  2. If absent, generate a new UUID v4 via `crypto.randomUUID()`.
  3. Load transcript from `rag_session_<id>`. If messages exist, re-render them into `#chat`.
  4. Render sidebar recent chats from `rag_sessions_index`.
  5. Check theme: if `rag_theme` is stored, apply `document.documentElement.dataset.theme = theme`. Otherwise detect `window.matchMedia('(prefers-color-scheme: dark)').matches`.
- **"New Chat" Click**:
  1. Generate fresh `newSessionId = crypto.randomUUID()`.
  2. Set `rag_current_session_id = newSessionId`.
  3. Clear `#chat` viewport and insert the standard introductory AI greeting.
  4. Update sidebar active item highlighting.
- **Session Switching**:
  1. Clicking a session in the sidebar saves the current chat, updates `rag_current_session_id` to the clicked ID, loads its messages from `localStorage`, and renders the entire transcript.
- **Session Deletion**:
  1. Clicking the delete icon removes `rag_session_<id>` and updates `rag_sessions_index`. If the deleted session was active, automatically triggers "New Chat".

---

## 4. Testing & Verification Plan

### 4.1 Automated Backend & Static Integration Tests (`tests/test_frontend_integration.py`)
1. **Static Asset Availability**:
   - `GET /` returns `HTTP 200` with Content-Type `text/html; charset=utf-8`.
   - `GET /static/css/style.css` returns `HTTP 200` with Content-Type `text/css`.
   - `GET /static/js/app.js` returns `HTTP 200` with Content-Type `text/javascript` or `application/javascript`.
   - `GET /static/vendor/marked.min.js`, `GET /static/vendor/purify.min.js`, `GET /static/vendor/highlight.min.js`, `GET /static/vendor/highlight.theme.css` all return `HTTP 200`.
2. **API Endpoint Contract Tests**:
   - `GET /api/workflows` returns valid JSON with `workflows` array and `default`.
   - `POST /api/chat` with `{ question, workflow, session_id }` returns `HTTP 200` containing `answer`, `source_used`, `trace`, and `trace_id`.
   - `POST /api/feedback` with `{ trace_id, score, name, comment }` returns `HTTP 200` with `status: "accepted"`.
3. **HTML Shell Validation**:
   - Confirms `templates/index.html` includes script tags for vendor dependencies before `app.js`.
   - Verifies all required semantic IDs exist (`#chat`, `#chatForm`, `#question`, `#workflowSelect`, `#themeToggle`, `#newChatBtn`, `#sessionsList`, `#toastContainer`).

### 4.2 Security & Client-Side Manual Verification
1. **XSS Sanitization Boundary**:
   - Test assistant responses containing `<script>alert('xss')</script>`, `<img src=x onerror=alert(1)>`, and markdown `[click](javascript:alert(1))`.
   - Verify DOMPurify strips active exploits while preserving safe Markdown elements (`strong`, `pre`, `code`, `a` with `target="_blank"`).
2. **Offline Resilience**:
   - Disconnect network access after loading the page and verify that syntax highlighting and Markdown rendering continue to operate flawlessly from `/static/vendor/`.
3. **Responsive Breakpoint Verification**:
   - Test viewport widths `375px` (mobile), `768px` (tablet), `1280px` (desktop).
   - Ensure the sidebar collapses cleanly into a slide-out drawer on viewports < 1024px.
