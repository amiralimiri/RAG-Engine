/* ==========================================================================
   Enterprise IT Support Copilot - Application Logic
   Ticket 4: Client State, Session Lifecycle & Theme Switcher
   ========================================================================== */

/* ---------- DOM Element Bindings ---------- */
const $ = (id) =>
  typeof document !== "undefined" && document.getElementById
    ? document.getElementById(id)
    : null;

const chatArea = $("chat");
const chatMessages = $("chatMessages") || chatArea;
const welcomeCard = $("welcomeCard");
const thinkingBubble = $("thinkingBubble");
const form = $("chatForm");
const question = $("messageInput") || $("question");
const sendBtn = $("sendBtn");
const trace = $("trace");
const sourceUsed = $("sourceUsed");
const workflowSelect = $("workflowSelect");
const workflowUsed = $("workflowUsed");
const themeToggleBtn = $("themeToggleBtn");
const newChatBtn = $("newChatBtn");
const recentChatsList = $("recentChatsList");
const clearHistoryBtn = $("clearHistoryBtn");
const sidebar = $("sidebar");
const sidebarBackdrop = $("sidebarBackdrop");
const sidebarToggleBtn = $("sidebarToggleBtn");
const hamburgerBtn = $("hamburgerBtn");
const modal = $("uploadModal");
const openUpload = $("openUpload");
const openIngestModalBtn = $("openIngestModalBtn");
const closeUpload = $("closeUpload");
const uploadBtn = $("uploadBtn");

/* ---------- Storage Key Constants ---------- */
const STORAGE_KEY_THEME = "rag_theme";
const STORAGE_KEY_CURRENT_SESSION = "rag_current_session_id";
const STORAGE_KEY_SESSIONS_INDEX = "rag_sessions_index";
const STORAGE_PREFIX_SESSION = "rag_session_";

/* In-memory fallback if localStorage is disabled or throws */
const memoryStorage = {};
const storage = {
  getItem(key) {
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        return window.localStorage.getItem(key);
      }
    } catch (e) {
      console.warn("localStorage.getItem failed, using memory fallback", e);
    }
    return Object.prototype.hasOwnProperty.call(memoryStorage, key)
      ? memoryStorage[key]
      : null;
  },
  setItem(key, value) {
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        window.localStorage.setItem(key, value);
        return;
      }
    } catch (e) {
      console.warn("localStorage.setItem failed, using memory fallback", e);
    }
    memoryStorage[key] = String(value);
  },
  removeItem(key) {
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        window.localStorage.removeItem(key);
      }
    } catch (e) {
      console.warn("localStorage.removeItem failed, using memory fallback", e);
    }
    delete memoryStorage[key];
  },
  getAllKeys() {
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        return Object.keys(window.localStorage);
      }
    } catch (e) {
      console.warn("localStorage.getAllKeys failed, using memory fallback", e);
    }
    return Object.keys(memoryStorage);
  },
};

/* ---------- UUID v4 Generator Helper ---------- */
function generateUUID() {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.getRandomValues === "function"
  ) {
    return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
      (
        Number(c) ^
        (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (Number(c) / 4)))
      ).toString(16),
    );
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/* ---------- Text Escaping & Formatting Utilities ---------- */
function escapeHtml(s = "") {
  return String(s).replace(
    /[&<>'"]/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
      })[c],
  );
}

function formatText(s = "") {
  return escapeHtml(s).replace(/\n/g, "<br>");
}

