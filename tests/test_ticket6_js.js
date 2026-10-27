/**
 * Verification Test Suite for Ticket 6
 * - Toast Notification System (showToast, auto-dismiss, styles, dom cleanup)
 * - Observability Reasoning Accordion (details.reasoning-accordion, badges, steps, trace_id chip)
 * - Interactive Trace ID Copying with Toast Notification
 * - Asynchronous Feedback Action Bar (optimistic locking, thumbs up/down, micro-popover, network error recovery)
 * - State Persistence in Session Transcript (localStorage reload lifecycle)
 */

const assert = require("assert");

// In-memory mock storage
const storage = new Map();
const localStorageMock = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
  clear: () => storage.clear(),
  getAllKeys: () => Array.from(storage.keys()),
};

class ClassListMock {
  constructor() {
    this._set = new Set();
  }
  add(c) {
    if (c) this._set.add(c);
  }
  remove(c) {
    this._set.delete(c);
  }
  contains(c) {
    return this._set.has(c);
  }
  toggle(c) {
    if (this._set.has(c)) {
      this._set.delete(c);
      return false;
    }
    this._set.add(c);
    return true;
  }
}

class MockElement {
  constructor(tagName = "div", id = "") {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.classList = new ClassListMock();
    this.attributes = {};
    this.style = {};
    this.listeners = {};
    this.value = "";
    this._textContent = "";
    this._innerHTML = "";
    this.dataset = {};
    this.disabled = false;
    this.parentNode = null;
  }

  get className() {
    return Array.from(this.classList._set).join(" ");
  }
  set className(v) {
    this.classList._set.clear();
    if (v) {
      String(v)
        .split(/\s+/)
        .filter(Boolean)
        .forEach((c) => this.classList.add(c));
    }
  }

  get textContent() {
    if (this.children.length === 0) return this._textContent;
    return this.children.map((c) => c.textContent).join("");
  }
  set textContent(v) {
    this._textContent = String(v);
    this.children = [];
  }

  get innerHTML() {
    return this._innerHTML;
  }
  set innerHTML(htmlStr) {
    this._innerHTML = String(htmlStr);
    this.children = parseHtmlStack(this._innerHTML);
    for (const ch of this.children) {
      ch.parentNode = this;
    }
  }

  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k.startsWith("data-")) {
      const camel = k
        .slice(5)
        .replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[camel] = String(v);
    }
  }
  getAttribute(k) {
    return this.attributes[k] || null;
  }
  removeAttribute(k) {
    delete this.attributes[k];
  }

  addEventListener(evt, fn) {
    if (!this.listeners[evt]) this.listeners[evt] = [];
    this.listeners[evt].push(fn);
  }
  dispatchEvent(evt) {
    const type = typeof evt === "string" ? evt : evt.type;
    const list = this.listeners[type] || [];
    for (const fn of list) fn(evt);
  }

  appendChild(child) {
    if (child.parentNode && child.parentNode !== this) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    if (!this.children.includes(child)) {
      this.children.push(child);
    }
    return child;
  }

  insertBefore(child, ref) {
    if (child.parentNode && child.parentNode !== this) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    const idx = this.children.indexOf(ref);
    if (idx >= 0) this.children.splice(idx, 0, child);
    else this.children.push(child);
    return child;
  }

  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) {
      this.children.splice(idx, 1);
      child.parentNode = null;
    }
    return child;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  closest(sel) {
    let cur = this;
    const isClass = sel.startsWith(".");
    const name = isClass ? sel.slice(1) : sel;
    while (cur) {
      if (isClass && cur.classList && cur.classList.contains(name)) return cur;
      if (
        !isClass &&
        cur.tagName &&
        cur.tagName.toLowerCase() === name.toLowerCase()
      )
        return cur;
      cur = cur.parentNode;
    }
    return null;
  }

  querySelector(sel) {
    for (const ch of this.children) {
      if (sel.startsWith(".") && ch.classList.contains(sel.slice(1))) return ch;
      if (sel.startsWith("#") && ch.id === sel.slice(1)) return ch;
      if (ch.tagName && ch.tagName.toLowerCase() === sel.toLowerCase())
        return ch;
      const found = ch.querySelector(sel);
      if (found) return found;
    }
    return null;
  }

  querySelectorAll(sel) {
    const res = [];
    const walk = (node) => {
      for (const ch of node.children) {
        if (sel.startsWith(".") && ch.classList.contains(sel.slice(1))) {
          res.push(ch);
        } else if (sel.startsWith("#") && ch.id === sel.slice(1)) {
          res.push(ch);
        } else if (ch.tagName && ch.tagName.toLowerCase() === sel.toLowerCase()) {
          res.push(ch);
        }
        walk(ch);
      }
    };
    walk(this);
    return res;
  }

  focus() {}
}

