# Ticket 07: Frontend Client Server-First Resumption, Session Switching, & Turn Edit/Fork UI

- **Milestone:** Unified Pluggable Conversation Memory (ADR-0016)
- **Status:** Pending
- **Dependency:** Ticket 06
- **Estimated Scope:** Medium

---

## 1. Goal & Context
Upgrade `static/js/app.js` with server-first session resumption and turn edit/branching controls:
1. **Server-First Resumption**: On page load, read `rag_current_session_id` from `storage`. Fetch `GET /api/chat/{session_id}`. If 200 OK, hydrate chat feed with previous messages, citation badges, source tags, and accordion traces. If 404, mint a fresh UUID.
2. **Session Enumeration & Switching**: Populate sidebar recent chats directly from `GET /api/chat/sessions`. Switching a session calls `GET /api/chat/{id}` and updates active session.
3. **Session Distinction**: Distinguish "New Chat" (minting a new UUID in `storage` while preserving the old session in backend/sidebar) from "Clear Conversation" (`DELETE /api/chat/{session_id}`).
4. **Turn Edit & Rewind UI**:
   - Add an edit button (✏️) on past user message bubbles.
   - Clicking Edit populates the textarea and renders an action banner:
     - Button 1: "Edit & Rewind Here" (calls stream chat with `rewind_turn_index: k`).
     - Button 2: "Branch to New Chat" (calls `POST /api/chat/{session_id}/fork`, switches active session, and streams the new turn).
     - Button 3: "Cancel".

---

## 2. Technical Requirements

### 2.1 Server-First Resumption (`initSessions`)
- When initializing:
  - Call `GET /api/chat/sessions` to populate `recentChatsList`.
  - Check stored `rag_current_session_id`.
  - If present:
    - `fetch("/api/chat/" + storedId)`
    - If response `ok`:
      - Clear DOM feed.
      - Parse JSON `turns`.
      - Loop through turns and render user bubble and assistant bubble (using existing `addMessage(role, text, source, citations, workflow, traceId, traceSteps)`).
    - If response 404:
      - Mint new UUID v4, set in storage, render welcome card.
  - If not present:
    - Mint new UUID v4, set in storage, render welcome card.

### 2.2 Turn Edit & Rewind / Branching Controls
- In `addMessage(role, text, ...)`:
  - If `role === "user"`:
    - Calculate `turnIndex` based on current number of user messages in feed.
    - Attach `data-turn-index` and inject an edit button `.btn-edit-turn`.
- Click handler on `.btn-edit-turn`:
  - Read user query text and `turnIndex`.
  - Populate input `question.value = text`.
  - Render `#turnActionBanner`:
    ```html
    <div id="turnActionBanner" class="turn-action-banner">
      <span class="banner-title">Editing Turn #${turnIndex + 1}</span>
      <button id="btnRewindSubmit" class="btn-sm btn-primary">Edit &amp; Rewind Here</button>
      <button id="btnForkSubmit" class="btn-sm btn-secondary">Branch to New Chat</button>
      <button id="btnCancelEdit" class="btn-sm btn-ghost">Cancel</button>
    </div>
    ```
- Handling "Edit & Rewind Here":
  - Set `pendingRewindTurnIndex = turnIndex`.
  - On submit: include `rewind_turn_index: pendingRewindTurnIndex` in request payload to `/api/chat/stream`.
  - Remove bubbles from DOM for turns $\ge \text{turnIndex}$.
  - Remove `#turnActionBanner`.
- Handling "Branch to New Chat":
  - Call `POST /api/chat/{currentSessionId}/fork` with `{upto_turn_index: turnIndex - 1}`.
  - On success:
    - Switch `currentSessionId = response.session_id`.
    - Update `storage.setItem("rag_current_session_id", currentSessionId)`.
    - Prune DOM bubbles for turns $\ge \text{turnIndex}$.
    - Submit stream request for the new edited question under the new `currentSessionId`.
    - Remove `#turnActionBanner`.

### 2.3 "New Chat" and "Clear History" Handlers
- `startNewChat()`:
  - Abort stream.
  - Mint new UUID v4.
  - Set as `currentSessionId`.
  - Clear message feed, show welcome card.
  - Refresh `GET /api/chat/sessions`.
- `deleteSession(sessionId)`:
  - Calls `DELETE /api/chat/{sessionId}`.
  - If active session, call `startNewChat()`.
  - Else, refresh session list.

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- Create `tests/test_frontend_memory.py` using Python's headless JS runner / mock fetch harness:
  - `test_init_sessions_resumes_from_server`: Mock `fetch` returning session with 2 turns; verify user/assistant bubbles and citations render in DOM.
  - `test_init_sessions_handles_404`: Mock `fetch` returning 404; verify new UUID generated and welcome card shown.
  - `test_edit_turn_renders_action_banner`: Trigger edit button on turn 1; verify `#turnActionBanner` appears with rewind and fork options.
  - `test_submit_rewind_sends_rewind_turn_index`: Click "Edit & Rewind Here"; verify stream request contains `rewind_turn_index: 1`.
  - `test_submit_fork_calls_fork_endpoint_and_switches_session`: Click "Branch to New Chat"; verify `POST /api/chat/{id}/fork` is called and active session ID changes.

### Green (Implementation)
- Update `static/js/app.js`.
- Add minimal styles for `#turnActionBanner` and `.btn-edit-turn` in `static/css/style.css` if needed.

### Refactor
- Ensure fallback to in-memory/localStorage cache if server fetch fails or is offline.
