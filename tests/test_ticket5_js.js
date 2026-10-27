/**
 * Verification Test Suite for Ticket 5
 * - Markdown Rendering Pipeline (marked + hljs)
 * - DOMPurify Sanitization against XSS payloads
 * - Fenced code block wrapping with .code-block-wrapper and language tags
 * - Copy-to-clipboard interaction and 2000ms "Copied!" feedback state
 * - Agentic Thinking State lifecycle (#thinkingBubble, #thinkingTimer, workflow badge, clean removal)
 */

const fs = require("fs");
const assert = require("assert");

// Load vendored libraries
const marked = require("../static/vendor/marked.min.js");
const hljs = require("../static/vendor/highlight.min.js");

// Set up mock DOM and browser environment
const storage = new Map();
const localStorageMock = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
  clear: () => storage.clear(),
};

class ClassListMock {
  constructor() {
    this._set = new Set();
  }
  add(c) {
    this._set.add(c);
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
  set innerHTML(v) {
    this._innerHTML = String(v);
    // Parse simplified children if needed
  }

  setAttribute(k, v) {
    this.attributes[k] = String(v);
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
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  replaceChild(newChild, oldChild) {
    const idx = this.children.indexOf(oldChild);
    if (idx >= 0) {
      this.children[idx] = newChild;
      newChild.parentNode = this;
      oldChild.parentNode = null;
    }
  }

  replaceWith(newElem) {
    if (this.parentNode) {
      this.parentNode.replaceChild(newElem, this);
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
        if (
          sel.includes(".message:not(.thinking-bubble)") &&
          ch.classList.contains("message") &&
          !ch.classList.contains("thinking-bubble")
        ) {
          res.push(ch);
        } else if (sel.startsWith(".") && ch.classList.contains(sel.slice(1))) {
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

// Build standard DOM tree matching templates/index.html
const docElements = {
  chat: new MockElement("section", "chat"),
  chatMessages: new MockElement("div", "chatMessages"),
  welcomeCard: new MockElement("div", "welcomeCard"),
  thinkingBubble: new MockElement("div", "thinkingBubble"),
  thinkingWorkflow: new MockElement("span", "thinkingWorkflow"),
  thinkingTimer: new MockElement("span", "thinkingTimer"),
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
  openUpload: new MockElement("button", "openUpload"),
  openIngestModalBtn: new MockElement("button", "openIngestModalBtn"),
  closeUpload: new MockElement("button", "closeUpload"),
  uploadBtn: new MockElement("button", "uploadBtn"),
};

// Wire thinkingBubble children
docElements.thinkingWorkflow.classList.add("thinking-workflow");
docElements.thinkingTimer.classList.add("thinking-timer");
docElements.thinkingBubble.classList.add("message");
docElements.thinkingBubble.classList.add("assistant");
docElements.thinkingBubble.classList.add("thinking-bubble");
docElements.thinkingBubble.classList.add("hidden");
docElements.thinkingBubble.appendChild(docElements.thinkingWorkflow);
docElements.thinkingBubble.appendChild(docElements.thinkingTimer);

docElements.chat.appendChild(docElements.chatMessages);
docElements.chatMessages.appendChild(docElements.welcomeCard);
docElements.chatMessages.appendChild(docElements.thinkingBubble);

// Workflow selector mock
docElements.workflowSelect.value = "fix_single_llm";

// Clipboard mock
let copiedClipboardData = null;
const clipboardMock = {
  writeText: async (text) => {
    copiedClipboardData = text;
    return true;
  },
};
try {
  navigator.clipboard = clipboardMock;
} catch (e) {
  Object.defineProperty(navigator, "clipboard", {
    value: clipboardMock,
    configurable: true,
  });
}

// DOMPurify Mock conforming to DOMPurify specification for unit assertions
const purifyMock = {
  sanitize: (html, config) => {
    let out = String(html || "");
    // Remove scripts
    out = out.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
    // Remove forbidden tags
    out = out.replace(
      /<(style|iframe|object|embed)\b[^<]*(?:(?!<\/\1>)<[^<]*)*<\/\1>/gi,
      "",
    );
    // Remove on* event attributes
    out = out.replace(/\bon\w+\s*=\s*(['"]).*?\1/gi, "");
    out = out.replace(/\bon\w+\s*=\s*[^>\s]+/gi, "");
    // Neutralize javascript: href
    out = out.replace(/href\s*=\s*(['"])\s*javascript:[^'"]*\1/gi, 'href="#"');
    return out;
  },
};

// Global environment shims
global.marked = marked;
global.hljs = hljs;
global.DOMPurify = purifyMock;
global.navigator = { clipboard: clipboardMock };
global.localStorage = localStorageMock;
global.window = {
  marked,
  hljs,
  DOMPurify: purifyMock,
  localStorage: localStorageMock,
  navigator: { clipboard: clipboardMock },
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  confirm: () => true,
  addEventListener: () => {},
};

global.document = {
  documentElement: new MockElement("html"),
  body: new MockElement("body"),
  getElementById: (id) => docElements[id] || null,
  createElement: (tag) => new MockElement(tag),
  querySelectorAll: () => [],
  addEventListener: () => {},
};

// Load app.js in an isolated scope
const appCode = fs.readFileSync("./static/js/app.js", "utf8");
new Function("module", "exports", appCode)(module, module.exports);

// Access exported functions
const {
  renderMarkdown,
  handleCopyCode,
  copyToClipboard,
  showThinking,
  hideThinking,
  getThinkingInterval,
  askAgent,
  addMessage,
} = module.exports;

async function runTests() {
  console.log("=== RUNNING TICKET 5 VERIFICATION SUITE ===");

  // TEST 1: Markdown Rendering & Breaks
  console.log("Test 1: Markdown Parsing with GFM and Standard Line Breaks");
  const markdownText = "First line\nSecond line\n\n**Bold Text** and *italic*";
  const renderedHtml = renderMarkdown(markdownText);
  assert(
    renderedHtml.includes("<p>First line<br>") ||
      renderedHtml.includes("First line<br>\nSecond line") ||
      renderedHtml.includes("First line<br>Second line"),
    "Breaks: true must render <br> on single newlines",
  );
  assert(
    renderedHtml.includes("<strong>Bold Text</strong>"),
    "Bold Markdown rendered correctly",
  );
  assert(
    renderedHtml.includes("<em>italic</em>"),
    "Italic Markdown rendered correctly",
  );
  console.log("  Passed: Standard line breaks and markdown syntax rendered");

  // TEST 2: Fenced Code Blocks with Wrapper and Language Tag
  console.log("Test 2: Code Block Wrapper & Language Title");
  const codeMarkdown = "```python\ndef solve(x):\n    return x * 2\n```";
  const codeHtml = renderMarkdown(codeMarkdown);
  assert(
    codeHtml.includes('class="code-block-wrapper"'),
    "Code block must be wrapped in .code-block-wrapper",
  );
  assert(
    codeHtml.includes('class="code-block-header"'),
    "Code block must contain .code-block-header",
  );
  assert(
    codeHtml.includes('class="code-lang">python</span>'),
    "Code block must display language tag in .code-lang",
  );
  assert(
    codeHtml.includes('class="copy-code-btn"'),
    "Code block header must include .copy-code-btn",
  );
  assert(
    codeHtml.includes('<pre><code class="hljs language-python">'),
    "Pre/code tag must have hljs and language class",
  );
  assert(
    codeHtml.includes('class="hljs-keyword">def</span>'),
    "Python syntax highlighting must tokenize keyword 'def'",
  );
  console.log(
    "  Passed: Code block wrapper, language chip, copy button, and hljs syntax highlighting verified",
  );

  // TEST 3: DOMPurify XSS Sanitization
  console.log("Test 3: Security Sanitization of Malicious XSS Payloads");
  const xssPayloads = [
    '<script>alert("xss")</script>Safe Text',
    '<img src="x" onerror="alert(1)">Image Text',
    '[Click Here](javascript:alert("pwned"))',
    '<iframe src="https://attacker.com"></iframe>Embedded frame',
    '<object data="exploit.swf"></object>Object tag',
  ];

  for (const payload of xssPayloads) {
    const sanitized = renderMarkdown(payload);
    assert(
      !sanitized.includes("<script"),
      `Payload must strip script tag: ${payload}`,
    );
    assert(
      !sanitized.includes("onerror"),
      `Payload must strip onerror attribute: ${payload}`,
    );
    assert(
      !sanitized.includes('href="javascript:'),
      `Payload must neutralize javascript: links: ${payload}`,
    );
    assert(
      !sanitized.includes("<iframe"),
      `Payload must forbid iframe tag: ${payload}`,
    );
    assert(
      !sanitized.includes("<object"),
      `Payload must forbid object tag: ${payload}`,
    );
  }
  console.log("  Passed: All malicious injection vectors sanitized");

  // TEST 4: Copy to Clipboard & Visual Feedback (2000ms timer)
  console.log("Test 4: One-Click Code Copy & 2000ms Visual State");
  const mockWrapper = new MockElement("div");
  mockWrapper.classList.add("code-block-wrapper");

  const mockPre = new MockElement("pre");
  const mockCode = new MockElement("code");
  mockCode.textContent = "const answer = 42;";
  mockPre.appendChild(mockCode);

  const mockBtn = new MockElement("button");
  mockBtn.classList.add("copy-code-btn");
  const mockLabel = new MockElement("span");
  mockLabel.classList.add("copy-label");
  mockLabel.textContent = "Copy";
  const mockIcon = new MockElement("svg");
  mockIcon.classList.add("copy-icon");
  mockBtn.appendChild(mockIcon);
  mockBtn.appendChild(mockLabel);

  mockWrapper.appendChild(mockBtn);
  mockWrapper.appendChild(mockPre);

  copiedClipboardData = null;
  await handleCopyCode(mockBtn);

  assert.strictEqual(
    copiedClipboardData,
    "const answer = 42;",
    "Exact code text must be copied to clipboard",
  );
  assert(
    mockBtn.classList.contains("copied"),
    "Button must have .copied class during feedback state",
  );
  assert.strictEqual(
    mockLabel.textContent,
    "Copied!",
    "Label must change to 'Copied!'",
  );

  // Fast-forward 2100ms to verify automatic revert
  await new Promise((resolve) => setTimeout(resolve, 2100));

  assert(
    !mockBtn.classList.contains("copied"),
    "Button must remove .copied class after 2000ms",
  );
  assert.strictEqual(
    mockLabel.textContent,
    "Copy",
    "Button label must reset back to 'Copy' after 2000ms",
  );
  console.log(
    "  Passed: Code copied to clipboard and 2000ms feedback lifecycle verified",
  );

  // TEST 5: Agentic Thinking State Lifecycle
  console.log("Test 5: In-Feed Thinking State & Elapsed Timer");
  docElements.workflowSelect.value = "fix_single_jev";

  showThinking("fix_single_jev");

  assert(
    !docElements.thinkingBubble.classList.contains("hidden"),
    "Thinking bubble must be unhidden when showThinking is called",
  );
  assert(
    docElements.chatMessages.children.includes(docElements.thinkingBubble),
    "Thinking bubble must be anchored into chatMessages",
  );
  assert.strictEqual(
    docElements.thinkingWorkflow.textContent,
    "fix_single_jev",
    "Thinking workflow badge must show active workflow name",
  );
  assert.strictEqual(
    docElements.thinkingTimer.textContent,
    "0.0s",
    "Thinking timer must initialize at 0.0s",
  );
  assert(
    getThinkingInterval() !== null,
    "Timer interval ticker must be active",
  );

  // Wait 250ms for ticker ticks
  await new Promise((resolve) => setTimeout(resolve, 250));
  const timerVal = parseFloat(docElements.thinkingTimer.textContent);
  assert(
    timerVal >= 0.1,
    `Elapsed timer must increment over time, saw ${docElements.thinkingTimer.textContent}`,
  );

  // Hide thinking
  hideThinking();
  assert(
    docElements.thinkingBubble.classList.contains("hidden"),
    "Thinking bubble must be hidden when hideThinking is called",
  );
  assert(
    !docElements.chatMessages.children.includes(docElements.thinkingBubble),
    "Thinking bubble must be cleanly removed from chatMessages",
  );
  assert.strictEqual(
    getThinkingInterval(),
    null,
    "Timer interval must be cleared upon hideThinking",
  );
  console.log("  Passed: Thinking indicator anchor, ticker, and clean removal verified");

  // TEST 6: Simulated askAgent workflow with Thinking Lifecycle and Composer Disabling
  console.log("Test 6: askAgent Integration & Composer State Disabling");
  let fetchCalled = false;
  let fetchBody = null;
  global.fetch = async (url, opts) => {
    fetchCalled = true;
    fetchBody = JSON.parse(opts.body);

    // Verify while fetch is active: inputs must be disabled and thinking active
    assert(docElements.sendBtn.disabled, "Send button must be disabled during active request");
    assert(
      docElements.messageInput.disabled,
      "Message textarea must be disabled during active request",
    );
    assert(
      docElements.workflowSelect.disabled,
      "Workflow selector must be disabled during active request",
    );
    assert(
      !docElements.thinkingBubble.classList.contains("hidden"),
      "Thinking bubble must be visible during request",
    );

    return {
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          answer: "Here is code:\n```python\ndef greet():\n    return 'hello'\n```",
          source_used: "vectorstore",
          workflow: "fix_single_jev",
          trace_id: "test-trace-id",
          trace: ["1. Retrieve", "2. Grade", "3. Answer"],
        }),
    };
  };

  docElements.workflowSelect.value = "fix_single_jev";
  docElements.messageInput.value = "How do I print hello in Python?";
  await askAgent("How do I print hello in Python?");

  assert(fetchCalled, "POST /api/chat should be dispatched");
  assert.strictEqual(
    fetchBody.question,
    "How do I print hello in Python?",
    "Dispatched body should contain prompt question",
  );
  assert.strictEqual(
    fetchBody.workflow,
    "fix_single_jev",
    "Dispatched body should contain active workflow",
  );

  // After request finishes:
  assert(
    !docElements.sendBtn.disabled,
    "Send button must be re-enabled after response",
  );
  assert(
    !docElements.messageInput.disabled,
    "Message textarea must be re-enabled after response",
  );
  assert(
    !docElements.workflowSelect.disabled,
    "Workflow selector must be re-enabled after response",
  );
  assert(
    docElements.thinkingBubble.classList.contains("hidden"),
    "Thinking bubble must be hidden after response",
  );
  assert(
    !docElements.chatMessages.children.includes(docElements.thinkingBubble),
    "Thinking bubble must be removed from feed",
  );

  // Check that the assistant message was added with rendered Markdown and code wrapper
  const assistantBubble = docElements.chatMessages.children[docElements.chatMessages.children.length - 1];
  assert(
    assistantBubble.classList.contains("assistant"),
    "Assistant message must be appended to chat",
  );
  assert(
    assistantBubble.innerHTML.includes("code-block-wrapper"),
    "Assistant message must contain code-block-wrapper",
  );
  assert(
    assistantBubble.innerHTML.includes("hljs-keyword"),
    "Assistant message must contain syntax highlighted python code",
  );
  console.log("  Passed: askAgent full lifecycle with thinking and composer states verified");

  // TEST 7: askAgent Network Failure & Clean Recovery
  console.log("Test 7: askAgent Network Error Recovery");
  global.fetch = async () => {
    throw new Error("Network timeout");
  };

  await askAgent("Failure test");

  assert(
    !docElements.sendBtn.disabled,
    "Send button must be re-enabled after error",
  );
  assert(
    !docElements.messageInput.disabled,
    "Message input must be re-enabled after error",
  );
  assert(
    docElements.thinkingBubble.classList.contains("hidden"),
    "Thinking bubble must be hidden after error",
  );
  assert.strictEqual(
    getThinkingInterval(),
    null,
    "Timer interval must be null after error",
  );
  console.log("  Passed: askAgent gracefully recovers and cleans up thinking state on error");

  console.log("\n==========================================");
  console.log(" ALL 7 TICKET 5 VERIFICATION CHECKS PASSED ");
  console.log("==========================================");
}

runTests().catch((err) => {
  console.error("Test failed:", err);
  process.exit(1);
});