/**
 * Stack-based HTML string parser into MockElement tree
 */
function parseHtmlStack(html) {
  const root = new MockElement("root");
  const stack = [root];
  const tagRegex = /<!--[\s\S]*?-->|<(?:\/([a-z0-9_-]+)|([a-z0-9_-]+)([^>]*?)(\/?))>|([^<]+)/gi;
  const voidTags = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);

  let match;
  while ((match = tagRegex.exec(html)) !== null) {
    const [full, closeTag, openTag, attrsStr, selfClose, text] = match;

    if (full.startsWith("<!--")) {
      continue;
    }

    if (closeTag) {
      const closing = closeTag.toUpperCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === closing) {
          stack.length = i;
          break;
        }
      }
    } else if (openTag) {
      const elem = new MockElement(openTag);
      if (attrsStr) {
        const attrRegex = /([a-z0-9_-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/gi;
        let aMatch;
        while ((aMatch = attrRegex.exec(attrsStr)) !== null) {
          const aName = aMatch[1];
          const aVal = aMatch[2] !== undefined ? aMatch[2] : (aMatch[3] !== undefined ? aMatch[3] : (aMatch[4] !== undefined ? aMatch[4] : ""));
          elem.setAttribute(aName, aVal);
          if (aName === "class") {
            elem.className = aVal;
          } else if (aName === "id") {
            elem.id = aVal;
          } else if (aName === "disabled") {
            elem.disabled = true;
          }
        }
      }

      const parent = stack[stack.length - 1];
      parent.appendChild(elem);

      const isVoid = selfClose === "/" || voidTags.has(openTag.toLowerCase());
      if (!isVoid) {
        stack.push(elem);
      }
    } else if (text && text.trim()) {
      const parent = stack[stack.length - 1];
      const textNode = new MockElement("span");
      textNode.textContent = text;
      parent.appendChild(textNode);
    }
  }

  return root.children;
}

// Global DOM setup
const docElements = {
  chat: new MockElement("section", "chat"),
  chatMessages: new MockElement("div", "chatMessages"),
  welcomeCard: new MockElement("div", "welcomeCard"),
  thinkingBubble: new MockElement("div", "thinkingBubble"),
  toastContainer: new MockElement("div", "toastContainer"),
  chatForm: new MockElement("form", "chatForm"),
  messageInput: new MockElement("textarea", "messageInput"),
  sendBtn: new MockElement("button", "sendBtn"),
  trace: new MockElement("div", "trace"),
  sourceUsed: new MockElement("div", "sourceUsed"),
  workflowSelect: new MockElement("select", "workflowSelect"),
  workflowUsed: new MockElement("div", "workflowUsed"),
  themeToggleBtn: new MockElement("button", "themeToggleBtn"),
  newChatBtn: new MockElement("button", "newChatBtn"),
  recentChatsList: new MockElement("ul", "recentChatsList"),
  clearHistoryBtn: new MockElement("button", "clearHistoryBtn"),
  sidebar: new MockElement("aside", "sidebar"),
  sidebarBackdrop: new MockElement("div", "sidebarBackdrop"),
  sidebarToggleBtn: new MockElement("button", "sidebarToggleBtn"),
  hamburgerBtn: new MockElement("span", "hamburgerBtn"),
  uploadModal: new MockElement("div", "uploadModal"),
};