async function readJsonSafe(response) {
  const text = await response.text();
  if (!text)
    return { detail: `HTTP ${response.status} ${response.statusText}` };
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

function formatRelativeTime(timestamp) {
  if (!timestamp) return "";
  const now = Date.now();
  const diffSec = Math.floor((now - timestamp) / 1000);
  if (diffSec < 45) return "Just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays}d ago`;
  const d = new Date(timestamp);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* ==========================================================================
   Theme Management
   ========================================================================== */
function getSystemTheme() {
  if (
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: light)").matches
  ) {
    return "light";
  }
  return "dark";
}

function getInitialTheme() {
  const stored = storage.getItem(STORAGE_KEY_THEME);
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  // Check if HTML already received a theme (e.g. from inline anti-FOUC script)
  if (
    typeof document !== "undefined" &&
    document.documentElement &&
    (document.documentElement.dataset.theme === "light" ||
      document.documentElement.dataset.theme === "dark")
  ) {
    return document.documentElement.dataset.theme;
  }
  return getSystemTheme();
}

function updateThemeToggleUI(theme) {
  if (!themeToggleBtn) return;
  const sunIcon = themeToggleBtn.querySelector(".sun-icon");
  const moonIcon = themeToggleBtn.querySelector(".moon-icon");

  if (theme === "dark") {
    if (sunIcon) sunIcon.style.display = "block";
    if (moonIcon) moonIcon.style.display = "none";
    themeToggleBtn.setAttribute("aria-label", "Switch to light theme");
    themeToggleBtn.setAttribute("title", "Switch to light theme");
  } else {
    if (sunIcon) sunIcon.style.display = "none";
    if (moonIcon) moonIcon.style.display = "block";
    themeToggleBtn.setAttribute("aria-label", "Switch to dark theme");
    themeToggleBtn.setAttribute("title", "Switch to dark theme");
  }
}

function setTheme(theme, persist = true) {
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.dataset.theme = theme;
    document.documentElement.setAttribute("data-theme", theme);
  }
  if (persist) {
    storage.setItem(STORAGE_KEY_THEME, theme);
  }
  updateThemeToggleUI(theme);
}

function initTheme() {
  const current = getInitialTheme();
  setTheme(current, false);

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", () => {
      const activeTheme =
        document.documentElement.dataset.theme === "light" ? "light" : "dark";
      const nextTheme = activeTheme === "dark" ? "light" : "dark";
      setTheme(nextTheme, true);
    });
  }

  // Auto-detect system OS theme changes if user hasn't chosen an explicit override
  if (typeof window !== "undefined" && window.matchMedia) {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = (e) => {
      if (!storage.getItem(STORAGE_KEY_THEME)) {
        setTheme(e.matches ? "dark" : "light", false);
      }
    };
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", listener);
    } else if (typeof media.addListener === "function") {
      media.addListener(listener);
    }
  }
}

/* ==========================================================================
   Session Lifecycle & Storage Engine
   ========================================================================== */
let currentSessionId = null;

function getSessionsIndex() {
  const raw = storage.getItem(STORAGE_KEY_SESSIONS_INDEX);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    console.warn("Failed to parse sessions index:", e);
    return [];
  }
}

function saveSessionsIndex(index) {
  storage.setItem(STORAGE_KEY_SESSIONS_INDEX, JSON.stringify(index));
}

function getSessionData(sessionId) {
  if (!sessionId) return null;
  const raw = storage.getItem(`${STORAGE_PREFIX_SESSION}${sessionId}`);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    console.warn(`Failed to parse session data for ${sessionId}:`, e);
    return null;
  }
}

function saveSessionData(sessionId, data) {
  if (!sessionId || !data) return;
  storage.setItem(
    `${STORAGE_PREFIX_SESSION}${sessionId}`,
    JSON.stringify(data),
  );
}

function deleteSessionData(sessionId) {
  if (!sessionId) return;
  storage.removeItem(`${STORAGE_PREFIX_SESSION}${sessionId}`);
}

function updateSessionIndexMetadata(sessionId, sessionData) {
  let index = getSessionsIndex();
  const existingIdx = index.findIndex((s) => s.id === sessionId);
  const title = sessionData.title || "New Chat";
  const updatedAt = sessionData.updatedAt || Date.now();
  const messageCount =
    sessionData.messages && Array.isArray(sessionData.messages)
      ? sessionData.messages.length
      : 0;

  const entry = { id: sessionId, title, updatedAt, messageCount };

  if (existingIdx >= 0) {
    index[existingIdx] = entry;
  } else {
    index.unshift(entry);
  }

  // Sort descending by most recent
  index.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  saveSessionsIndex(index);
  renderRecentChatsList();
}

