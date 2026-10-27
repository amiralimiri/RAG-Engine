/* ==========================================================================
   Enterprise IT Support Copilot - Application Logic
   Ticket 4: Client State, Session Lifecycle & Theme Switcher
   Ticket 5: Markdown Pipeline, Security Sanitization & In-Feed Thinking State
   Ticket 6: Observability Reasoning Accordion, Telemetry Feedback & Toast Notifications
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
const toastContainer = $("toastContainer");
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
   Ticket 5: Markdown Pipeline, Syntax Highlighting & Security Sanitization
   ========================================================================== */
function findClosest(el, sel) {
  if (!el) return null;
  if (typeof el.closest === "function") return el.closest(sel);
  let cur = el;
  const isClass = sel.startsWith(".");
  const name = isClass ? sel.slice(1) : sel;
  while (cur) {
    if (isClass && cur.classList && cur.classList.contains(name)) return cur;
    if (!isClass && cur.tagName && cur.tagName.toLowerCase() === name.toLowerCase())
      return cur;
    cur = cur.parentNode;
  }
  return null;
}

function getMarked() {
  if (typeof marked !== "undefined") return marked;
  if (typeof window !== "undefined" && window.marked) return window.marked;
  if (typeof globalThis !== "undefined" && globalThis.marked) return globalThis.marked;
  return null;
}

function getDOMPurify() {
  if (typeof DOMPurify !== "undefined") return DOMPurify;
  if (typeof window !== "undefined" && window.DOMPurify) return window.DOMPurify;
  if (typeof globalThis !== "undefined" && globalThis.DOMPurify) return globalThis.DOMPurify;
  return null;
}

function getHljs() {
  if (typeof hljs !== "undefined") return hljs;
  if (typeof window !== "undefined" && window.hljs) return window.hljs;
  if (typeof globalThis !== "undefined" && globalThis.hljs) return globalThis.hljs;
  return null;
}

let markedConfigured = false;