docElements.chat.appendChild(docElements.chatMessages);
docElements.chatMessages.appendChild(docElements.welcomeCard);
docElements.workflowSelect.value = "fix_single_llm";

let copiedClipboardData = null;
const clipboardMock = {
  writeText: async (text) => {
    copiedClipboardData = text;
    return true;
  },
};

global.window = {
  localStorage: localStorageMock,
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  navigator: { clipboard: clipboardMock },
  addEventListener: () => {},
};
global.localStorage = localStorageMock;
global.navigator = global.window.navigator;

global.document = {
  documentElement: {
    dataset: {},
    setAttribute: () => {},
  },
  body: new MockElement("body"),
  getElementById: (id) => docElements[id] || null,
  querySelector: (sel) => {
    if (sel === "#toastContainer") return docElements.toastContainer;
    return null;
  },
  querySelectorAll: (sel) => {
    if (sel === ".comment-popover") {
      return docElements.chatMessages.querySelectorAll(".comment-popover");
    }
    return [];
  },
  createElement: (tag) => new MockElement(tag),
  addEventListener: (evt, fn) => {
    if (!docElements.chat.listeners[evt]) docElements.chat.listeners[evt] = [];
    docElements.chat.listeners[evt].push(fn);
  },
};

// Mock fetch
let fetchCalls = [];
let mockFetchResponse = { ok: true, status: 200, json: async () => ({ status: "accepted" }) };

global.fetch = async (url, opts) => {
  fetchCalls.push({ url, opts });
  return {
    ok: mockFetchResponse.ok,
    status: mockFetchResponse.status,
    statusText: mockFetchResponse.ok ? "OK" : "Error",
    text: async () => JSON.stringify(await mockFetchResponse.json()),
    json: mockFetchResponse.json,
  };
};

// Require application module
const app = require("../static/js/app.js");

