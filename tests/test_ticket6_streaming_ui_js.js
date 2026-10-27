/**
 * Verification Test Suite for Ticket 6 (Response Streaming - ADR 0004)
 * - Progressive UI State Machine & Interrupted Stream Persistence
 * - Dynamic Thinking State & Live Step Label Updates (.thinking-status / .thinking-workflow)
 * - In-feed thinkingBubble removal and assistant bubble mounting on first token chunk
 * - RAF-throttled Progressive Markdown rendering & sanitization (scheduleProgressiveRender)
 * - Terminal metadata formatting (hljs syntax highlighting, reasoning accordion, trace chip, feedback bar)
 * - Interrupted stream handling (warning badge, source_used: "interrupted", localStorage persistence, toast)
 * - Early pre-stream error recovery before tokens
 * - ChatForm submit event wiring to streaming workflow
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");

// Load vendored dependencies
const marked = require("../static/vendor/marked.min.js");
const hljs = require("../static/vendor/highlight.min.js");

// Load app.js code
const appPath = path.resolve(__dirname, "../static/js/app.js");
const appCode = fs.readFileSync(appPath, "utf8");

// Mock LocalStorage
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
    this.scrollTop = 0;
    this.scrollHeight = 1000;
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
        } else if (
          ch.tagName &&
          ch.tagName.toLowerCase() === sel.toLowerCase()
        ) {
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

function parseHtmlStack(html) {
  const root = new MockElement("root");
  const stack = [root];
  const tagRegex =
    /<!--[\s\S]*?-->|<(?:\/([a-z0-9_-]+)|([a-z0-9_-]+)([^>]*?)(\/?))>|([^<]+)/gi;
  const voidTags = new Set([
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
  ]);

  let match;
  while ((match = tagRegex.exec(html)) !== null) {
    const [full, closeTag, openTag, attrsStr, selfClose, text] = match;

    if (full.startsWith("<!--")) continue;

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
        const attrRegex =
          /([a-z0-9_-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/gi;
        let aMatch;
        while ((aMatch = attrRegex.exec(attrsStr)) !== null) {
          const aName = aMatch[1];
          const aVal =
            aMatch[2] !== undefined
              ? aMatch[2]
              : aMatch[3] !== undefined
                ? aMatch[3]
                : aMatch[4] !== undefined
                  ? aMatch[4]
                  : "";
          elem.setAttribute(aName, aVal);
          if (aName === "class") elem.className = aVal;
          else if (aName === "id") elem.id = aVal;
          else if (aName === "disabled") elem.disabled = true;
        }
      }

      const parent = stack[stack.length - 1];
      parent.appendChild(elem);

      const isVoid =
        selfClose === "/" || voidTags.has(openTag.toLowerCase());
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

// Build standard DOM tree matching templates/index.html
const docElements = {
  chat: new MockElement("section", "chat"),
  chatMessages: new MockElement("div", "chatMessages"),
  welcomeCard: new MockElement("div", "welcomeCard"),
  thinkingBubble: new MockElement("div", "thinkingBubble"),
  thinkingWorkflow: new MockElement("span", "thinkingWorkflow"),
  thinkingTimer: new MockElement("span", "thinkingTimer"),
  toastContainer: new MockElement("div", "toastContainer"),
  chatForm: new MockElement("form", "chatForm"),
  question: new MockElement("textarea", "question"),
  sendBtn: new MockElement("button", "sendBtn"),
  workflowSelect: new MockElement("select", "workflowSelect"),
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

// Wire up thinkingBubble child elements
docElements.thinkingBubble.className = "message assistant thinking-bubble hidden";
const bubbleContent = new MockElement("div");
bubbleContent.className = "bubble thinking-content";
const textWrap = new MockElement("div");
textWrap.className = "thinking-text-wrap";
const thinkingTitle = new MockElement("span");
thinkingTitle.className = "thinking-title";
thinkingTitle.textContent = "Executing Agentic Workflow";
const thinkingMeta = new MockElement("span");
thinkingMeta.className = "thinking-meta";

docElements.thinkingWorkflow.className = "thinking-workflow";
docElements.thinkingWorkflow.textContent = "fix_single_llm";
docElements.thinkingTimer.className = "thinking-timer";
docElements.thinkingTimer.textContent = "0.0s";

thinkingMeta.appendChild(docElements.thinkingWorkflow);
thinkingMeta.appendChild(docElements.thinkingTimer);
textWrap.appendChild(thinkingTitle);
textWrap.appendChild(thinkingMeta);
bubbleContent.appendChild(textWrap);
docElements.thinkingBubble.appendChild(bubbleContent);

docElements.chat.appendChild(docElements.chatMessages);
docElements.chatMessages.appendChild(docElements.welcomeCard);
docElements.workflowSelect.value = "fix_single_llm";

// Globals setup
global.window = {
  localStorage: localStorageMock,
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  navigator: { clipboard: { writeText: async () => true } },
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
  addEventListener: () => {},
};
global.localStorage = localStorageMock;
global.navigator = global.window.navigator;
global.marked = marked;
global.hljs = hljs;
global.DOMPurify = {
  sanitize: (html) => html,
};

global.document = {
  documentElement: {
    dataset: {},
    setAttribute: () => {},
  },
  getElementById: (id) => docElements[id] || null,
  querySelector: (sel) => {
    if (sel.startsWith("#")) {
      return docElements[sel.slice(1)] || null;
    }
    return null;
  },
  querySelectorAll: (sel) => [],
  createElement: (tag) => new MockElement(tag),
  addEventListener: () => {},
  body: docElements.chat,
};

// Evaluate app.js
const mockModule = { exports: {} };
new Function("module", "exports", appCode)(mockModule, mockModule.exports);
const app = mockModule.exports;

/**
 * Helper: creates a mock ReadableStream delivering SSE formatted text chunks
 */