function initMarked() {
  const markedObj = getMarked();
  const hljsObj = getHljs();
  if (!markedObj) return;

  const renderer = {
    code(tokenOrCode, maybeLang) {
      let codeText = "";
      let rawLang = "";
      if (typeof tokenOrCode === "object" && tokenOrCode !== null) {
        codeText = tokenOrCode.text || "";
        rawLang = tokenOrCode.lang || "";
      } else {
        codeText = String(tokenOrCode || "");
        rawLang = String(maybeLang || "");
      }

      const lang = (rawLang || "").trim().split(/\s+/)[0] || "text";
      const validLang =
        hljsObj && hljsObj.getLanguage && hljsObj.getLanguage(lang)
          ? lang
          : "plaintext";

      let highlighted = "";
      if (hljsObj && typeof hljsObj.highlight === "function") {
        try {
          highlighted = hljsObj.highlight(codeText, { language: validLang }).value;
        } catch (e) {
          highlighted = escapeHtml(codeText);
        }
      } else {
        highlighted = escapeHtml(codeText);
      }

      const displayLang = escapeHtml(lang);
      return (
        `<div class="code-block-wrapper">` +
        `<div class="code-block-header">` +
        `<span class="code-lang">${displayLang}</span>` +
        `<button class="copy-code-btn" type="button" aria-label="Copy code">` +
        `<svg class="copy-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
        `<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>` +
        `<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>` +
        `</svg>` +
        `<span class="copy-label">Copy</span>` +
        `</button>` +
        `</div>` +
        `<pre><code class="hljs language-${displayLang}">${highlighted}</code></pre>` +
        `</div>\n`
      );
    },
  };

  if (typeof markedObj.use === "function") {
    markedObj.use({
      gfm: true,
      breaks: true,
      renderer,
    });
  } else if (typeof markedObj.setOptions === "function") {
    markedObj.setOptions({
      gfm: true,
      breaks: true,
      renderer,
    });
  }
  markedConfigured = true;
}

const PURIFY_CONFIG = {
  USE_PROFILES: { html: true, svg: true },
  ADD_TAGS: ["svg", "path", "rect", "polyline", "line", "circle"],
  ADD_ATTR: [
    "target",
    "rel",
    "data-code",
    "data-lang",
    "viewBox",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "aria-hidden",
    "aria-label",
  ],
  FORBID_TAGS: ["style", "script", "iframe", "object", "embed"],
  FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover"],
};

function sanitizeHtmlFallback(html) {
  return String(html || "")
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(
      /<(style|iframe|object|embed)\b[^<]*(?:(?!<\/\1>)<[^<]*)*<\/\1>/gi,
      "",
    )
    .replace(/\bon\w+\s*=\s*(['"]).*?\1/gi, "")
    .replace(/\bon\w+\s*=\s*[^>\s]+/gi, "")
    .replace(/href\s*=\s*(['"])\s*javascript:[^'"]*\1/gi, 'href="#"');
}

function renderMarkdown(rawText) {
  if (!rawText) return "";

  const markedObj = getMarked();
  const purifyObj = getDOMPurify();

  let rawHtml = "";
  if (markedObj) {
    if (!markedConfigured) {
      initMarked();
    }
    try {
      rawHtml =
        typeof markedObj.parse === "function"
          ? markedObj.parse(rawText)
          : markedObj(rawText);
    } catch (e) {
      console.warn("marked.parse failed:", e);
      rawHtml = formatText(rawText);
    }
  } else {
    rawHtml = formatText(rawText);
  }

  if (purifyObj && typeof purifyObj.sanitize === "function") {
    return purifyObj.sanitize(rawHtml, PURIFY_CONFIG);
  }

  return sanitizeHtmlFallback(rawHtml);
}

/* ---------- Clipboard Copy Utility & Code Block Interactions ---------- */
async function copyToClipboard(text) {
  const nav =
    (typeof window !== "undefined" && window.navigator) ||
    (typeof navigator !== "undefined" ? navigator : null);

  if (nav && nav.clipboard && typeof nav.clipboard.writeText === "function") {
    try {
      await nav.clipboard.writeText(text);
      return true;
    } catch (e) {
      console.warn("navigator.clipboard.writeText failed:", e);
    }
  }

  if (typeof document !== "undefined") {
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      textarea.style.left = "-9999px";
      const target = document.body || document.documentElement;
      if (target && typeof target.appendChild === "function") {
        target.appendChild(textarea);
      }
      if (typeof textarea.select === "function") {
        textarea.select();
      }
      const success = document.execCommand
        ? document.execCommand("copy")
        : true;
      if (typeof textarea.remove === "function") {
        textarea.remove();
      }
      return success;
    } catch (e) {
      console.warn("execCommand fallback copy failed:", e);
    }
  }
  return false;
}

async function handleCopyCode(btn) {
  if (!btn) return;
  const wrapper = findClosest(btn, ".code-block-wrapper");
  const codeElem = wrapper
    ? wrapper.querySelector
      ? wrapper.querySelector("code")
      : null
    : null;
  const rawCode =
    btn.dataset && btn.dataset.code
      ? btn.dataset.code
      : codeElem
        ? codeElem.textContent
        : "";

  await copyToClipboard(rawCode);

  if (btn._copiedTimeout) {
    clearTimeout(btn._copiedTimeout);
  }

  if (!btn._originalHtml) {
    btn._originalHtml = btn.innerHTML;
  }

  btn.classList.add("copied");

  const labelSpan = btn.querySelector
    ? btn.querySelector(".copy-label")
    : null;
  if (labelSpan) {
    labelSpan.textContent = "Copied!";
  } else {
    btn.textContent = "Copied!";
  }

  const copyIcon = btn.querySelector
    ? btn.querySelector(".copy-icon")
    : null;
  if (copyIcon && typeof document !== "undefined" && document.createElement) {
    const checkIcon = document.createElement("svg");
    checkIcon.className = "check-icon";
    checkIcon.setAttribute("width", "13");
    checkIcon.setAttribute("height", "13");
    checkIcon.setAttribute("viewBox", "0 0 24 24");
    checkIcon.setAttribute("fill", "none");
    checkIcon.setAttribute("stroke", "currentColor");
    checkIcon.setAttribute("stroke-width", "2");
    checkIcon.setAttribute("stroke-linecap", "round");
    checkIcon.setAttribute("stroke-linejoin", "round");
    checkIcon.setAttribute("aria-hidden", "true");
    checkIcon.innerHTML = '<polyline points="20 6 9 17 4 12"></polyline>';
    if (typeof copyIcon.replaceWith === "function") {
      copyIcon.replaceWith(checkIcon);
    } else if (copyIcon.parentNode) {
      copyIcon.parentNode.replaceChild(checkIcon, copyIcon);
    }
  }

  btn._copiedTimeout = setTimeout(() => {
    btn.classList.remove("copied");
    if (btn._originalHtml) {
      btn.innerHTML = btn._originalHtml;
      delete btn._originalHtml;
    } else {
      if (labelSpan) labelSpan.textContent = "Copy";
    }
    delete btn._copiedTimeout;
  }, 2000);
}

/* ==========================================================================
   Ticket 5: In-Feed Thinking State Indicator & Timer Lifecycle
   ========================================================================== */
let thinkingTimerInterval = null;
let thinkingStartTime = null;

function showThinking(workflowName) {
  if (!thinkingBubble) return;

  const activeWf =
    workflowName ||
    (workflowSelect ? workflowSelect.value : "") ||
    "fix_single_llm";

  const wfEl =
    (thinkingBubble.querySelector
      ? thinkingBubble.querySelector(".thinking-workflow")
      : null) || $("thinkingWorkflow");
  if (wfEl) {
    wfEl.textContent = activeWf;
  }

  const timerEl =
    (thinkingBubble.querySelector
      ? thinkingBubble.querySelector(".thinking-timer")
      : null) || $("thinkingTimer");
  if (timerEl) {
    timerEl.textContent = "0.0s";
  }

  thinkingBubble.classList.remove("hidden");

  const targetContainer = chatMessages || chatArea;
  if (targetContainer) {
    targetContainer.appendChild(thinkingBubble);
  }

  if (chatArea) {
    chatArea.scrollTop = chatArea.scrollHeight;
  }

  if (thinkingTimerInterval) {
    clearInterval(thinkingTimerInterval);
  }

  thinkingStartTime = Date.now();
  thinkingTimerInterval = setInterval(() => {
    if (!thinkingStartTime) return;
    const elapsedSec = ((Date.now() - thinkingStartTime) / 1000).toFixed(1);
    const liveTimerEl =
      (thinkingBubble.querySelector
        ? thinkingBubble.querySelector(".thinking-timer")
        : null) || $("thinkingTimer");
    if (liveTimerEl) {
      liveTimerEl.textContent = `${elapsedSec}s`;
    }
  }, 100);
}

function hideThinking() {
  if (thinkingTimerInterval) {
    clearInterval(thinkingTimerInterval);
    thinkingTimerInterval = null;
  }
  thinkingStartTime = null;

  if (!thinkingBubble) return;
  thinkingBubble.classList.add("hidden");
  if (thinkingBubble.parentNode) {
    thinkingBubble.parentNode.removeChild(thinkingBubble);
  }
}

/* ==========================================================================
   Ticket 6: Floating Toast Notifications
   ========================================================================== */
function showToast(message, type = "info", duration = 3000) {
  if (typeof document === "undefined") return null;

  let container =
    toastContainer ||
    $("toastContainer") ||
    (typeof document.getElementById === "function"
      ? document.getElementById("toastContainer")
      : null);

  if (!container && typeof document.querySelector === "function") {
    container = document.querySelector("#toastContainer");
  }

  if (
    !container &&
    document.body &&
    typeof document.createElement === "function"
  ) {
    container = document.createElement("div");
    container.id = "toastContainer";
    container.className = "toast-container";
    container.setAttribute("aria-live", "polite");
    container.setAttribute("aria-atomic", "true");
    document.body.appendChild(container);
  }

  if (!container) return null;

  const validTypes = ["success", "info", "error"];
  const toastType = validTypes.includes(type) ? type : "info";

  const toast = document.createElement("div");
  let toastClass = "toast-info";
  if (toastType === "success") {
    toastClass = "toast-success";
  } else if (toastType === "error") {
    toastClass = "toast-error";
  }
  toast.className = `toast ${toastClass}`;
  toast.setAttribute("role", "status");

  let iconText = "ℹ";
  if (toastType === "success") iconText = "✓";
  else if (toastType === "error") iconText = "✕";

  toast.innerHTML = `<span class="toast-icon">${iconText}</span><span class="toast-message">${escapeHtml(message)}</span>`;

  if (typeof container.appendChild === "function") {
    container.appendChild(toast);
  }

  let dismissed = false;
  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    if (toast.classList && typeof toast.classList.add === "function") {
      toast.classList.add("toast-fade-out");
    }
    setTimeout(() => {
      if (
        toast.parentNode &&
        typeof toast.parentNode.removeChild === "function"
      ) {
        toast.parentNode.removeChild(toast);
      } else if (typeof toast.remove === "function") {
        toast.remove();
      }
    }, 260);
  };

  if (typeof toast.addEventListener === "function") {
    toast.addEventListener("click", dismiss);
  }

  if (duration && duration > 0) {
    setTimeout(dismiss, duration);
  }

  return toast;
}

/* ==========================================================================
   Ticket 6: Observability Reasoning Accordion & Source Badges
   ========================================================================== */
function getSourceBadgeClass(source) {
  const s = String(source || "")
    .toLowerCase()
    .trim();
  if (s.includes("vector") || s.includes("knowledge") || s.includes("store")) {
    return "badge-vectorstore";
  }
  if (s.includes("web") || s.includes("search")) {
    return "badge-web_search";
  }
  return `badge-${s.replace(/[^a-z0-9_-]/g, "_") || "default"}`;
}

async function handleCopyTraceId(traceChip) {
  if (!traceChip) return;
  const traceId =
    (traceChip.dataset && traceChip.dataset.traceId) ||
    (typeof traceChip.getAttribute === "function"
      ? traceChip.getAttribute("data-trace-id")
      : "") ||
    "";

  if (!traceId) return;

  await copyToClipboard(traceId);
  showToast("Trace ID copied for Langfuse debugging", "info");
}

/* ==========================================================================
   Ticket 6: Telemetry Feedback & Comment Micro-Popover
   ========================================================================== */
function updateMessageFeedback(traceId, feedbackData) {
  if (!currentSessionId) return;
  const sessionData = getSessionData(currentSessionId);
  if (!sessionData || !Array.isArray(sessionData.messages)) return;

  let msg = null;
  if (traceId) {
    msg = sessionData.messages.find(
      (m) => m.role === "assistant" && m.trace_id === traceId,
    );
  }
  if (!msg) {
    msg = [...sessionData.messages]
      .reverse()
      .find((m) => m.role === "assistant");
  }

  if (msg) {
    if (!msg.feedback) {
      msg.feedback = { score: null, comment: null };
    }
    if (feedbackData.score !== undefined) {
      msg.feedback.score = feedbackData.score;
    }
    if (feedbackData.comment !== undefined) {
      msg.feedback.comment = feedbackData.comment;
    }
    sessionData.updatedAt = Date.now();
    saveSessionData(currentSessionId, sessionData);
    updateSessionIndexMetadata(currentSessionId, sessionData);
  }
}

async function submitFeedback(
  traceId,
  score,
  comment = null,
  feedbackBar = null,
) {
  const numericScore = typeof score === "number" ? score : parseFloat(score);
  if (isNaN(numericScore) || numericScore < 0.0 || numericScore > 1.0) {
    showToast("Invalid feedback score", "error");
    return false;
  }

  if (!traceId) {
    showToast("No trace ID available for this response", "error");
    return false;
  }

  let thumbsUp = null;
  let thumbsDown = null;
  let statusMsg = null;

  if (feedbackBar) {
    thumbsUp = feedbackBar.querySelector
      ? feedbackBar.querySelector(".thumbs-up")
      : null;
    thumbsDown = feedbackBar.querySelector
      ? feedbackBar.querySelector(".thumbs-down")
      : null;
    statusMsg = feedbackBar.querySelector
      ? feedbackBar.querySelector(".feedback-status-msg")
      : null;

    // Optimistic locking: disable both thumbs buttons to prevent duplicate votes
    if (thumbsUp) thumbsUp.disabled = true;
    if (thumbsDown) thumbsDown.disabled = true;

    if (numericScore >= 0.5) {
      if (thumbsUp && thumbsUp.classList) thumbsUp.classList.add("selected");
      if (thumbsDown && thumbsDown.classList)
        thumbsDown.classList.remove("selected");
    } else {
      if (thumbsDown && thumbsDown.classList)
        thumbsDown.classList.add("selected");
      if (thumbsUp && thumbsUp.classList) thumbsUp.classList.remove("selected");
    }

    if (statusMsg) {
      statusMsg.textContent = comment
        ? "Submitting note..."
        : "Submitting feedback...";
    }
  }

  try {
    const payload = {
      trace_id: traceId,
      score: numericScore,
      name: "user_feedback",
      comment: comment || null,
    };

    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await readJsonSafe(res);
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    if (statusMsg) {
      statusMsg.textContent = comment
        ? "Feedback & note submitted!"
        : "Thanks for your feedback!";
    }

    showToast("Feedback submitted successfully!", "success");
    updateMessageFeedback(traceId, { score: numericScore, comment: comment });
    return true;
  } catch (err) {
    console.error("Feedback submission error:", err);
    if (feedbackBar) {
      // Gracefully unlock buttons on error
      if (thumbsUp) {
        thumbsUp.disabled = false;
        if (thumbsUp.classList) thumbsUp.classList.remove("selected");
      }
      if (thumbsDown) {
        thumbsDown.disabled = false;
        if (thumbsDown.classList) thumbsDown.classList.remove("selected");
      }
      if (statusMsg) {
        statusMsg.textContent = "";
      }
    }
    showToast(`Failed to submit feedback: ${err.message}`, "error");
    return false;
  }
}

function closeAllCommentPopovers() {
  if (typeof document === "undefined" || !document.querySelectorAll) return;
  const popovers = document.querySelectorAll(".comment-popover");
  popovers.forEach((p) => {
    if (p.parentNode && typeof p.parentNode.removeChild === "function") {
      p.parentNode.removeChild(p);
    } else if (typeof p.remove === "function") {
      p.remove();
    }
  });
}

function toggleCommentPopover(feedbackBar, traceId) {
  if (!feedbackBar || typeof document === "undefined") return;

  const existing = feedbackBar.querySelector
    ? feedbackBar.querySelector(".comment-popover")
    : null;
  if (existing) {
    if (
      existing.parentNode &&
      typeof existing.parentNode.removeChild === "function"
    ) {
      existing.parentNode.removeChild(existing);
    } else if (typeof existing.remove === "function") {
      existing.remove();
    }
    return;
  }

  closeAllCommentPopovers();

  const popover = document.createElement("div");
  popover.className = "comment-popover feedback-popover";
  popover.innerHTML =
    `<textarea placeholder="Add details or context (optional)..." aria-label="Feedback note"></textarea>` +
    `<div class="popover-actions">` +
    `<button type="button" class="popover-btn popover-cancel">Cancel</button>` +
    `<button type="button" class="popover-btn popover-submit">Submit</button>` +
    `</div>`;

  if (typeof feedbackBar.appendChild === "function") {
    feedbackBar.appendChild(popover);
  }

  const textarea = popover.querySelector
    ? popover.querySelector("textarea")
    : null;
  if (textarea && typeof textarea.focus === "function") {
    textarea.focus();
  }

  const cancelBtn = popover.querySelector
    ? popover.querySelector(".popover-cancel")
    : null;
  if (cancelBtn && typeof cancelBtn.addEventListener === "function") {
    cancelBtn.addEventListener("click", (e) => {
      if (e && typeof e.stopPropagation === "function") e.stopPropagation();
      if (
        popover.parentNode &&
        typeof popover.parentNode.removeChild === "function"
      ) {
        popover.parentNode.removeChild(popover);
      } else if (typeof popover.remove === "function") {
        popover.remove();
      }
    });
  }

  const submitBtn = popover.querySelector
    ? popover.querySelector(".popover-submit")
    : null;
  if (submitBtn && typeof submitBtn.addEventListener === "function") {
    submitBtn.addEventListener("click", async (e) => {
      if (e && typeof e.stopPropagation === "function") e.stopPropagation();
      const note = textarea ? textarea.value.trim() : "";
      if (
        popover.parentNode &&
        typeof popover.parentNode.removeChild === "function"
      ) {
        popover.parentNode.removeChild(popover);
      } else if (typeof popover.remove === "function") {
        popover.remove();
      }

      let score = 1.0;
      const upBtn = feedbackBar.querySelector
        ? feedbackBar.querySelector(".thumbs-up")
        : null;
      const downBtn = feedbackBar.querySelector
        ? feedbackBar.querySelector(".thumbs-down")
        : null;
      if (
        downBtn &&
        downBtn.classList &&
        downBtn.classList.contains("selected")
      ) {
        score = 0.0;
      } else if (
        upBtn &&
        upBtn.classList &&
        upBtn.classList.contains("selected")
      ) {
        score = 1.0;
      }

      await submitFeedback(traceId, score, note || null, feedbackBar);
    });
  }
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

function addMessage(
  role,
  text,
  source = "",
  citations = [],
  workflow = "",
  traceId = "",
  traceSteps = [],
  feedback = null,
) {
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

  let accordionHtml = "";
  let feedbackHtml = "";

  if (role === "assistant") {
    const sourceClass = getSourceBadgeClass(source);
    const displaySource = source || "vectorstore";
    const displayWorkflow =
      workflow ||
      (workflowSelect ? workflowSelect.value : "") ||
      "fix_single_llm";
    const safeTraceId = traceId || "";

    const traceIdChipHtml = safeTraceId
      ? `<div class="trace-id-chip" data-trace-id="${escapeHtml(safeTraceId)}" role="button" tabindex="0" title="Click to copy Trace ID">` +
        `<code>${escapeHtml(safeTraceId)}</code>` +
        `<button class="copy-trace-btn" type="button" aria-label="Copy Trace ID">⎘</button>` +
        `</div>`
      : `<span class="audit-value">—</span>`;

    const steps =
      Array.isArray(traceSteps) && traceSteps.length > 0
        ? traceSteps
        : ["direct"];

    const stepsListHtml = steps
      .map(
        (step) =>
          `<li class="step-item step-chip"><span class="step-dot"></span><span class="step-text">${escapeHtml(step)}</span></li>`,
      )
      .join("");

    accordionHtml =
      `<details class="reasoning-accordion">` +
      `<summary class="reasoning-summary">` +
      `<div class="summary-left">` +
      `<span class="reasoning-icon">⚡</span>` +
      `<span class="summary-title">Reasoning &amp; Audit Steps</span>` +
      `<span class="source-badge ${sourceClass}">${escapeHtml(displaySource)}</span>` +
      `<span class="workflow-chip">${escapeHtml(displayWorkflow)}</span>` +
      `</div>` +
      `<span class="accordion-arrow">›</span>` +
      `</summary>` +
      `<div class="reasoning-content">` +
      `<div class="audit-row">` +
      `<span class="audit-label">Workflow:</span>` +
      `<span class="audit-value workflow-chip">${escapeHtml(displayWorkflow)}</span>` +
      `</div>` +
      `<div class="audit-row">` +
      `<span class="audit-label">Langfuse Trace ID:</span>` +
      `${traceIdChipHtml}` +
      `</div>` +
      `<div class="audit-steps-section">` +
      `<div class="audit-label">Execution Trace:</div>` +
      `<ol class="step-list">` +
      `${stepsListHtml}` +
      `</ol>` +
      `</div>` +
      `</div>` +
      `</details>`;

    // Feedback Bar
    const upSelected =
      feedback && feedback.score !== null && Number(feedback.score) >= 0.5;
    const downSelected =
      feedback && feedback.score !== null && Number(feedback.score) < 0.5;
    const isLocked = Boolean(upSelected || downSelected);

    let initialStatus = "";
    if (feedback && feedback.score !== null) {
      initialStatus = feedback.comment
        ? "Feedback & note recorded"
        : "Feedback recorded";
    }

    feedbackHtml =
      `<div class="feedback-bar" data-trace-id="${escapeHtml(safeTraceId)}">` +
      `<button class="feedback-btn thumbs-up${upSelected ? " selected" : ""}" title="Helpful answer" data-score="1.0" type="button"${isLocked ? " disabled" : ""}>` +
      `👍 <span>Helpful</span>` +
      `</button>` +
      `<button class="feedback-btn thumbs-down${downSelected ? " selected" : ""}" title="Not helpful" data-score="0.0" type="button"${isLocked ? " disabled" : ""}>` +
      `👎 <span>Unhelpful</span>` +
      `</button>` +
      `<button class="feedback-btn add-note-btn" title="Add feedback note" type="button">` +
      `💬 <span>Add Note</span>` +
      `</button>` +
      `<div class="feedback-status-msg">${escapeHtml(initialStatus)}</div>` +
      `</div>`;
  }

  const avatarLabel = role === "user" ? "You" : "AI";
  const bodyHtml =
    role === "assistant" ? renderMarkdown(text) : formatText(text);
  wrap.innerHTML = `<div class="avatar">${avatarLabel}</div><div class="bubble">${bodyHtml}${citeHtml}${accordionHtml}${feedbackHtml}</div>`;

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
        msg.trace_id || "",
        msg.trace || [],
        msg.feedback || null,
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
  hideThinking();
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
  hideThinking();
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

  // 3. Update status displays & activate agentic thinking state
  renderTrace(["Running workflow..."]);
  if (sourceUsed) sourceUsed.textContent = "Running";
  const activeWf = workflowSelect
    ? workflowSelect.value || "fix_single_llm"
    : sessionData.workflow || "fix_single_llm";
  if (workflowUsed) workflowUsed.textContent = activeWf;

  const btn = sendBtn || (form ? form.querySelector("button") : null);
  if (btn) btn.disabled = true;
  if (question) question.disabled = true;
  if (workflowSelect) workflowSelect.disabled = true;

  showThinking(activeWf);

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

    // Hide thinking indicator before injecting assistant bubble
    hideThinking();

    // 4. Render assistant response into DOM
    addMessage(
      "assistant",
      data.answer,
      data.source_used,
      data.citations || [],
      data.workflow || activeWf,
      data.trace_id || "",
      data.trace || [],
      null,
    );
    renderTrace(data.trace || []);
    if (sourceUsed) sourceUsed.textContent = data.source_used;
    if (workflowUsed) workflowUsed.textContent = data.workflow || activeWf;

    // 5. Persist assistant message to session
    const responseNow = Date.now();
    sessionData.updatedAt = responseNow;
    sessionData.messages.push({
      role: "assistant",
      text: data.answer,
      source_used: data.source_used,
      trace_id: data.trace_id || null,
      trace: data.trace || [],
      workflow: data.workflow || activeWf,
      citations: data.citations || [],
      timestamp: responseNow,
      feedback: { score: null, comment: null },
    });
    saveSessionData(currentSessionId, sessionData);
    updateSessionIndexMetadata(currentSessionId, sessionData);
  } catch (e) {
    hideThinking();
    addMessage(
      "assistant",
      `Error: ${e.message}`,
      "error",
      [],
      workflowSelect ? workflowSelect.value || "" : "",
      "",
      ["Request failed"],
      null,
    );
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
    hideThinking();
    if (btn) btn.disabled = false;
    if (question) {
      question.disabled = false;
      question.focus();
    }
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

// Delegated event listener for one-click code copy buttons, trace ID copying, and feedback
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", (e) => {
    const target = e.target;

    // 1. One-click code copy button
    const btn = findClosest(target, ".copy-code-btn");
    if (btn) {
      if (typeof e.preventDefault === "function") e.preventDefault();
      handleCopyCode(btn);
      return;
    }

    // 2. Interactive trace_id chip click-to-copy
    const traceChip = findClosest(target, ".trace-id-chip");
    if (traceChip) {
      if (typeof e.preventDefault === "function") e.preventDefault();
      handleCopyTraceId(traceChip);
      return;
    }

    // 3. Feedback buttons (thumbs up / thumbs down / add-note)
    const feedbackBtn = findClosest(target, ".feedback-btn");
    if (feedbackBtn) {
      const feedbackBar = findClosest(feedbackBtn, ".feedback-bar");
      if (!feedbackBar) return;
      const traceId =
        (feedbackBar.dataset && feedbackBar.dataset.traceId) ||
        (typeof feedbackBar.getAttribute === "function"
          ? feedbackBar.getAttribute("data-trace-id")
          : "") ||
        "";

      if (feedbackBtn.classList && feedbackBtn.classList.contains("thumbs-up")) {
        if (feedbackBtn.disabled) return;
        if (typeof e.preventDefault === "function") e.preventDefault();
        submitFeedback(traceId, 1.0, null, feedbackBar);
        return;
      }

      if (
        feedbackBtn.classList &&
        feedbackBtn.classList.contains("thumbs-down")
      ) {
        if (feedbackBtn.disabled) return;
        if (typeof e.preventDefault === "function") e.preventDefault();
        submitFeedback(traceId, 0.0, null, feedbackBar);
        return;
      }

      if (
        feedbackBtn.classList &&
        feedbackBtn.classList.contains("add-note-btn")
      ) {
        if (typeof e.preventDefault === "function") e.preventDefault();
        toggleCommentPopover(feedbackBar, traceId);
        return;
      }
    }

    // 4. Click outside comment popovers closes them
    if (
      !findClosest(target, ".comment-popover") &&
      !findClosest(target, ".add-note-btn")
    ) {
      closeAllCommentPopovers();
    }
  });

  // Keyboard accessibility for Trace ID chip (Enter / Space)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const traceChip = findClosest(e.target, ".trace-id-chip");
      if (traceChip) {
        if (typeof e.preventDefault === "function") e.preventDefault();
        handleCopyTraceId(traceChip);
      }
    }
  });
}

/* ==========================================================================
   Initialization Bootstrap
   ========================================================================== */
initTheme();
initSessions();
initMobileSidebar();
loadWorkflows();
hideThinking();

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
    // Ticket 5 exports
    renderMarkdown,
    initMarked,
    copyToClipboard,
    handleCopyCode,
    showThinking,
    hideThinking,
    askAgent,
    addMessage,
    renderSessionTranscript,
    getThinkingInterval: () => thinkingTimerInterval,
    // Ticket 6 exports
    showToast,
    submitFeedback,
    updateMessageFeedback,
    getSourceBadgeClass,
    handleCopyTraceId,
    toggleCommentPopover,
    closeAllCommentPopovers,
  };
}