async function runTicket6Tests() {
  console.log("=== RUNNING TICKET 6 VERIFICATION SUITE ===");

  // --------------------------------------------------------------------------
  // Test 1: Toast Notification System
  // --------------------------------------------------------------------------
  console.log("Test 1: Toast Notification System (showToast, types, auto-dismiss)");
  docElements.toastContainer.children = [];

  const infoToast = app.showToast("Test info message", "info", 100);
  assert.ok(infoToast, "showToast should return a toast element");
  assert.ok(infoToast.classList.contains("toast"), "Toast must have .toast class");
  assert.ok(infoToast.classList.contains("toast-info"), "Toast must have .toast-info class");
  assert.ok(infoToast.innerHTML.includes("Test info message"), "Toast must render the message text");
  assert.strictEqual(docElements.toastContainer.children.length, 1, "Toast appended to container");

  const successToast = app.showToast("Success done", "success", 100);
  assert.ok(successToast.classList.contains("toast-success"), "Must support 'success' type");

  const errorToast = app.showToast("An error occurred", "error", 100);
  assert.ok(errorToast.classList.contains("toast-error"), "Must support 'error' type");

  // Verify auto-dismissal
  await new Promise((r) => setTimeout(r, 400));
  assert.ok(infoToast.classList.contains("toast-fade-out"), "Toast must have .toast-fade-out after timer expires");
  console.log("  Passed: Toast notification creation, type classes, and auto-dismiss lifecycle verified");

  // --------------------------------------------------------------------------
  // Test 2: Source Badge Class Mapping
  // --------------------------------------------------------------------------
  console.log("Test 2: Source Badge Class Determination");
  assert.strictEqual(app.getSourceBadgeClass("vectorstore"), "badge-vectorstore");
  assert.strictEqual(app.getSourceBadgeClass("knowledge_base"), "badge-vectorstore");
  assert.strictEqual(app.getSourceBadgeClass("web_search"), "badge-web_search");
  assert.strictEqual(app.getSourceBadgeClass("web"), "badge-web_search");
  assert.strictEqual(app.getSourceBadgeClass("direct"), "badge-direct");
  console.log("  Passed: Source badges correctly map to badge-vectorstore and badge-web_search");

  // --------------------------------------------------------------------------
  // Test 3: Per-Message Reasoning & Audit Accordion Rendering
  // --------------------------------------------------------------------------
  console.log("Test 3: Per-Message Reasoning & Audit Accordion Markup");
  docElements.chatMessages.children = [];

  app.addMessage(
    "assistant",
    "Here is your answer regarding VPN access.",
    "vectorstore",
    [],
    "fix_single_jev",
    "trace-test-uuid-1234",
    ["retrieve", "grade_evidence", "generate"],
    null,
  );

  const assistantMessage = docElements.chatMessages.children[0];
  assert.ok(assistantMessage, "Assistant message was added to chatMessages");
  assert.ok(assistantMessage.classList.contains("assistant"), "Must have .assistant class");

  const accordion = assistantMessage.querySelector(".reasoning-accordion");
  assert.ok(accordion, "Must include details.reasoning-accordion element");
  assert.strictEqual(accordion.tagName, "DETAILS", "Accordion tag must be <details>");

  const summary = accordion.querySelector(".reasoning-summary");
  assert.ok(summary, "Accordion must contain summary.reasoning-summary");

  const sourceBadge = summary.querySelector(".source-badge");
  assert.ok(sourceBadge, "Summary must contain .source-badge");
  assert.ok(sourceBadge.classList.contains("badge-vectorstore"), "Source badge must have .badge-vectorstore");
  assert.strictEqual(sourceBadge.textContent.trim(), "vectorstore");

  const workflowChip = summary.querySelector(".workflow-chip");
  assert.ok(workflowChip, "Summary must contain active workflow chip");
  assert.strictEqual(workflowChip.textContent.trim(), "fix_single_jev");

  const traceIdChip = accordion.querySelector(".trace-id-chip");
  assert.ok(traceIdChip, "Accordion content must include .trace-id-chip");
  assert.strictEqual(traceIdChip.getAttribute("data-trace-id"), "trace-test-uuid-1234");
  assert.ok(traceIdChip.querySelector("code"), "Trace ID chip must contain <code> tag");

  const stepItems = accordion.querySelectorAll(".step-item");
  assert.strictEqual(stepItems.length, 3, "Must render exactly 3 step items for trace");
  assert.ok(stepItems[0].textContent.includes("retrieve"));
  assert.ok(stepItems[1].textContent.includes("grade_evidence"));
  assert.ok(stepItems[2].textContent.includes("generate"));
  console.log("  Passed: details.reasoning-accordion renders summary badges, trace ID, and step list");

  // --------------------------------------------------------------------------
  // Test 4: Interactive Trace ID Copying & Toast Trigger
  // --------------------------------------------------------------------------
  console.log("Test 4: Interactive Trace ID Click-to-Copy");
  copiedClipboardData = null;
  docElements.toastContainer.children = [];

  await app.handleCopyTraceId(traceIdChip);
  assert.strictEqual(copiedClipboardData, "trace-test-uuid-1234", "Trace ID must be written to clipboard");

  const toastElem = docElements.toastContainer.children[0];
  assert.ok(toastElem, "showToast must be triggered on copy");
  assert.ok(
    toastElem.innerHTML.includes("Trace ID copied for Langfuse debugging") ||
      toastElem.innerHTML.includes("Trace ID copied"),
    "Toast message must confirm Trace ID copy action",
  );
  assert.ok(toastElem.classList.contains("toast-info"), "Toast should use info level");
  console.log("  Passed: Trace ID copied to clipboard with toast confirmation");

  // --------------------------------------------------------------------------
  // Test 5: Asynchronous Feedback Action Bar & Optimistic Locking
  // --------------------------------------------------------------------------
  console.log("Test 5: Feedback Action Bar & Optimistic Locking (Thumbs Up / Down)");
  const feedbackBar = assistantMessage.querySelector(".feedback-bar");
  assert.ok(feedbackBar, "Assistant message must include .feedback-bar");
  assert.strictEqual(feedbackBar.getAttribute("data-trace-id"), "trace-test-uuid-1234");

  const thumbsUp = feedbackBar.querySelector(".thumbs-up");
  const thumbsDown = feedbackBar.querySelector(".thumbs-down");
  const addNoteBtn = feedbackBar.querySelector(".add-note-btn");
  const statusMsg = feedbackBar.querySelector(".feedback-status-msg");

  assert.ok(thumbsUp && thumbsDown && addNoteBtn && statusMsg, "All feedback bar controls must exist");
  assert.strictEqual(thumbsUp.getAttribute("data-score"), "1.0");
  assert.strictEqual(thumbsDown.getAttribute("data-score"), "0.0");

  fetchCalls = [];
  mockFetchResponse = {
    ok: true,
    status: 200,
    json: async () => ({ status: "accepted", trace_id: "trace-test-uuid-1234" }),
  };

  const success = await app.submitFeedback("trace-test-uuid-1234", 1.0, null, feedbackBar);
  assert.strictEqual(success, true, "submitFeedback should return true on success");
  assert.ok(thumbsUp.classList.contains("selected"), "Thumbs up button must receive .selected class");
  assert.strictEqual(thumbsUp.disabled, true, "Thumbs up button must be locked (disabled)");
  assert.strictEqual(thumbsDown.disabled, true, "Thumbs down button must be locked (disabled)");
  assert.ok(statusMsg.textContent.includes("feedback"), "Status message must confirm feedback submission");

  assert.strictEqual(fetchCalls.length, 1, "Exactly one POST /api/feedback dispatched");
  assert.strictEqual(fetchCalls[0].url, "/api/feedback");
  const sentPayload = JSON.parse(fetchCalls[0].opts.body);
  assert.strictEqual(sentPayload.trace_id, "trace-test-uuid-1234");
  assert.strictEqual(sentPayload.score, 1.0);
  assert.strictEqual(sentPayload.name, "user_feedback");
  assert.strictEqual(sentPayload.comment, null);
  console.log("  Passed: Thumbs up optimistic locking and POST /api/feedback verified");

  // --------------------------------------------------------------------------
  // Test 6: Feedback Graceful Network Error Recovery
  // --------------------------------------------------------------------------
  console.log("Test 6: Graceful Unlock on Feedback Network Failure");
  // Reset buttons
  thumbsUp.disabled = false;
  thumbsDown.disabled = false;
  thumbsUp.classList.remove("selected");

  mockFetchResponse = {
    ok: false,
    status: 500,
    json: async () => ({ detail: "Database connection timed out" }),
  };

  const failed = await app.submitFeedback("trace-test-uuid-1234", 0.0, null, feedbackBar);
  assert.strictEqual(failed, false, "submitFeedback should return false on error");
  assert.strictEqual(thumbsDown.classList.contains("selected"), false, "Selected state must be removed on error");
  assert.strictEqual(thumbsUp.disabled, false, "Thumbs up must be unlocked");
  assert.strictEqual(thumbsDown.disabled, false, "Thumbs down must be unlocked");
  console.log("  Passed: Network error unlocks buttons and cleans up optimistic lock");

  // --------------------------------------------------------------------------
  // Test 7: Micro-Popover Comment Workflow
  // --------------------------------------------------------------------------
  console.log("Test 7: Micro-Popover Comment Workflow");
  app.toggleCommentPopover(feedbackBar, "trace-test-uuid-1234");
  const popover = feedbackBar.querySelector(".comment-popover");
  assert.ok(popover, "Micro-popover must be injected into feedbackBar");
  const textarea = popover.querySelector("textarea");
  assert.ok(textarea, "Popover must contain a textarea");
  const cancelBtn = popover.querySelector(".popover-cancel");
  const submitBtn = popover.querySelector(".popover-submit");
  assert.ok(cancelBtn && submitBtn, "Popover must contain Cancel and Submit buttons");

  // Test toggling off
  app.toggleCommentPopover(feedbackBar, "trace-test-uuid-1234");
  assert.strictEqual(feedbackBar.querySelector(".comment-popover"), null, "Toggling again removes popover");

  // Open and submit with comment
  app.toggleCommentPopover(feedbackBar, "trace-test-uuid-1234");
  const activePopover = feedbackBar.querySelector(".comment-popover");
  const activeTextarea = activePopover.querySelector("textarea");
  activeTextarea.value = "Great explanation of VPN protocols!";

  fetchCalls = [];
  mockFetchResponse = {
    ok: true,
    status: 200,
    json: async () => ({ status: "accepted" }),
  };

  const activeSubmitBtn = activePopover.querySelector(".popover-submit");
  await activeSubmitBtn.dispatchEvent({ type: "click", stopPropagation: () => {} });

  assert.strictEqual(fetchCalls.length, 1, "Submitting popover dispatches /api/feedback");
  const commentPayload = JSON.parse(fetchCalls[0].opts.body);
  assert.strictEqual(commentPayload.comment, "Great explanation of VPN protocols!");
  console.log("  Passed: Micro-popover allows attaching optional comment notes");

  // --------------------------------------------------------------------------
  // Test 8: Session Persistence of Feedback Across Reload
  // --------------------------------------------------------------------------
  console.log("Test 8: Historical Feedback State Persistence Across Reload");
  docElements.chatMessages.children = [];

  const mockSessionData = {
    id: "session-hist-1",
    title: "History Chat",
    updatedAt: Date.now(),
    workflow: "fix_single_jev",
    messages: [
      {
        role: "user",
        text: "How to reset token?",
      },
      {
        role: "assistant",
        text: "Use the self-service portal.",
        source_used: "vectorstore",
        trace_id: "trace-hist-999",
        trace: ["retrieve", "generate"],
        workflow: "fix_single_jev",
        citations: [],
        feedback: { score: 1.0, comment: "Resolved quickly" },
      },
    ],
  };

  // Render restored transcript
  app.renderSessionTranscript(mockSessionData);
  const restoredMsg = docElements.chatMessages.children.find((c) =>
    c.classList.contains("assistant"),
  );
  assert.ok(restoredMsg, "Restored assistant message rendered");

  const restoredFeedbackBar = restoredMsg.querySelector(".feedback-bar");
  assert.ok(restoredFeedbackBar, "Restored message has feedback bar");
  const restoredUpBtn = restoredFeedbackBar.querySelector(".thumbs-up");
  assert.ok(restoredUpBtn.classList.contains("selected"), "Saved thumbs-up vote must remain .selected");
  assert.strictEqual(restoredUpBtn.disabled, true, "Saved vote must remain locked");

  const restoredStatus = restoredFeedbackBar.querySelector(".feedback-status-msg");
  assert.ok(restoredStatus.textContent.includes("recorded"), "Status message reflects recorded feedback");
  console.log("  Passed: Historical trace and feedback state persist cleanly across transcript reloads");

  console.log("\n==========================================");
  console.log(" ALL 8 TICKET 6 VERIFICATION CHECKS PASSED ");
  console.log("==========================================");
}

runTicket6Tests().catch((err) => {
  console.error("Test failed:", err);
  process.exit(1);
});