function renderRecentChatsList() {
  if (!recentChatsList) return;
  const sessions = getSessionsIndex();
  recentChatsList.innerHTML = "";

  if (sessions.length === 0) {
    const emptyLi = document.createElement("li");
    emptyLi.className = "empty-sessions";
    emptyLi.style.cssText =
      "padding: 12px 10px; font-size: 12px; color: var(--text-muted); text-align: center;";
    emptyLi.textContent = "No recent conversations";
    recentChatsList.appendChild(emptyLi);
    return;
  }

  sessions.forEach((session) => {
    const li = document.createElement("li");
    const isActive = session.id === currentSessionId;
    li.className = `session-item ${isActive ? "active" : ""}`;
    li.dataset.sessionId = session.id;
    li.setAttribute("tabindex", "0");
    li.setAttribute("role", "button");
    li.setAttribute("aria-label", `Load conversation: ${session.title}`);

    const titleDiv = document.createElement("div");
    titleDiv.className = "session-title";
    titleDiv.setAttribute(
      "title",
      `${session.title} (${formatRelativeTime(session.updatedAt)})`,
    );

    const titleText = document.createElement("span");
    titleText.className = "session-text";
    titleText.textContent = session.title;

    const timeSpan = document.createElement("span");
    timeSpan.className = "session-time";
    timeSpan.style.cssText =
      "font-size: 10px; color: var(--text-muted); margin-left: 6px; font-weight: normal; flex-shrink: 0;";
    timeSpan.textContent = formatRelativeTime(session.updatedAt);

    titleDiv.appendChild(titleText);
    titleDiv.appendChild(timeSpan);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "delete-session-btn";
    deleteBtn.type = "button";
    deleteBtn.setAttribute("aria-label", "Delete conversation");
    deleteBtn.setAttribute("title", "Delete conversation");
    deleteBtn.innerHTML = "&times;";

    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(session.id);
    });

    li.appendChild(titleDiv);
    li.appendChild(deleteBtn);

    li.addEventListener("click", () => {
      switchSession(session.id);
    });

    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        switchSession(session.id);
      }
    });

    recentChatsList.appendChild(li);
  });
}

function clearMessageFeed() {
  if (chatMessages) {
    const messages = chatMessages.querySelectorAll(
      ".message:not(.thinking-bubble)",
    );
    messages.forEach((m) => m.remove());
  }
  if (welcomeCard) {
    welcomeCard.classList.remove("hidden");
  }
}

