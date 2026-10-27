const fs = require("fs");
const assert = require("assert");

// Create mock DOM environment
const storage = new Map();
const localStorageMock = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
  clear: () => storage.clear(),
  key: (i) => Array.from(storage.keys())[i],
  get length() {
    return storage.size;
  },
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
    this.textContent = "";
    this.innerHTML = "";
    this.dataset = {};
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
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child, ref) {
    child.parentNode = this;
    const idx = this.children.indexOf(ref);
    if (idx >= 0) this.children.splice(idx, 0, child);
    else this.children.push(child);
    return child;
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  querySelectorAll(sel) {
    const results = [];
    const walk = (node) => {
      for (const ch of node.children) {
        if (
          sel.includes(".message:not(.thinking-bubble)") &&
          ch.classList.contains("message") &&
          !ch.classList.contains("thinking-bubble")
        ) {
          results.push(ch);
        }
        walk(ch);
      }
    };
    walk(this);
    return results;
  }
  querySelector(sel) {
    if (sel === ".sun-icon")
      return this.children.find((c) => c.classList.contains("sun-icon"));
    if (sel === ".moon-icon")
      return this.children.find((c) => c.classList.contains("moon-icon"));
    return null;
  }
  focus() {}
}

const docElements = {
  chat: new MockElement("section", "chat"),
  chatMessages: new MockElement("div", "chatMessages"),
  welcomeCard: new MockElement("div", "welcomeCard"),
  thinkingBubble: new MockElement("div", "thinkingBubble"),
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

docElements.thinkingBubble.classList.add("message");
docElements.thinkingBubble.classList.add("thinking-bubble");
docElements.chat.appendChild(docElements.chatMessages);
docElements.chatMessages.appendChild(docElements.welcomeCard);
docElements.chatMessages.appendChild(docElements.thinkingBubble);

const sunIcon = new MockElement("svg");
sunIcon.classList.add("sun-icon");
const moonIcon = new MockElement("svg");
moonIcon.classList.add("moon-icon");
docElements.themeToggleBtn.appendChild(sunIcon);
docElements.themeToggleBtn.appendChild(moonIcon);

global.window = {
  localStorage: localStorageMock,
  matchMedia: (q) => ({ matches: false, addEventListener: () => {} }),
  confirm: (msg) => true,
  addEventListener: () => {},
};
global.document = {
  documentElement: new MockElement("html"),
  getElementById: (id) => docElements[id] || null,
  createElement: (tag) => new MockElement(tag),
  querySelectorAll: () => [],
};
global.localStorage = localStorageMock;

// Load script
const code = fs.readFileSync("./static/js/app.js", "utf8");
eval(code);

console.log("=== RUNNING FRONTEND LOGIC TESTS ===");

// TEST 1: Session bootstrap
console.log("Test 1: Initial Session Bootstrap");
const initialSessionId = localStorage.getItem("rag_current_session_id");
assert(
  initialSessionId && initialSessionId.length === 36,
  "Session ID must be a 36-char UUID",
);
console.log("  Passed: Session ID initialized:", initialSessionId);

// TEST 2: Theme Management
console.log("Test 2: Theme Management & Toggling");
assert.strictEqual(
  document.documentElement.dataset.theme,
  "dark",
  "Default theme should be dark",
);
assert.strictEqual(
  sunIcon.style.display,
  "block",
  "Sun icon should show in dark mode",
);
assert.strictEqual(
  moonIcon.style.display,
  "none",
  "Moon icon should hide in dark mode",
);

// Toggle to light
docElements.themeToggleBtn.dispatchEvent({ type: "click" });
assert.strictEqual(
  document.documentElement.dataset.theme,
  "light",
  "Theme toggled to light",
);
assert.strictEqual(
  localStorage.getItem("rag_theme"),
  "light",
  "Theme stored in localStorage",
);
assert.strictEqual(
  sunIcon.style.display,
  "none",
  "Sun icon hidden in light mode",
);
assert.strictEqual(
  moonIcon.style.display,
  "block",
  "Moon icon shown in light mode",
);

// Toggle back to dark
docElements.themeToggleBtn.dispatchEvent({ type: "click" });
assert.strictEqual(
  document.documentElement.dataset.theme,
  "dark",
  "Theme toggled back to dark",
);
assert.strictEqual(
  localStorage.getItem("rag_theme"),
  "dark",
  "Theme dark stored in localStorage",
);
console.log("  Passed: Theme toggles and persists cleanly");

// TEST 3: Mobile Navigation Drawer
console.log("Test 3: Mobile Drawer Toggling");
assert(!docElements.sidebar.classList.contains("open"), "Sidebar closed initially");
docElements.sidebarToggleBtn.dispatchEvent({ type: "click" });
assert(docElements.sidebar.classList.contains("open"), "Sidebar opened on hamburger click");
assert(
  docElements.sidebarBackdrop.classList.contains("active"),
  "Backdrop active when sidebar open",
);
docElements.sidebarBackdrop.dispatchEvent({ type: "click" });
assert(!docElements.sidebar.classList.contains("open"), "Sidebar closed on backdrop click");
console.log("  Passed: Drawer opens and closes via hamburger and backdrop");

// TEST 4: "New Chat" Action
console.log("Test 4: 'New Chat' Lifecycle");
const oldSession = localStorage.getItem("rag_current_session_id");
docElements.newChatBtn.dispatchEvent({ type: "click" });
const newSession = localStorage.getItem("rag_current_session_id");
assert.notStrictEqual(newSession, oldSession, "New Chat generated fresh session ID");
assert(
  !docElements.welcomeCard.classList.contains("hidden"),
  "Welcome card is displayed in new chat",
);
console.log("  Passed: New Chat generates fresh session ID and resets feed");

// TEST 5: Session Storage, Transcripts & Indexing
console.log("Test 5: Session Data Persistence & Index Updates");
const sessionData = {
  id: newSession,
  title: "How do I configure VPN?",
  createdAt: Date.now() - 60000,
  updatedAt: Date.now() - 60000,
  workflow: "fix_single_jev",
  messages: [
    { role: "user", text: "How do I configure VPN?", timestamp: Date.now() - 60000 },
    {
      role: "assistant",
      text: "Download OpenVPN client...",
      source_used: "vectorstore",
      trace_id: "test-trace-123",
      trace: ["retrieval", "generate"],
      workflow: "fix_single_jev",
      citations: [],
      timestamp: Date.now() - 30000,
    },
  ],
};
saveSessionData(newSession, sessionData);
updateSessionIndexMetadata(newSession, sessionData);

const index = getSessionsIndex();
assert(index.length === 1, "Index should contain 1 session");
assert.strictEqual(index[0].id, newSession, "Index entry matches session id");
assert.strictEqual(index[0].title, "How do I configure VPN?", "Index entry title matches");
assert.strictEqual(index[0].messageCount, 2, "Message count matches");
console.log("  Passed: Session data and catalog index saved correctly");

// TEST 6: Session Switching
console.log("Test 6: Session Switching");
// Start another new chat
startNewChat();
const secondSession = localStorage.getItem("rag_current_session_id");
assert.notStrictEqual(secondSession, newSession, "Another new chat has its own ID");

// Switch back to first session
switchSession(newSession);
assert.strictEqual(
  localStorage.getItem("rag_current_session_id"),
  newSession,
  "Active session restored to first session",
);
assert(
  docElements.welcomeCard.classList.contains("hidden"),
  "Welcome card hidden after restoring messages",
);
console.log("  Passed: Switching session restores transcript and updates active state");

// TEST 7: Session Deletion
console.log("Test 7: Session Deletion");
deleteSession(newSession);
const remainingIndex = getSessionsIndex();
assert(remainingIndex.length === 0, "Index empty after deleting session");
assert.strictEqual(getSessionData(newSession), null, "Session data removed from storage");
const activeAfterDelete = localStorage.getItem("rag_current_session_id");
assert.notStrictEqual(
  activeAfterDelete,
  newSession,
  "Active session automatically switched away from deleted session",
);
console.log("  Passed: Session deletion removes data and starts new chat if active");

// TEST 8: Clear All History
console.log("Test 8: Clear All History Confirmation");
const s1 = generateUUID();
const s2 = generateUUID();
saveSessionData(s1, { id: s1, title: "Chat 1", updatedAt: Date.now(), messages: [] });
saveSessionData(s2, { id: s2, title: "Chat 2", updatedAt: Date.now(), messages: [] });
saveSessionsIndex([
  { id: s1, title: "Chat 1", updatedAt: Date.now(), messageCount: 0 },
  { id: s2, title: "Chat 2", updatedAt: Date.now(), messageCount: 0 },
]);
assert.strictEqual(getSessionsIndex().length, 2);

clearAllHistory();
assert.strictEqual(getSessionsIndex().length, 0, "Index cleared");
assert.strictEqual(getSessionData(s1), null, "Session 1 removed");
assert.strictEqual(getSessionData(s2), null, "Session 2 removed");
console.log("  Passed: Clear all history removes all sessions and resets index");

// TEST 9: Relative Time Formatter
console.log("Test 9: Relative Time Formatter");
assert.strictEqual(formatRelativeTime(Date.now() - 10000), "Just now");
assert.strictEqual(formatRelativeTime(Date.now() - 5 * 60 * 1000), "5m ago");
assert.strictEqual(formatRelativeTime(Date.now() - 3 * 3600 * 1000), "3h ago");
assert.strictEqual(formatRelativeTime(Date.now() - 25 * 3600 * 1000), "Yesterday");
assert.strictEqual(formatRelativeTime(Date.now() - 3 * 86400 * 1000), "3d ago");
console.log("  Passed: Relative timestamps formatted accurately");

console.log("\n==========================================");
console.log(" ALL 9 TICKET 4 VERIFICATION CHECKS PASSED ");
console.log("==========================================");