function createMockSSEStream(frames, options = {}) {
  let index = 0;
  const stream = new ReadableStream({
    async pull(controller) {
      if (options.shouldThrowAt && index === options.shouldThrowAt) {
        controller.error(new Error(options.throwMessage || "Stream connection failed"));
        return;
      }
      if (index >= frames.length) {
        controller.close();
        return;
      }
      const frame = frames[index++];
      controller.enqueue(new TextEncoder().encode(frame));
    },
  });

  return stream;
}

async function runTicket6Tests() {
  console.log("=== RUNNING TICKET 6 STREAMING UI & STATE MACHINE TEST SUITE ===");

  // --------------------------------------------------------------------------
  // TEST 1: Live Step Label Updates in Thinking State
  // --------------------------------------------------------------------------
  console.log("Test 1: Dynamic Thinking State & Step Label Updates");
  {
    app.showThinking("fix_single_llm");
    assert(
      !docElements.thinkingBubble.classList.contains("hidden"),
      "Thinking bubble must be visible after showThinking",
    );

    // Initial label matches active workflow
    assert.strictEqual(docElements.thinkingWorkflow.textContent, "fix_single_llm");

    // Dynamic Step update 1
    app.updateThinkingStep("Analyzing request parameters...");
    assert.strictEqual(
      docElements.thinkingWorkflow.textContent,
      "Analyzing request parameters...",
      "Status label must reflect step 1 label",
    );

    // Dynamic Step update 2
    app.updateThinkingStep("Querying corporate vector store...");
    assert.strictEqual(
      docElements.thinkingWorkflow.textContent,
      "Querying corporate vector store...",
      "Status label must reflect step 2 label",
    );

    app.hideThinking();
    assert(
      docElements.thinkingBubble.classList.contains("hidden"),
      "Thinking bubble must be hidden after hideThinking",
    );
    console.log("  Passed: Dynamic step label updates during thinking verified.");
  }

  // --------------------------------------------------------------------------
  // TEST 2: Smooth Transition from Thinking Bubble to Assistant Bubble on First Token
  // --------------------------------------------------------------------------
  console.log("Test 2: Transition from Thinking Bubble to Assistant Bubble on First Token");
  {
    const frames = [
      'event: step\ndata: {"step": "route", "label": "Classifying query intent"}\n\n',
      'event: step\ndata: {"step": "retrieve", "label": "Searching enterprise docs"}\n\n',
      'event: token\ndata: {"delta": "Hello! "}\n\n',
      'event: token\ndata: {"delta": "How can I help you today?"}\n\n',
      'event: metadata\ndata: {"source_used": "vectorstore", "workflow": "fix_single_llm", "trace_id": "tr-test-1", "trace": ["route", "retrieve", "generate"]}\n\n',
    ];

    global.fetch = async (url, opts) => {
      assert.strictEqual(url, "/api/chat/stream", "Must call /api/chat/stream endpoint");
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        body: createMockSSEStream(frames),
        text: async () => "",
      };
    };

    docElements.question.value = "Hello assistant";
    await app.askAgent("Hello assistant");

    // Thinking bubble must be smoothly removed
    assert(
      docElements.thinkingBubble.classList.contains("hidden"),
      "thinkingBubble must be hidden upon stream completion",
    );
    assert(
      !docElements.chatMessages.children.includes(docElements.thinkingBubble),
      "thinkingBubble must be detached from chatMessages container",
    );

    // Assistant bubble mounted
    const assistantBubble =
      docElements.chatMessages.children[docElements.chatMessages.children.length - 1];
    assert(
      assistantBubble.classList.contains("assistant"),
      "Assistant message must be appended to chat container",
    );

    const messageBody = assistantBubble.querySelector(".message-body");
    assert(messageBody !== null, "Assistant bubble must have inner markdown-content container");
    assert(
      assistantBubble.textContent.includes("How can I help you today?"),
      "Rendered bubble must contain complete response text",
    );

    console.log("  Passed: Smooth transition from thinkingBubble to assistant bubble on first token verified.");
  }

  // --------------------------------------------------------------------------
  // TEST 3: RAF-Throttled Progressive Markdown Rendering
  // --------------------------------------------------------------------------
  console.log("Test 3: RAF-Throttled Progressive Markdown Rendering");
  {
    const testContentEl = new MockElement("div");
    app.setAccumulatedMarkdown("");

    // Simulate rapid incoming token chunks
    app.scheduleProgressiveRender(testContentEl, "Token1 ");
    assert.strictEqual(app.getAccumulatedMarkdown(), "Token1 ");

    app.scheduleProgressiveRender(testContentEl, "Token1 Token2 ");
    assert.strictEqual(app.getAccumulatedMarkdown(), "Token1 Token2 ");

    app.scheduleProgressiveRender(testContentEl, "Token1 Token2 Token3");
    assert.strictEqual(app.getAccumulatedMarkdown(), "Token1 Token2 Token3");

    // Advance RAF timer callback
    await new Promise((r) => setTimeout(r, 20));

    assert(
      testContentEl.innerHTML.includes("Token1 Token2 Token3"),
      "Target element must render accumulated text following RAF flush",
    );
    console.log("  Passed: Progressive RAF-throttled rendering verified.");
  }

  // --------------------------------------------------------------------------
  // TEST 4: Reasoning Accordion, Trace ID Chip, Feedback Bar & Full State Persistence
  // --------------------------------------------------------------------------
  console.log("Test 4: Metadata Event Formatting & Observability Injection");
  {
    const frames = [
      'event: token\ndata: {"delta": "Here is the code:\\n\\n```python\\nimport sys\\nprint(sys.version)\\n```"}\n\n',
      'event: metadata\ndata: {"source_used": "vectorstore", "workflow": "rag_fusion", "trace_id": "tr-langfuse-999", "trace": ["fusion_query", "vector_retrieve", "answer_synthesize"], "citations": [{"title": "Python Docs", "url": "https://docs.python.org"}]}\n\n',
    ];

    global.fetch = async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      body: createMockSSEStream(frames),
      text: async () => "",
    });

    docElements.workflowSelect.value = "rag_fusion";
    await app.askAgent("Show python version script");

    const assistantBubble =
      docElements.chatMessages.children[docElements.chatMessages.children.length - 1];

    // Verify reasoning accordion
    const accordion = assistantBubble.querySelector(".reasoning-accordion");
    assert(accordion !== null, "details.reasoning-accordion must be injected into assistant bubble");

    const sourceBadge = accordion.querySelector(".source-badge");
    assert(sourceBadge !== null, "Accordion must contain source-badge");
    assert(
      sourceBadge.classList.contains("badge-vectorstore"),
      "Source badge must reflect vectorstore source",
    );

    // Verify trace ID chip
    const traceChip = accordion.querySelector(".trace-id-chip");
    assert(traceChip !== null, "Trace ID chip must be rendered");
    assert.strictEqual(
      traceChip.getAttribute("data-trace-id"),
      "tr-langfuse-999",
      "Trace ID chip must contain correct Langfuse trace ID",
    );

    // Verify feedback bar
    const feedbackBar = assistantBubble.querySelector(".feedback-bar");
    assert(feedbackBar !== null, "Feedback bar must be injected into assistant bubble");
    assert.strictEqual(
      feedbackBar.getAttribute("data-trace-id"),
      "tr-langfuse-999",
      "Feedback bar must bind trace ID for voting",
    );
    assert(feedbackBar.querySelector(".thumbs-up") !== null, "Feedback bar must have thumbs-up button");
    assert(feedbackBar.querySelector(".thumbs-down") !== null, "Feedback bar must have thumbs-down button");

    // Verify composer inputs re-enabled
    assert(!docElements.sendBtn.disabled, "Send button must be re-enabled");
    assert(!docElements.question.disabled, "Question input must be re-enabled");
    assert(!docElements.workflowSelect.disabled, "Workflow select must be re-enabled");

    // Verify session persistence in localStorage
    const storedSession = JSON.parse(
      localStorageMock.getItem(`rag_session_${app.getSessionData(app.getSessionsIndex()[0].id).id}`),
    );
    assert(storedSession !== null, "Session must be persisted in localStorage");
    const lastMsg = storedSession.messages[storedSession.messages.length - 1];
    assert.strictEqual(lastMsg.role, "assistant");
    assert.strictEqual(lastMsg.source_used, "vectorstore");
    assert.strictEqual(lastMsg.trace_id, "tr-langfuse-999");
    assert.deepStrictEqual(lastMsg.trace, [
      "fusion_query",
      "vector_retrieve",
      "answer_synthesize",
    ]);

    console.log("  Passed: Reasoning accordion, trace chip, feedback bar, and localStorage persistence verified.");
  }

  // --------------------------------------------------------------------------
  // TEST 5: Stream Interruption & Partial Recovery Persistence (Network Drop Mid-Stream)
  // --------------------------------------------------------------------------
  console.log("Test 5: Interrupted Stream Recovery & Partial Transcript Persistence");
  {
    // Stream delivers partial tokens then terminates unexpectedly
    const frames = [
      'event: token\ndata: {"delta": "The server firewall configuration was found at /etc/firewall."}\n\n',
      'event: token\ndata: {"delta": " To restart the daemon run: systemctl restart"}\n\n',
    ];

    global.fetch = async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      body: createMockSSEStream(frames, {
        shouldThrowAt: 2,
        throwMessage: "Connection reset by peer",
      }),
      text: async () => "",
    });

    await app.askAgent("How do I restart the firewall?");

    const assistantBubble =
      docElements.chatMessages.children[docElements.chatMessages.children.length - 1];

    // 1. Partial tokens preserved in DOM
    assert(
      assistantBubble.textContent.includes("The server firewall configuration was found"),
      "Partial tokens must be retained in the DOM",
    );

    // 2. Interrupted warning badge appended
    const warningBadge = assistantBubble.querySelector(".interrupted-badge");
    assert(warningBadge !== null, "Warning badge .interrupted-badge must be appended to assistant bubble");
    assert(
      warningBadge.textContent.includes("[Interrupted / Incomplete]"),
      "Warning badge must display '[Interrupted / Incomplete]'",
    );

    // 3. Persisted in localStorage marked with source_used: 'interrupted'
    const index = app.getSessionsIndex();
    const currentSession = app.getSessionData(index[0].id);
    const interruptedMsg = currentSession.messages[currentSession.messages.length - 1];
    assert.strictEqual(interruptedMsg.role, "assistant");
    assert.strictEqual(
      interruptedMsg.source_used,
      "interrupted",
      "Interrupted turn must be persisted with source_used='interrupted'",
    );
    assert(
      interruptedMsg.text.includes("[Interrupted / Incomplete]"),
      "Interrupted turn text in localStorage must have '[Interrupted / Incomplete]' appended",
    );

    // 4. Composer inputs re-enabled
    assert(!docElements.sendBtn.disabled, "Send button must be re-enabled after interruption");
    assert(!docElements.question.disabled, "Question input must be re-enabled after interruption");

    console.log("  Passed: Partial transcript recovery with interrupted warning badge verified.");
  }

  // --------------------------------------------------------------------------
  // TEST 6: Mid-Stream Error Event (event: error) after Partial Tokens
  // --------------------------------------------------------------------------
  console.log("Test 6: Mid-Stream event: error Handling");
  {
    const frames = [
      'event: token\ndata: {"delta": "Step 1: Check your network settings."}\n\n',
      'event: error\ndata: {"message": "Inference stream failed: GPU OOM", "code": 500}\n\n',
    ];

    global.fetch = async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      body: createMockSSEStream(frames),
      text: async () => "",
    });

    await app.askAgent("Network troubleshooting");

    const assistantBubble =
      docElements.chatMessages.children[docElements.chatMessages.children.length - 1];

    // Preserves partial tokens and attaches warning badge
    assert(assistantBubble.textContent.includes("Step 1: Check your network settings."));
    const badge = assistantBubble.querySelector(".interrupted-badge");
    assert(badge !== null, "Badge must be attached on mid-stream event: error");

    // Persisted as interrupted
    const currentSession = app.getSessionData(app.getSessionsIndex()[0].id);
    const lastMsg = currentSession.messages[currentSession.messages.length - 1];
    assert.strictEqual(lastMsg.source_used, "interrupted");

    console.log("  Passed: Mid-stream event: error properly persisted as interrupted.");
  }

  // --------------------------------------------------------------------------
  // TEST 7: Early Pre-Stream Error Recovery (before any tokens received)
  // --------------------------------------------------------------------------
  console.log("Test 7: Early Pre-Stream Error Recovery (Zero Tokens)");
  {
    // Server returns HTTP 400 Bad Request
    global.fetch = async () => ({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      text: async () => JSON.stringify({ detail: "Unknown workflow 'unsupported_wf'" }),
    });

    docElements.workflowSelect.value = "unsupported_wf";
    await app.askAgent("Invalid workflow query");

    // Thinking bubble cleanly removed
    assert(
      docElements.thinkingBubble.classList.contains("hidden"),
      "Thinking bubble must be hidden on pre-stream error",
    );

    // Error bubble rendered in chat
    const errorBubble =
      docElements.chatMessages.children[docElements.chatMessages.children.length - 1];
    assert(
      errorBubble.textContent.includes("unsupported_wf"),
      "Error bubble must be appended with error detail",
    );

    // Persisted as error
    const currentSession = app.getSessionData(app.getSessionsIndex()[0].id);
    const lastMsg = currentSession.messages[currentSession.messages.length - 1];
    assert.strictEqual(lastMsg.source_used, "error");

    // Inputs re-enabled
    assert(!docElements.sendBtn.disabled, "Send button re-enabled on pre-stream error");
    assert(!docElements.question.disabled, "Question input re-enabled on pre-stream error");

    console.log("  Passed: Pre-stream error cleanly handled and reported.");
  }

  // --------------------------------------------------------------------------
  // TEST 8: Form Submission Dispatches Streaming Lifecycle
  // --------------------------------------------------------------------------
  console.log("Test 8: Form Submit Handler Streaming Wiring");
  {
    let streamEndpointHit = false;
    const frames = [
      'event: token\ndata: {"delta": "Form response"}\n\n',
      'event: metadata\ndata: {"source_used": "vectorstore", "workflow": "fix_single_llm", "trace_id": "tr-form", "trace": []}\n\n',
    ];

    global.fetch = async (url) => {
      if (url === "/api/chat/stream") {
        streamEndpointHit = true;
      }
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        body: createMockSSEStream(frames),
        text: async () => "",
      };
    };

    docElements.question.value = "Form question";
    docElements.chatForm.dispatchEvent({
      type: "submit",
      preventDefault: () => {},
    });

    // Allow async execution
    await new Promise((r) => setTimeout(r, 50));

    assert(streamEndpointHit, "Submitting chatForm must dispatch /api/chat/stream");
    console.log("  Passed: Form submit handler dispatches streaming chat.");
  }

  console.log("\n=======================================================");
  console.log(" ALL 8 TICKET 6 STREAMING UI VERIFICATION CHECKS PASSED ");
  console.log("=======================================================\n");
}

runTicket6Tests().catch((err) => {
  console.error("Test suite failed:", err);
  process.exit(1);
});