function addMessage(role, text, source = "", citations = [], workflow = "") {
  if (welcomeCard) {
    welcomeCard.classList.add("hidden");
  }

  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;

  const citeHtml =
    citations && citations.length
      ? `<div class="citations"><strong>Sources</strong><br>${citations
          .map((c) =>
            c.url
              ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>`
              : escapeHtml(c.title),
          )
          .join("<br>")}</div>`
      : "";

  const metaHtml = [
    source
      ? `<div class="answer-source">Source: ${escapeHtml(source)}</div>`
      : "",
    workflow
      ? `<div class="workflow-tag">Workflow: ${escapeHtml(workflow)}</div>`
      : "",
  ].join("");

  const avatarLabel = role === "user" ? "You" : "AI";
  wrap.innerHTML = `<div class="avatar">${avatarLabel}</div><div class="bubble">${formatText(text)}${metaHtml}${citeHtml}</div>`;

  if (
    thinkingBubble &&
    chatMessages &&
    thinkingBubble.parentNode === chatMessages
  ) {
    chatMessages.insertBefore(wrap, thinkingBubble);
  } else if (chatMessages) {
    chatMessages.appendChild(wrap);
  }

  if (chatArea) {
    chatArea.scrollTop = chatArea.scrollHeight;
  }
}

function renderTrace(items = []) {
  if (!trace) return;
  trace.innerHTML = items && items.length
    ? items
        .map((x) => `<div class="trace-item">${escapeHtml(x)}</div>`)
        .join("")
    : '<div class="empty">No trace.</div>';
}

function renderSessionTranscript(sessionData) {
  clearMessageFeed();

  if (
    sessionData &&
    Array.isArray(sessionData.messages) &&
    sessionData.messages.length > 0
  ) {
    if (welcomeCard) welcomeCard.classList.add("hidden");
    sessionData.messages.forEach((msg) => {
      addMessage(
        msg.role,
        msg.text,
        msg.source_used || "",
        msg.citations || [],
        msg.workflow || "",
      );
    });

    // Update status bar with last assistant response if available
    const lastAssistant = [...sessionData.messages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (lastAssistant) {
      renderTrace(lastAssistant.trace || []);
      if (sourceUsed)
        sourceUsed.textContent = lastAssistant.source_used || "—";
      if (workflowUsed)
        workflowUsed.textContent =
          lastAssistant.workflow || sessionData.workflow || "—";
    }
  } else {
    if (welcomeCard) welcomeCard.classList.remove("hidden");
    renderTrace([]);
    if (sourceUsed) sourceUsed.textContent = "—";
    if (workflowUsed)
      workflowUsed.textContent = workflowSelect
        ? workflowSelect.value || "—"
        : "—";
  }
}

function startNewChat() {
  const newSessionId = generateUUID();
  currentSessionId = newSessionId;
  storage.setItem(STORAGE_KEY_CURRENT_SESSION, currentSessionId);

  clearMessageFeed();
  renderTrace([]);
  if (sourceUsed) sourceUsed.textContent = "—";
  if (workflowUsed)
    workflowUsed.textContent = workflowSelect
      ? workflowSelect.value || "—"
      : "—";

  renderRecentChatsList();
  closeSidebar();

  if (question) {
    question.value = "";
    question.style.height = "auto";
    question.focus();
  }
}

function switchSession(sessionId) {
  if (!sessionId) return;
  if (sessionId === currentSessionId) {
    closeSidebar();
    return;
  }

  currentSessionId = sessionId;
  storage.setItem(STORAGE_KEY_CURRENT_SESSION, currentSessionId);

  const sessionData = getSessionData(sessionId);
  renderSessionTranscript(sessionData);

  if (sessionData && sessionData.workflow && workflowSelect) {
    const options = workflowSelect.options ? Array.from(workflowSelect.options) : [];
    const matchingOption = options.find(
      (opt) => opt.value === sessionData.workflow,
    );
    if (matchingOption) {
      workflowSelect.value = sessionData.workflow;
    }
  }

  renderRecentChatsList();
  closeSidebar();

  if (chatArea) {
    chatArea.scrollTop = chatArea.scrollHeight;
  }
}

function deleteSession(sessionId) {
  if (!sessionId) return;
  deleteSessionData(sessionId);

  let index = getSessionsIndex().filter((s) => s.id !== sessionId);
  saveSessionsIndex(index);

  if (sessionId === currentSessionId) {
    startNewChat();
  } else {
    renderRecentChatsList();
  }
}

function clearAllHistory() {
  if (
    typeof window !== "undefined" &&
    typeof window.confirm === "function" &&
    !window.confirm("Are you sure you want to clear all chat history?")
  ) {
    return;
  }

  const index = getSessionsIndex();
  index.forEach((s) => deleteSessionData(s.id));

  // Purge any orphan session entries
  const allKeys = storage.getAllKeys();
  allKeys.forEach((key) => {
    if (key && key.startsWith(STORAGE_PREFIX_SESSION)) {
      storage.removeItem(key);
    }
  });

  saveSessionsIndex([]);
  startNewChat();
}

function initSessions() {
  let storedSessionId = storage.getItem(STORAGE_KEY_CURRENT_SESSION);
  if (!storedSessionId) {
    storedSessionId = generateUUID();
    storage.setItem(STORAGE_KEY_CURRENT_SESSION, storedSessionId);
  }
  currentSessionId = storedSessionId;

  const sessionData = getSessionData(currentSessionId);
  renderSessionTranscript(sessionData);
  renderRecentChatsList();
}

/* ==========================================================================
   Mobile Sidebar Drawer Toggling
   ========================================================================== */
function openSidebar() {
  if (sidebar) sidebar.classList.add("open");
  if (sidebarBackdrop) {
    sidebarBackdrop.classList.add("active");
    sidebarBackdrop.setAttribute("aria-hidden", "false");
  }
}

function closeSidebar() {
  if (sidebar) sidebar.classList.remove("open");
  if (sidebarBackdrop) {
    sidebarBackdrop.classList.remove("active");
    sidebarBackdrop.setAttribute("aria-hidden", "true");
  }
}

function toggleSidebar() {
  if (sidebar && sidebar.classList.contains("open")) {
    closeSidebar();
  } else {
    openSidebar();
  }
}

function initMobileSidebar() {
  if (sidebarToggleBtn) {
    sidebarToggleBtn.addEventListener("click", toggleSidebar);
  }
  if (hamburgerBtn && hamburgerBtn !== sidebarToggleBtn) {
    hamburgerBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleSidebar();
    });
  }
  if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener("click", closeSidebar);
  }
}

/* ==========================================================================
   Workflow Backend Selector
   ========================================================================== */
async function loadWorkflows() {
  if (!workflowSelect) return;
  try {
    const res = await fetch("/api/workflows");
    if (!res.ok) throw new Error("failed");
    const data = await res.json();
    const workflows = data.workflows || [];
    if (!workflows.length) throw new Error("empty");
    workflowSelect.innerHTML = workflows
      .map((w) => `<option value="${escapeHtml(w)}">${escapeHtml(w)}</option>`)
      .join("");
    if (data.default && workflows.includes(data.default)) {
      workflowSelect.value = data.default;
    }
  } catch {
    workflowSelect.innerHTML = `<option value="fix_single_llm">fix_single_llm</option>`;
  }
}

/* ==========================================================================
   Chat Agent Interaction
   ========================================================================== */
async function askAgent(q) {
  if (!q || !q.trim()) return;
  const promptText = q.trim();

  // Ensure an active session ID exists
  if (!currentSessionId) {
    currentSessionId = generateUUID();
    storage.setItem(STORAGE_KEY_CURRENT_SESSION, currentSessionId);
  }

  // 1. Render user message into DOM
  addMessage("user", promptText);
  if (question) {
    question.value = "";
    question.style.height = "auto";
  }

  // 2. Persist user message to session
  const now = Date.now();
  let sessionData = getSessionData(currentSessionId);
  if (!sessionData) {
    sessionData = {
      id: currentSessionId,
      title:
        promptText.length > 45 ? `${promptText.slice(0, 42)}...` : promptText,
      createdAt: now,
      updatedAt: now,
      workflow: workflowSelect ? workflowSelect.value || "" : "",
      messages: [],
    };
  }
  sessionData.updatedAt = now;
  sessionData.workflow = workflowSelect
    ? workflowSelect.value || sessionData.workflow
    : sessionData.workflow;
  sessionData.messages.push({
    role: "user",
    text: promptText,
    timestamp: now,
  });
  saveSessionData(currentSessionId, sessionData);
  updateSessionIndexMetadata(currentSessionId, sessionData);

  // 3. Update status displays
  renderTrace(["Running workflow..."]);
  if (sourceUsed) sourceUsed.textContent = "Running";
  if (workflowUsed)
    workflowUsed.textContent = workflowSelect ? workflowSelect.value || "default" : "default";

  const btn = sendBtn || (form ? form.querySelector("button") : null);
  if (btn) btn.disabled = true;
  if (workflowSelect) workflowSelect.disabled = true;

  try {
    const body = {
      question: promptText,
      session_id: currentSessionId,
    };
    if (workflowSelect && workflowSelect.value) {
      body.workflow = workflowSelect.value;
    }

    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await readJsonSafe(res);
    if (!res.ok) throw new Error(data.detail || "Request failed");

    // 4. Render assistant response into DOM
    addMessage(
      "assistant",
      data.answer,
      data.source_used,
      data.citations || [],
      data.workflow || "",
    );
    renderTrace(data.trace || []);
    if (sourceUsed) sourceUsed.textContent = data.source_used;
    if (workflowUsed) workflowUsed.textContent = data.workflow || "—";

    // 5. Persist assistant message to session
    const responseNow = Date.now();
    sessionData.updatedAt = responseNow;
    sessionData.messages.push({
      role: "assistant",
      text: data.answer,
      source_used: data.source_used,
      trace_id: data.trace_id || null,
      trace: data.trace || [],
      workflow: data.workflow || "",
      citations: data.citations || [],
      timestamp: responseNow,
      feedback: { score: null, comment: null },
    });
    saveSessionData(currentSessionId, sessionData);
    updateSessionIndexMetadata(currentSessionId, sessionData);
  } catch (e) {
    addMessage("assistant", `Error: ${e.message}`);
    renderTrace(["Request failed"]);
    if (sourceUsed) sourceUsed.textContent = "Error";
    if (workflowUsed) workflowUsed.textContent = "Error";

    const errorNow = Date.now();
    sessionData.updatedAt = errorNow;
    sessionData.messages.push({
      role: "assistant",
      text: `Error: ${e.message}`,
      source_used: "error",
      trace_id: null,
      trace: ["Request failed"],
      workflow: workflowSelect ? workflowSelect.value || "" : "",
      citations: [],
      timestamp: errorNow,
      feedback: { score: null, comment: null },
    });
    saveSessionData(currentSessionId, sessionData);
    updateSessionIndexMetadata(currentSessionId, sessionData);
  } finally {
    if (btn) btn.disabled = false;
    if (workflowSelect) workflowSelect.disabled = false;
  }
}

/* ==========================================================================
   Composer Events & Document Ingestion
   ========================================================================== */
if (form) {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!question) return;
    const q = question.value.trim();
    if (q) askAgent(q);
  });
}

if (question) {
  // Enter submits, Shift+Enter new line
  question.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (form) {
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.dispatchEvent(new Event("submit", { cancelable: true }));
        }
      }
    }
  });

  // Auto-resize textarea height
  question.addEventListener("input", () => {
    question.style.height = "auto";
    question.style.height = Math.min(question.scrollHeight, 200) + "px";
  });
}

// Example prompts click handlers
if (typeof document !== "undefined" && document.querySelectorAll) {
  document.querySelectorAll(".example").forEach((b) =>
    b.addEventListener("click", () => askAgent(b.textContent.trim())),
  );
}

// New Chat button
if (newChatBtn) {
  newChatBtn.addEventListener("click", () => {
    startNewChat();
  });
}

// Clear chat history button
if (clearHistoryBtn) {
  clearHistoryBtn.addEventListener("click", () => {
    clearAllHistory();
  });
}

// Document ingestion modal triggers
if (openUpload) {
  openUpload.addEventListener("click", () => {
    if (modal) modal.classList.remove("hidden");
  });
}
if (openIngestModalBtn) {
  openIngestModalBtn.addEventListener("click", () => {
    if (modal) modal.classList.remove("hidden");
  });
}
if (closeUpload) {
  closeUpload.addEventListener("click", () => {
    if (modal) modal.classList.add("hidden");
  });
}
if (modal) {
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });
}

// Global keyboard shortcuts (Escape closes drawers & modals)
if (typeof window !== "undefined" && window.addEventListener) {
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeSidebar();
      if (modal) modal.classList.add("hidden");
    }
  });
}

// Document upload action
if (uploadBtn) {
  uploadBtn.onclick = async () => {
    const fileInput = document.getElementById("fileInput");
    const adminKey = document.getElementById("adminKey");
    const status = document.getElementById("uploadStatus");
    const file = fileInput ? fileInput.files[0] : null;
    const key = adminKey ? adminKey.value : "";

    if (!file) {
      if (status) status.textContent = "Choose a file first.";
      return;
    }
    if (status) status.textContent = "Indexing document...";

    const fd = new FormData();
    fd.append("file", file);

    try {
      const r = await fetch("/api/ingest", {
        method: "POST",
        headers: { "X-Admin-Key": key },
        body: fd,
      });
      const d = await readJsonSafe(r);
      if (!r.ok) throw new Error(d.detail || "Upload failed");
      if (status)
        status.textContent = `Indexed ${d.file}: ${d.chunks} chunks.`;
    } catch (e) {
      if (status) status.textContent = `Error: ${e.message}`;
    }
  };
}

/* ==========================================================================
   Initialization Bootstrap
   ========================================================================== */
initTheme();
initSessions();
initMobileSidebar();
loadWorkflows();

/* Export for Node/test environments if available */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    generateUUID,
    getInitialTheme,
    setTheme,
    getSessionsIndex,
    saveSessionsIndex,
    getSessionData,
    saveSessionData,
    deleteSessionData,
    startNewChat,
    switchSession,
    deleteSession,
    clearAllHistory,
    formatRelativeTime,
  };
}
