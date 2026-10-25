const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const question = document.getElementById("question");
const trace = document.getElementById("trace");
const sourceUsed = document.getElementById("sourceUsed");
const workflowSelect = document.getElementById("workflowSelect");
const workflowUsed = document.getElementById("workflowUsed");

function escapeHtml(s = "") {
  return s.replace(
    /[&<>'"]/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        c
      ],
  );
}
function formatText(s = "") {
  return escapeHtml(s).replace(/\n/g, "<br>");
}

// پاسخ را امن به JSON تبدیل میکند؛ اگر JSON نبود، متن خام یا statusText را برمیگرداند
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

function addMessage(role, text, source = "", citations = [], workflow = "") {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  const citeHtml = citations.length
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
  wrap.innerHTML = `<div class="avatar">AI</div><div class="bubble">${formatText(text)}${metaHtml}${citeHtml}</div>`;
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
}

function renderTrace(items = []) {
  trace.innerHTML = items.length
    ? items
        .map((x) => `<div class="trace-item">${escapeHtml(x)}</div>`)
        .join("")
    : '<div class="empty">No trace.</div>';
}

async function loadWorkflows() {
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

async function askAgent(q) {
  addMessage("user", q);
  question.value = "";
  renderTrace(["Running workflow..."]);
  sourceUsed.textContent = "Running";
  workflowUsed.textContent = workflowSelect.value || "default";

  const btn = form.querySelector("button");
  btn.disabled = true;
  workflowSelect.disabled = true;

  try {
    const body = { question: q };
    if (workflowSelect.value) body.workflow = workflowSelect.value;

    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await readJsonSafe(res);
    if (!res.ok) throw new Error(data.detail || "Request failed");

    addMessage(
      "assistant",
      data.answer,
      data.source_used,
      data.citations || [],
      data.workflow || "",
    );
    renderTrace(data.trace || []);
    sourceUsed.textContent = data.source_used;
    workflowUsed.textContent = data.workflow || "—";
  } catch (e) {
    addMessage("assistant", `Error: ${e.message}`);
    renderTrace(["Request failed"]);
    sourceUsed.textContent = "Error";
    workflowUsed.textContent = "Error";
  } finally {
    btn.disabled = false;
    workflowSelect.disabled = false;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = question.value.trim();
  if (q) askAgent(q);
});

document
  .querySelectorAll(".example")
  .forEach((b) =>
    b.addEventListener("click", () => askAgent(b.textContent.trim())),
  );

/* ---------- upload modal ---------- */
const modal = document.getElementById("uploadModal");
document.getElementById("openUpload").onclick = () =>
  modal.classList.remove("hidden");
document.getElementById("closeUpload").onclick = () =>
  modal.classList.add("hidden");

document.getElementById("uploadBtn").onclick = async () => {
  const file = document.getElementById("fileInput").files[0];
  const key = document.getElementById("adminKey").value;
  const status = document.getElementById("uploadStatus");
  if (!file) {
    status.textContent = "Choose a file first.";
    return;
  }
  status.textContent = "Indexing document...";

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
    status.textContent = `Indexed ${d.file}: ${d.chunks} chunks.`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
};

/* ---------- init ---------- */
loadWorkflows();
