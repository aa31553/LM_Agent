const API_PREFIX = localStorage.getItem("lmAgentApiBase") || "http://127.0.0.1:8000/api/v1";
const tabs = {
  overview: "狀態",
  knowledge: "知識庫",
  documents: "文件",
  skills: "Skills",
  chat: "問答",
  admin: "管理稽核",
  llmwiki: "LLMWiki",
  raw: "API",
};

const state = {
  token: localStorage.getItem("lmAgentToken") || "admin",
  requestId: localStorage.getItem("lmAgentRequestId") || newRequestId(),
  selectedKbId: localStorage.getItem("lmAgentKbId") || "",
  selectedDocId: localStorage.getItem("lmAgentDocId") || "",
  sessionId: localStorage.getItem("lmAgentSessionId") || "",
  knowledgeBases: [],
  documents: [],
  skills: [],
  selectedSkillName: localStorage.getItem("lmAgentSkillName") || "",
  llmwikiPage: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

document.addEventListener("DOMContentLoaded", () => {
  $("#connectionLabel").textContent = API_PREFIX;
  $("#tokenInput").value = state.token;
  $("#requestIdInput").value = state.requestId;
  $("#tokenInput").addEventListener("input", (event) => {
    state.token = event.target.value.trim();
    localStorage.setItem("lmAgentToken", state.token);
  });
  $("#requestIdInput").addEventListener("input", (event) => {
    state.requestId = event.target.value.trim();
    localStorage.setItem("lmAgentRequestId", state.requestId);
  });
  $("#newRequestIdBtn").addEventListener("click", () => {
    state.requestId = newRequestId();
    $("#requestIdInput").value = state.requestId;
    localStorage.setItem("lmAgentRequestId", state.requestId);
  });
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });
  renderAll();
  loadStartupData();
});

function renderAll() {
  renderOverview();
  renderKnowledge();
  renderLLMWiki();
  renderDocuments();
  renderSkills();
  renderChat();
  renderAdmin();
  renderRaw();
}

function activateTab(tab) {
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  $$(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === tab));
  $("#activeTitle").textContent = tabs[tab] || tab;
}

async function loadStartupData() {
  await Promise.allSettled([
    refreshKnowledgeBases(),
    refreshDocuments(),
    refreshSkills(),
    refreshOverview(),
  ]);
}

function newRequestId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function api(path, options = {}) {
  const headers = {
    Authorization: `Bearer ${state.token}`,
    "X-Request-ID": state.requestId,
    ...(options.headers || {}),
  };
  let body = options.body;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: options.method || "GET",
    headers,
    body,
  });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message = payload?.message || payload?.detail || response.statusText;
    const error = new Error(message);
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showAlert(message, tone = "warning") {
  const alert = $("#globalAlert");
  alert.hidden = false;
  alert.textContent = message;
  alert.dataset.tone = tone;
  clearTimeout(showAlert.timer);
  showAlert.timer = setTimeout(() => {
    alert.hidden = true;
  }, 5200);
}

function writeOutput(id, payload) {
  const node = $(id);
  if (!node) return;
  node.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
}

function errorPayload(error) {
  return {
    status: error.status || "client",
    message: error.message,
    details: error.payload || null,
  };
}

function statusBadge(value) {
  const safe = escapeHtml(String(value ?? "-"));
  return `<span class="status ${safe}">${safe}</span>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function optionHtml(items, selectedId = "") {
  const empty = `<option value="">選擇知識庫</option>`;
  return (
    empty +
    items
      .map((item) => {
        const id = item.knowledge_base_id;
        const selected = id === selectedId ? "selected" : "";
        return `<option value="${escapeHtml(id)}" ${selected}>${escapeHtml(item.name)} (${escapeHtml(item.default_confidential_level)})</option>`;
      })
      .join("")
  );
}

function renderOverview() {
  $("#overview").innerHTML = `
    <div class="grid">
      <div class="metric-row">
        <div class="metric"><span>知識庫</span><b id="metricKb">-</b></div>
        <div class="metric"><span>文件</span><b id="metricDocs">-</b></div>
        <div class="metric"><span>選取知識庫</span><b id="metricSelectedKb">-</b></div>
        <div class="metric"><span>選取文件</span><b id="metricSelectedDoc">-</b></div>
      </div>
      <div class="grid two">
        <div class="panel">
          <div class="panel-header">
            <h2>服務狀態</h2>
            <div class="actions">
              <button data-action="health">Health</button>
              <button data-action="dependencies">Dependencies</button>
              <button data-action="adminStatus">Admin</button>
            </div>
          </div>
          <div id="overviewOutput" class="output"></div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>營運指標</h2>
            <button data-action="metrics">更新</button>
          </div>
          <div id="metricsOutput" class="output"></div>
        </div>
      </div>
    </div>
  `;
  $("#overview").addEventListener("click", onOverviewClick);
  updateMetricsShell();
}

async function onOverviewClick(event) {
  const action = event.target.dataset.action;
  if (!action) return;
  try {
    if (action === "health") writeOutput("#overviewOutput", await api("/health"));
    if (action === "dependencies") writeOutput("#overviewOutput", await api("/health/dependencies"));
    if (action === "adminStatus") writeOutput("#overviewOutput", await api("/admin/status"));
    if (action === "metrics") writeOutput("#metricsOutput", await api("/admin/operations/metrics"));
  } catch (error) {
    writeOutput(action === "metrics" ? "#metricsOutput" : "#overviewOutput", errorPayload(error));
  }
}

async function refreshOverview() {
  try {
    writeOutput("#overviewOutput", await api("/health"));
  } catch (error) {
    writeOutput("#overviewOutput", errorPayload(error));
  }
  updateMetricsShell();
}

function updateMetricsShell() {
  $("#metricKb").textContent = state.knowledgeBases.length;
  $("#metricDocs").textContent = state.documents.length;
  $("#metricSelectedKb").textContent = state.selectedKbId ? state.selectedKbId.slice(0, 8) : "-";
  $("#metricSelectedDoc").textContent = state.selectedDocId ? state.selectedDocId.slice(0, 8) : "-";
}

function renderKnowledge() {
  $("#knowledge").innerHTML = `
    <div class="grid two">
      <div class="panel">
        <div class="panel-header">
          <h2>建立知識庫</h2>
          <button class="primary" id="createKbBtn">建立</button>
        </div>
        <div class="form-grid">
          <label class="wide">名稱<input id="kbName" value="default-kb" /></label>
          <label>部門<input id="kbDept" placeholder="qa" /></label>
          <label>預設密級
            <select id="kbLevel">
              <option>public</option><option selected>internal</option><option>confidential</option><option>restricted</option>
            </select>
          </label>
          <label class="full">描述<textarea id="kbDescription"></textarea></label>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>知識庫權限</h2>
          <div class="actions">
            <button id="loadKbPermBtn">讀取</button>
            <button class="primary" id="saveKbPermBtn">儲存</button>
            <button class="danger" id="deleteKbPermBtn">刪除</button>
          </div>
        </div>
        <div class="form-grid">
          <label class="wide">Knowledge Base ID<input id="kbPermId" /></label>
          <label>對象<select id="kbPermSubject"><option>user</option><option>role</option><option>department</option></select></label>
          <label>權限<select id="kbPermLevel"><option>read</option><option>write</option><option>admin</option></select></label>
          <label class="wide">值<input id="kbPermValue" placeholder="admin 或 qa" /></label>
          <label class="wide">Permission ID<input id="kbPermissionIdInput" /></label>
        </div>
      </div>
      <div class="panel full-width">
        <div class="panel-header">
          <h2>知識庫列表</h2>
          <button id="refreshKbBtn">更新</button>
        </div>
        <div id="kbTable"></div>
      </div>
      <div class="panel">
        <div class="panel-header"><h2>回應</h2></div>
        <div id="kbOutput" class="output"></div>
      </div>
    </div>
  `;
  $("#kbPermId").value = state.selectedKbId;
  $("#refreshKbBtn").addEventListener("click", refreshKnowledgeBases);
  $("#createKbBtn").addEventListener("click", createKnowledgeBase);
  $("#loadKbPermBtn").addEventListener("click", loadKbPermissions);
  $("#saveKbPermBtn").addEventListener("click", saveKbPermission);
  $("#deleteKbPermBtn").addEventListener("click", deleteKbPermission);
  renderKnowledgeTable();
}

async function refreshKnowledgeBases() {
  try {
    const payload = await api("/knowledge-bases");
    state.knowledgeBases = payload.items || [];
    renderKnowledgeTable();
    syncKbSelectors();
    updateMetricsShell();
    writeOutput("#kbOutput", payload);
  } catch (error) {
    writeOutput("#kbOutput", errorPayload(error));
  }
}

async function createKnowledgeBase() {
  try {
    const payload = await api("/knowledge-bases", {
      method: "POST",
      json: {
        name: $("#kbName").value.trim(),
        description: $("#kbDescription").value.trim() || null,
        owner_department: $("#kbDept").value.trim() || null,
        default_confidential_level: $("#kbLevel").value,
      },
    });
    state.selectedKbId = payload.knowledge_base_id;
    localStorage.setItem("lmAgentKbId", state.selectedKbId);
    $("#kbPermId").value = state.selectedKbId;
    await refreshKnowledgeBases();
    writeOutput("#kbOutput", payload);
  } catch (error) {
    writeOutput("#kbOutput", errorPayload(error));
  }
}

function renderKnowledgeTable() {
  const rows = state.knowledgeBases
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.name)}</td>
          <td>${escapeHtml(item.knowledge_base_id)}</td>
          <td>${escapeHtml(item.owner_department || "-")}</td>
          <td>${statusBadge(item.default_confidential_level)}</td>
          <td>${escapeHtml(item.document_count)}</td>
          <td><button data-kb-id="${escapeHtml(item.knowledge_base_id)}">選取</button></td>
        </tr>
      `,
    )
    .join("");
  $("#kbTable").innerHTML = table(["名稱", "ID", "部門", "密級", "文件", ""], rows);
  $$("#kbTable button").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedKbId = button.dataset.kbId;
      localStorage.setItem("lmAgentKbId", state.selectedKbId);
      $("#kbPermId").value = state.selectedKbId;
      syncKbSelectors();
      updateMetricsShell();
      showAlert("已選取知識庫");
    });
  });
}

async function loadKbPermissions() {
  try {
    const id = $("#kbPermId").value.trim() || state.selectedKbId;
    writeOutput("#kbOutput", await api(`/permissions/knowledge-bases/${id}`));
  } catch (error) {
    writeOutput("#kbOutput", errorPayload(error));
  }
}

async function saveKbPermission() {
  try {
    const id = $("#kbPermId").value.trim() || state.selectedKbId;
    const payload = await api(`/permissions/knowledge-bases/${id}`, {
      method: "POST",
      json: {
        subject_type: $("#kbPermSubject").value,
        subject_value: $("#kbPermValue").value.trim(),
        permission: $("#kbPermLevel").value,
      },
    });
    writeOutput("#kbOutput", payload);
  } catch (error) {
    writeOutput("#kbOutput", errorPayload(error));
  }
}

async function deleteKbPermission() {
  try {
    const id = $("#kbPermissionIdInput").value.trim();
    if (!id) throw new Error("請輸入 Permission ID");
    await api(`/permissions/knowledge-bases/permissions/${id}`, { method: "DELETE" });
    writeOutput("#kbOutput", "deleted");
  } catch (error) {
    writeOutput("#kbOutput", errorPayload(error));
  }
}

function syncKbSelectors() {
  ["#uploadKbId", "#chatKbId", "#llmwikiKbId"].forEach((selector) => {
    const node = $(selector);
    if (node) node.innerHTML = optionHtml(state.knowledgeBases, state.selectedKbId);
  });
}

function renderLLMWiki() {
  $("#llmwiki").innerHTML = `
    <div class="grid">
      <div class="panel">
        <div class="panel-header">
          <h2>LLMWiki Knowledge Compiler</h2>
          <div class="actions">
            <button id="llmwikiSearchBtn">Search</button>
            <button id="llmwikiBuildBtn">Preview</button>
            <button class="primary" id="llmwikiCompileBtn">Compile</button>
            <button id="llmwikiIndexBtn">Index</button>
            <button id="llmwikiLintBtn">Lint</button>
            <button id="llmwikiGraphBtn">Graph</button>
            <button id="llmwikiDemoBtn">Demo</button>
          </div>
        </div>
        <div class="form-grid">
          <label class="wide">Knowledge Base<select id="llmwikiKbId"></select></label>
          <label class="wide">Topic<input id="llmwikiTopic" value="LLMWiki" /></label>
          <label>Top K<input id="llmwikiTopK" type="number" min="1" max="80" value="24" /></label>
        </div>
      </div>
      <div class="metric-row">
        <div class="metric"><span>Status</span><b id="llmwikiCompiledStatus">Preview</b></div>
        <div class="metric"><span>Sources</span><b id="llmwikiSourceCount">-</b></div>
        <div class="metric"><span>Evidence</span><b id="llmwikiChunkCount">-</b></div>
        <div class="metric"><span>Links</span><b id="llmwikiLinkCount">-</b></div>
      </div>
      <div class="grid two">
        <div class="panel">
          <div class="panel-header"><h2>Compiled Knowledge</h2></div>
          <div id="llmwikiSummary" class="wiki-summary muted">No topic loaded.</div>
          <div id="llmwikiLinks" class="wiki-links"></div>
        </div>
        <div class="panel">
          <div class="panel-header"><h2>Knowledge Graph</h2></div>
          <div id="llmwikiGraph" class="wiki-graph"></div>
        </div>
      </div>
      <div class="panel full-width">
        <div class="panel-header"><h2>Evidence</h2></div>
        <div id="llmwikiEvidence"></div>
      </div>
      <div class="panel full-width">
        <div class="panel-header"><h2>Compiled Markdown</h2></div>
        <div id="llmwikiMarkdown" class="output"></div>
      </div>
      <div class="panel">
        <div class="panel-header"><h2>API Output</h2></div>
        <div id="llmwikiOutput" class="output"></div>
      </div>
    </div>
  `;
  $("#llmwikiSearchBtn").addEventListener("click", searchLLMWikiTopics);
  $("#llmwikiBuildBtn").addEventListener("click", buildLLMWikiPage);
  $("#llmwikiCompileBtn").addEventListener("click", compileLLMWikiPage);
  $("#llmwikiIndexBtn").addEventListener("click", loadLLMWikiIndex);
  $("#llmwikiLintBtn").addEventListener("click", lintLLMWiki);
  $("#llmwikiGraphBtn").addEventListener("click", loadLLMWikiGraph);
  $("#llmwikiDemoBtn").addEventListener("click", loadLLMWikiDemo);
  syncKbSelectors();
}

function llmwikiParams(includeTopK = true) {
  const kbId = $("#llmwikiKbId").value || state.selectedKbId;
  if (!kbId) throw new Error("Select a knowledge base first.");
  const params = new URLSearchParams();
  params.append("knowledge_base_ids", kbId);
  if (includeTopK) params.set("top_k", $("#llmwikiTopK").value || "12");
  return params;
}

function llmwikiTopic() {
  const topic = $("#llmwikiTopic").value.trim();
  if (!topic) throw new Error("Enter a topic.");
  return topic;
}

async function searchLLMWikiTopics() {
  try {
    const topic = llmwikiTopic();
    const params = llmwikiParams(false);
    params.set("q", topic);
    params.set("limit", "10");
    const payload = await api(`/llmwiki/search?${params}`);
    renderLLMWikiSearch(payload.items || []);
    writeOutput("#llmwikiOutput", payload);
  } catch (error) {
    writeOutput("#llmwikiOutput", errorPayload(error));
  }
}

async function buildLLMWikiPage() {
  try {
    const topic = llmwikiTopic();
    const params = llmwikiParams(true);
    params.set("include_graph", "true");
    params.set("prefer_compiled", "true");
    const payload = await api(`/llmwiki/topics/${encodeURIComponent(topic)}?${params}`);
    state.llmwikiPage = payload;
    renderLLMWikiPage(payload);
    writeOutput("#llmwikiOutput", payload);
  } catch (error) {
    writeOutput("#llmwikiOutput", errorPayload(error));
  }
}

async function compileLLMWikiPage() {
  try {
    const topic = llmwikiTopic();
    const params = llmwikiParams(true);
    params.set("include_graph", "true");
    const payload = await api(`/llmwiki/topics/${encodeURIComponent(topic)}/compile?${params}`, {
      method: "POST",
    });
    state.llmwikiPage = payload.page;
    renderLLMWikiPage(payload.page);
    writeOutput("#llmwikiOutput", payload);
  } catch (error) {
    writeOutput("#llmwikiOutput", errorPayload(error));
  }
}

async function loadLLMWikiIndex() {
  try {
    const params = llmwikiParams(false);
    const payload = await api(`/llmwiki/index?${params}`);
    renderLLMWikiIndex(payload.items || []);
    writeOutput("#llmwikiOutput", payload);
  } catch (error) {
    writeOutput("#llmwikiOutput", errorPayload(error));
  }
}

async function lintLLMWiki() {
  try {
    const params = llmwikiParams(false);
    const payload = await api(`/llmwiki/lint?${params}`);
    renderLLMWikiLint(payload);
    writeOutput("#llmwikiOutput", payload);
  } catch (error) {
    writeOutput("#llmwikiOutput", errorPayload(error));
  }
}

async function loadLLMWikiDemo() {
  try {
    const payload = await api("/llmwiki/demo");
    $("#llmwikiTopic").value = payload.topic;
    state.llmwikiPage = payload;
    renderLLMWikiPage(payload);
    writeOutput("#llmwikiOutput", payload);
  } catch (error) {
    writeOutput("#llmwikiOutput", errorPayload(error));
  }
}

async function loadLLMWikiGraph() {
  try {
    const topic = llmwikiTopic();
    const params = llmwikiParams(true);
    const payload = await api(`/llmwiki/topics/${encodeURIComponent(topic)}/graph?${params}`);
    drawLLMWikiGraph(payload);
    writeOutput("#llmwikiOutput", payload);
  } catch (error) {
    writeOutput("#llmwikiOutput", errorPayload(error));
  }
}

function renderLLMWikiIndex(items) {
  updateLLMWikiMetrics(null, items.length);
  const rows = items
    .map(
      (item) => `
        <tr>
          <td><button data-topic="${escapeHtml(item.topic)}">${escapeHtml(item.topic)}</button></td>
          <td>${statusBadge(item.stale ? "stale" : "compiled")}</td>
          <td>${escapeHtml(item.source_document_count)}</td>
          <td>${escapeHtml(item.source_chunk_count)}</td>
          <td>${escapeHtml(item.updated_at)}</td>
          <td>${escapeHtml((item.linked_topics || []).join(", "))}</td>
        </tr>
      `,
    )
    .join("");
  $("#llmwikiEvidence").innerHTML = table(["Topic", "Status", "Sources", "Evidence", "Updated", "Links"], rows);
  $$("#llmwikiEvidence button[data-topic]").forEach((button) => {
    button.addEventListener("click", async () => {
      $("#llmwikiTopic").value = button.dataset.topic;
      await buildLLMWikiPage();
    });
  });
}

function renderLLMWikiLint(payload) {
  const issues = payload.issues || [];
  const issueRows = issues
    .map(
      (item) => `
        <tr>
          <td>${statusBadge(item.severity)}</td>
          <td>${escapeHtml(item.code)}</td>
          <td>${escapeHtml(item.topic || "-")}</td>
          <td>${escapeHtml(item.message)}</td>
        </tr>
      `,
    )
    .join("");
  const suggestions = (payload.suggested_topics || [])
    .map((topic) => `<button data-topic="${escapeHtml(topic)}">${escapeHtml(topic)}</button>`)
    .join("");
  $("#llmwikiEvidence").innerHTML = `
    ${table(["Severity", "Code", "Topic", "Message"], issueRows)}
    <div class="wiki-links">${suggestions}</div>
  `;
  $$("#llmwikiEvidence button[data-topic]").forEach((button) => {
    button.addEventListener("click", async () => {
      $("#llmwikiTopic").value = button.dataset.topic;
      await buildLLMWikiPage();
    });
  });
  updateLLMWikiMetrics({
    source_document_count: payload.checked_pages,
    source_chunk_count: issues.length,
    linked_topics: payload.suggested_topics || [],
  });
}

function renderLLMWikiSearch(items) {
  const rows = items
    .map(
      (item) => `
        <tr>
          <td><button data-topic="${escapeHtml(item.topic)}">${escapeHtml(item.topic)}</button></td>
          <td>${escapeHtml(item.evidence_count)}</td>
          <td>${escapeHtml(item.document_count)}</td>
          <td>${escapeHtml((item.related_topics || []).join(", "))}</td>
        </tr>
      `,
    )
    .join("");
  $("#llmwikiEvidence").innerHTML = table(["Topic", "Evidence", "Documents", "Related"], rows);
  $$("#llmwikiEvidence button[data-topic]").forEach((button) => {
    button.addEventListener("click", async () => {
      $("#llmwikiTopic").value = button.dataset.topic;
      await buildLLMWikiPage();
    });
  });
}

function renderLLMWikiPage(page) {
  $("#llmwikiSummary").classList.remove("muted");
  const status = page.compiled_page_id ? (page.stale ? "Compiled, stale" : "Compiled") : "Preview";
  $("#llmwikiSummary").innerHTML = `
    <h3>${escapeHtml(page.topic)}</h3>
    <div class="wiki-meta">
      ${statusBadge(status.toLowerCase().replaceAll(" ", "-").replaceAll(",", ""))}
      <span>${escapeHtml(page.last_compiled_at || "not persisted")}</span>
    </div>
    <p>${escapeHtml(page.summary || "")}</p>
    <ul>${(page.key_points || []).map((point) => `<li>${escapeHtml(point)}</li>`).join("")}</ul>
    ${(page.contradictions || []).length ? `<h4>Contradictions</h4><ul>${page.contradictions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
    ${(page.maintenance_notes || []).length ? `<h4>Maintenance</h4><ul>${page.maintenance_notes.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
  `;
  $("#llmwikiLinks").innerHTML = (page.linked_topics || [])
    .map((link) => `<button data-topic="${escapeHtml(link.topic)}">${escapeHtml(link.topic)}</button>`)
    .join("");
  $$("#llmwikiLinks button[data-topic]").forEach((button) => {
    button.addEventListener("click", async () => {
      $("#llmwikiTopic").value = button.dataset.topic;
      await buildLLMWikiPage();
    });
  });
  renderLLMWikiEvidence(page.evidence || []);
  drawLLMWikiGraph(page.graph);
  writeOutput("#llmwikiMarkdown", page.content_markdown || "");
  updateLLMWikiMetrics(page);
}

function renderLLMWikiEvidence(evidence) {
  const rows = evidence
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.document_title || item.document_id)}</td>
          <td>${escapeHtml(item.section_title || "-")}</td>
          <td>${escapeHtml(item.snippet)}</td>
          <td>${statusBadge(item.confidential_level)}</td>
          <td>${escapeHtml(Number(item.score || 0).toFixed(2))}</td>
        </tr>
      `,
    )
    .join("");
  $("#llmwikiEvidence").innerHTML = table(["Document", "Section", "Snippet", "Level", "Score"], rows);
}

function updateLLMWikiMetrics(page, indexCount = null) {
  $("#llmwikiCompiledStatus").textContent = page
    ? page.compiled_page_id
      ? page.stale
        ? "Stale"
        : "Compiled"
      : "Preview"
    : indexCount === null
      ? "-"
      : `${indexCount} pages`;
  $("#llmwikiSourceCount").textContent = page ? page.source_document_count : "-";
  $("#llmwikiChunkCount").textContent = page ? page.source_chunk_count : "-";
  $("#llmwikiLinkCount").textContent = page ? (page.linked_topics || []).length : "-";
}

function drawLLMWikiGraph(graph) {
  const container = $("#llmwikiGraph");
  if (!graph || !graph.nodes?.length) {
    container.innerHTML = `<div class="muted">No graph available.</div>`;
    return;
  }
  const width = Math.max(container.clientWidth || 520, 420);
  const height = 360;
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = Math.min(width, height) * 0.35;
  const nodes = graph.nodes.map((node, index) => {
    if (node.type === "topic" && index === 0) return { ...node, x: centerX, y: centerY };
    const angle = ((index - 1) / Math.max(graph.nodes.length - 1, 1)) * Math.PI * 2;
    const nodeRadius = node.type === "document" ? radius * 0.72 : radius;
    return {
      ...node,
      x: centerX + Math.cos(angle) * nodeRadius,
      y: centerY + Math.sin(angle) * nodeRadius,
    };
  });
  const byId = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const edges = (graph.edges || [])
    .map((edge) => ({ ...edge, sourceNode: byId[edge.source], targetNode: byId[edge.target] }))
    .filter((edge) => edge.sourceNode && edge.targetNode);
  container.innerHTML = "";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.classList.add("wiki-graph-svg");
  edges.forEach((edge) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", edge.sourceNode.x);
    line.setAttribute("y1", edge.sourceNode.y);
    line.setAttribute("x2", edge.targetNode.x);
    line.setAttribute("y2", edge.targetNode.y);
    line.setAttribute("class", `wiki-edge ${edge.relation}`);
    svg.appendChild(line);
  });
  nodes.forEach((node) => {
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.setAttribute("class", `wiki-node ${node.type}`);
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", node.x);
    circle.setAttribute("cy", node.y);
    circle.setAttribute("r", node.type === "topic" ? 18 : node.type === "document" ? 13 : 9);
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", node.x);
    label.setAttribute("y", node.y + 28);
    label.textContent = node.label.length > 28 ? `${node.label.slice(0, 25)}...` : node.label;
    group.append(circle, label);
    svg.appendChild(group);
  });
  container.appendChild(svg);
}

function renderDocuments() {
  $("#documents").innerHTML = `
    <div class="grid">
      <div class="panel">
        <div class="panel-header">
          <h2>上傳文件</h2>
          <button class="primary" id="uploadDocBtn">上傳</button>
        </div>
        <div class="form-grid">
          <label class="wide">檔案<input id="uploadFile" type="file" /></label>
          <label class="wide">知識庫<select id="uploadKbId"></select></label>
          <label>密級<select id="uploadLevel"><option>public</option><option selected>internal</option><option>confidential</option><option>restricted</option></select></label>
          <label>部門<input id="uploadDept" /></label>
          <label>類型<input id="uploadType" value="manual_upload" /></label>
          <label>版本<input id="uploadVersion" /></label>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>文件列表</h2>
          <div class="actions">
            <select id="docStatusFilter"><option value="">全部狀態</option><option>uploaded</option><option>parsing</option><option>ocr_processing</option><option>chunking</option><option>embedding</option><option>indexing</option><option>ready</option><option>failed</option><option>archived</option></select>
            <button id="refreshDocsBtn">更新</button>
          </div>
        </div>
        <div id="docTable"></div>
      </div>
      <div class="grid two">
        <div class="panel">
          <div class="panel-header">
            <h2>文件操作</h2>
            <div class="actions">
              <button id="docDetailBtn">詳情</button>
              <button id="docStatusBtn">狀態</button>
              <button class="warning" id="docReindexBtn">重建索引</button>
              <button class="danger" id="docArchiveBtn">封存</button>
            </div>
          </div>
          <label>Document ID<input id="docIdInput" /></label>
          <div id="docOutput" class="output"></div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>文件權限</h2>
            <div class="actions">
              <button id="loadDocPermBtn">讀取</button>
              <button class="primary" id="saveDocPermBtn">儲存</button>
              <button class="danger" id="deletePermBtn">刪除</button>
            </div>
          </div>
          <div class="form-grid">
            <label>對象<select id="docPermSubject"><option>user</option><option>role</option><option>department</option></select></label>
            <label>權限<select id="docPermLevel"><option>read</option><option>write</option><option>admin</option></select></label>
            <label class="wide">值<input id="docPermValue" /></label>
            <label class="wide">Permission ID<input id="permissionIdInput" /></label>
          </div>
          <div id="permissionOutput" class="output"></div>
        </div>
      </div>
    </div>
  `;
  $("#docIdInput").value = state.selectedDocId;
  $("#refreshDocsBtn").addEventListener("click", refreshDocuments);
  $("#docStatusFilter").addEventListener("change", refreshDocuments);
  $("#uploadDocBtn").addEventListener("click", uploadDocument);
  $("#docDetailBtn").addEventListener("click", () => documentAction("detail"));
  $("#docStatusBtn").addEventListener("click", () => documentAction("status"));
  $("#docReindexBtn").addEventListener("click", () => documentAction("reindex"));
  $("#docArchiveBtn").addEventListener("click", () => documentAction("archive"));
  $("#loadDocPermBtn").addEventListener("click", loadDocPermissions);
  $("#saveDocPermBtn").addEventListener("click", saveDocPermission);
  $("#deletePermBtn").addEventListener("click", deletePermission);
  syncKbSelectors();
  renderDocumentTable();
}

async function refreshDocuments() {
  try {
    const params = new URLSearchParams();
    const status = $("#docStatusFilter")?.value || "";
    if (status) params.set("status", status);
    const payload = await api(`/documents${params.toString() ? `?${params}` : ""}`);
    state.documents = payload.items || [];
    renderDocumentTable();
    updateMetricsShell();
    writeOutput("#docOutput", payload);
  } catch (error) {
    writeOutput("#docOutput", errorPayload(error));
  }
}

async function uploadDocument() {
  try {
    const file = $("#uploadFile").files[0];
    if (!file) throw new Error("請選擇檔案");
    const kbId = $("#uploadKbId").value || state.selectedKbId;
    if (!kbId) throw new Error("請先選擇知識庫");
    const form = new FormData();
    form.append("file", file);
    form.append("knowledge_base_id", kbId);
    form.append("confidential_level", $("#uploadLevel").value);
    if ($("#uploadDept").value.trim()) form.append("department", $("#uploadDept").value.trim());
    if ($("#uploadType").value.trim()) form.append("document_type", $("#uploadType").value.trim());
    if ($("#uploadVersion").value.trim()) form.append("version", $("#uploadVersion").value.trim());
    const payload = await api("/documents/upload", { method: "POST", body: form });
    state.selectedDocId = payload.document_id;
    localStorage.setItem("lmAgentDocId", state.selectedDocId);
    $("#docIdInput").value = state.selectedDocId;
    await refreshDocuments();
    writeOutput("#docOutput", payload);
  } catch (error) {
    writeOutput("#docOutput", errorPayload(error));
  }
}

function renderDocumentTable() {
  const rows = state.documents
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.filename)}</td>
          <td>${escapeHtml(item.document_id)}</td>
          <td>${statusBadge(item.status)}</td>
          <td>${statusBadge(item.confidential_level)}</td>
          <td>${escapeHtml(item.chunk_count)}</td>
          <td>${escapeHtml(item.department || "-")}</td>
          <td><button data-doc-id="${escapeHtml(item.document_id)}">選取</button></td>
        </tr>
      `,
    )
    .join("");
  $("#docTable").innerHTML = table(["檔名", "ID", "狀態", "密級", "Chunks", "部門", ""], rows);
  $$("#docTable button").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDocId = button.dataset.docId;
      localStorage.setItem("lmAgentDocId", state.selectedDocId);
      $("#docIdInput").value = state.selectedDocId;
      updateMetricsShell();
      showAlert("已選取文件");
    });
  });
}

async function documentAction(action) {
  const id = $("#docIdInput").value.trim() || state.selectedDocId;
  try {
    if (!id) throw new Error("請選擇文件");
    let payload;
    if (action === "detail") payload = await api(`/documents/${id}`);
    if (action === "status") payload = await api(`/documents/${id}/status`);
    if (action === "reindex") payload = await api(`/documents/${id}/reindex`, { method: "POST" });
    if (action === "archive") payload = await api(`/documents/${id}/archive`, { method: "POST" });
    if (action === "archive") await refreshDocuments();
    writeOutput("#docOutput", payload);
  } catch (error) {
    writeOutput("#docOutput", errorPayload(error));
  }
}

async function loadDocPermissions() {
  try {
    const id = $("#docIdInput").value.trim() || state.selectedDocId;
    writeOutput("#permissionOutput", await api(`/permissions/documents/${id}`));
  } catch (error) {
    writeOutput("#permissionOutput", errorPayload(error));
  }
}

async function saveDocPermission() {
  try {
    const id = $("#docIdInput").value.trim() || state.selectedDocId;
    const payload = await api(`/permissions/documents/${id}`, {
      method: "POST",
      json: {
        subject_type: $("#docPermSubject").value,
        subject_value: $("#docPermValue").value.trim(),
        permission: $("#docPermLevel").value,
      },
    });
    writeOutput("#permissionOutput", payload);
  } catch (error) {
    writeOutput("#permissionOutput", errorPayload(error));
  }
}

async function deletePermission() {
  try {
    const id = $("#permissionIdInput").value.trim();
    if (!id) throw new Error("請輸入 Permission ID");
    await api(`/permissions/documents/permissions/${id}`, { method: "DELETE" });
    writeOutput("#permissionOutput", "deleted");
  } catch (error) {
    writeOutput("#permissionOutput", errorPayload(error));
  }
}

function renderSkills() {
  $("#skills").innerHTML = `
    <div class="grid">
      <div class="grid two">
        <div class="panel">
          <div class="panel-header">
            <h2>Skill editor</h2>
            <div class="actions">
              <button id="newSkillBtn">New</button>
              <button class="primary" id="createSkillBtn">Create</button>
              <button id="updateSkillBtn">Update</button>
              <button class="danger" id="deleteSkillBtn">Delete</button>
            </div>
          </div>
          <div class="form-grid">
            <label>Name<input id="skillName" placeholder="research-helper" /></label>
            <label><input id="skillEnabled" type="checkbox" checked /> Enabled</label>
            <label class="full">Description<textarea id="skillDescription" rows="3" placeholder="What the skill does and when to use it"></textarea></label>
            <label class="full">SKILL.md instructions<textarea id="skillInstructions" rows="12" placeholder="# Workflow"></textarea></label>
          </div>
          <div id="skillOutput" class="output compact-output"></div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>Installed skills</h2>
            <button id="refreshSkillsBtn">Refresh</button>
          </div>
          <div id="skillTable"></div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <div>
            <h2>Skill access</h2>
            <p class="muted">No rules means open access. Once a rule exists, only matching users, departments, roles, and administrators can use this skill.</p>
          </div>
          <button class="primary" id="addSkillPermissionBtn">Add / update rule</button>
        </div>
        <div class="form-grid">
          <label>Subject type
            <select id="skillPermissionSubjectType">
              <option value="user">User</option>
              <option value="department">Department</option>
              <option value="role">Role</option>
            </select>
          </label>
          <label class="wide">Subject value<input id="skillPermissionSubjectValue" placeholder="user id, department, or role" /></label>
          <label>Permission
            <select id="skillPermissionLevel">
              <option value="read">Read / use</option>
              <option value="admin">Admin access</option>
            </select>
          </label>
        </div>
        <div id="skillPermissions"></div>
        <div id="skillPermissionOutput" class="output compact-output"></div>
      </div>
      <div class="grid two">
        <div class="panel">
          <div class="panel-header">
            <h2>Bundled files</h2>
            <button class="primary" id="uploadSkillFileBtn">Upload / replace</button>
          </div>
          <div class="form-grid">
            <label class="wide">File<input id="skillResourceFile" type="file" /></label>
            <label class="wide">Relative path<input id="skillResourcePath" placeholder="references/policy.md" /></label>
            <label><input id="skillFileOverwrite" type="checkbox" /> Overwrite</label>
          </div>
          <div id="skillFiles"></div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>Raw file preview</h2>
            <span id="skillPreviewLink" class="muted"></span>
          </div>
          <div id="skillPreview" class="output skill-preview">Select a skill file to view its original content.</div>
        </div>
      </div>
    </div>
  `;
  $("#newSkillBtn").addEventListener("click", resetSkillForm);
  $("#createSkillBtn").addEventListener("click", createSkill);
  $("#updateSkillBtn").addEventListener("click", updateSkill);
  $("#deleteSkillBtn").addEventListener("click", deleteSkill);
  $("#refreshSkillsBtn").addEventListener("click", refreshSkills);
  $("#uploadSkillFileBtn").addEventListener("click", uploadSkillFile);
  $("#addSkillPermissionBtn").addEventListener("click", setSkillPermission);
  renderSkillTable();
  renderSkillFiles([]);
  renderSkillPermissions([]);
}

async function refreshSkills() {
  try {
    const payload = await api("/skills");
    state.skills = payload.items || [];
    renderSkillTable();
    if (state.selectedSkillName && state.skills.some((item) => item.name === state.selectedSkillName)) {
      await selectSkill(state.selectedSkillName, false);
    }
  } catch (error) {
    writeOutput("#skillOutput", errorPayload(error));
  }
}

function renderSkillTable() {
  const node = $("#skillTable");
  if (!node) return;
  const rows = state.skills
    .map(
      (item) => `
        <tr>
          <td><code>$${escapeHtml(item.name)}</code></td>
          <td>${statusBadge(item.enabled ? "enabled" : "disabled")}</td>
          <td>${item.is_system ? "system" : "custom"}</td>
          <td>${item.permission_count ? `restricted (${escapeHtml(item.permission_count)})` : "open"}</td>
          <td>${escapeHtml(item.file_count)}</td>
          <td><button data-skill-name="${escapeHtml(item.name)}">Open</button></td>
        </tr>
      `,
    )
    .join("");
  node.innerHTML = table(["Skill", "Status", "Type", "Access", "Files", ""], rows);
  $$("#skillTable button").forEach((button) => {
    button.addEventListener("click", () => selectSkill(button.dataset.skillName));
  });
}

async function selectSkill(name, previewMainFile = true) {
  try {
    const detail = await api(`/skills/${encodeURIComponent(name)}`);
    state.selectedSkillName = detail.name;
    localStorage.setItem("lmAgentSkillName", detail.name);
    $("#skillName").value = detail.name;
    $("#skillName").readOnly = true;
    $("#skillDescription").value = detail.description;
    $("#skillInstructions").value = detail.instructions;
    $("#skillEnabled").checked = detail.enabled;
    $("#deleteSkillBtn").disabled = detail.is_system;
    renderSkillFiles(detail.files || []);
    await refreshSkillPermissions(detail.name);
    writeOutput("#skillOutput", detail);
    if (previewMainFile) {
      const mainFile = (detail.files || []).find((item) => item.relative_path === "SKILL.md");
      if (mainFile) await previewSkillFile(mainFile);
    }
  } catch (error) {
    writeOutput("#skillOutput", errorPayload(error));
  }
}

function resetSkillForm() {
  state.selectedSkillName = "";
  localStorage.removeItem("lmAgentSkillName");
  $("#skillName").value = "";
  $("#skillName").readOnly = false;
  $("#skillDescription").value = "";
  $("#skillInstructions").value = "# Instructions\n\n";
  $("#skillEnabled").checked = true;
  $("#deleteSkillBtn").disabled = false;
  renderSkillFiles([]);
  renderSkillPermissions([]);
  $("#skillPermissionOutput").textContent = "";
  $("#skillPreviewLink").textContent = "";
  $("#skillPreview").textContent = "Create or select a skill.";
}

async function createSkill() {
  try {
    const payload = await api("/skills", {
      method: "POST",
      json: {
        name: $("#skillName").value.trim(),
        description: $("#skillDescription").value.trim(),
        instructions: $("#skillInstructions").value.trim(),
        enabled: $("#skillEnabled").checked,
      },
    });
    state.selectedSkillName = payload.name;
    localStorage.setItem("lmAgentSkillName", payload.name);
    await refreshSkills();
    await selectSkill(payload.name);
    showAlert(`Created $${payload.name}`, "success");
  } catch (error) {
    writeOutput("#skillOutput", errorPayload(error));
  }
}

async function updateSkill() {
  try {
    const name = state.selectedSkillName || $("#skillName").value.trim();
    if (!name) throw new Error("Select a skill first.");
    const payload = await api(`/skills/${encodeURIComponent(name)}`, {
      method: "PATCH",
      json: {
        description: $("#skillDescription").value.trim(),
        instructions: $("#skillInstructions").value.trim(),
        enabled: $("#skillEnabled").checked,
      },
    });
    await refreshSkills();
    await selectSkill(payload.name);
    showAlert(`Updated $${payload.name}`, "success");
  } catch (error) {
    writeOutput("#skillOutput", errorPayload(error));
  }
}

async function deleteSkill() {
  try {
    const name = state.selectedSkillName;
    if (!name) throw new Error("Select a skill first.");
    if (!window.confirm(`Delete $${name} and all bundled files?`)) return;
    await api(`/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
    resetSkillForm();
    await refreshSkills();
    showAlert(`Deleted $${name}`, "success");
  } catch (error) {
    writeOutput("#skillOutput", errorPayload(error));
  }
}

function renderSkillFiles(files) {
  const node = $("#skillFiles");
  if (!node) return;
  const rows = files
    .map(
      (file) => `
        <tr>
          <td><code>${escapeHtml(file.relative_path)}</code></td>
          <td>${escapeHtml(file.mime_type)}</td>
          <td>${escapeHtml(file.size)}</td>
          <td class="actions">
            <button data-skill-file="view" data-file-path="${escapeHtml(file.relative_path)}">View</button>
            ${file.relative_path === "SKILL.md" ? "" : `<button class="danger" data-skill-file="delete" data-file-path="${escapeHtml(file.relative_path)}">Delete</button>`}
          </td>
        </tr>
      `,
    )
    .join("");
  node.innerHTML = table(["Path", "Type", "Bytes", ""], rows);
  $$("#skillFiles button").forEach((button) => {
    button.addEventListener("click", async () => {
      const detail = await api(`/skills/${encodeURIComponent(state.selectedSkillName)}`);
      const file = detail.files.find((item) => item.relative_path === button.dataset.filePath);
      if (!file) return;
      if (button.dataset.skillFile === "view") await previewSkillFile(file);
      if (button.dataset.skillFile === "delete") await deleteSkillFile(file);
    });
  });
}

async function uploadSkillFile() {
  try {
    if (!state.selectedSkillName) throw new Error("Select a skill first.");
    const file = $("#skillResourceFile").files[0];
    if (!file) throw new Error("Select a file to upload.");
    const form = new FormData();
    form.append("file", file);
    form.append("relative_path", $("#skillResourcePath").value.trim() || file.name);
    form.append("overwrite", $("#skillFileOverwrite").checked ? "true" : "false");
    const payload = await api(`/skills/${encodeURIComponent(state.selectedSkillName)}/files`, {
      method: "POST",
      body: form,
    });
    await selectSkill(state.selectedSkillName, false);
    await previewSkillFile(payload.file);
    showAlert(`Uploaded ${payload.file.relative_path}`, "success");
  } catch (error) {
    writeOutput("#skillOutput", errorPayload(error));
  }
}

async function deleteSkillFile(file) {
  try {
    if (!window.confirm(`Delete ${file.relative_path}?`)) return;
    const encodedPath = file.relative_path.split("/").map(encodeURIComponent).join("/");
    await api(`/skills/${encodeURIComponent(state.selectedSkillName)}/files/${encodedPath}`, {
      method: "DELETE",
    });
    await selectSkill(state.selectedSkillName, false);
    $("#skillPreview").textContent = "File deleted.";
  } catch (error) {
    writeOutput("#skillOutput", errorPayload(error));
  }
}

async function refreshSkillPermissions(name = state.selectedSkillName) {
  if (!name) {
    renderSkillPermissions([]);
    return;
  }
  try {
    const payload = await api(`/permissions/skills/${encodeURIComponent(name)}`);
    renderSkillPermissions(payload.permissions || []);
    $("#skillPermissionOutput").textContent = "";
  } catch (error) {
    renderSkillPermissions([]);
    writeOutput("#skillPermissionOutput", errorPayload(error));
  }
}

function renderSkillPermissions(permissions) {
  const node = $("#skillPermissions");
  if (!node) return;
  const rows = permissions
    .map(
      (permission) => `
        <tr>
          <td>${escapeHtml(permission.subject_type)}</td>
          <td><code>${escapeHtml(permission.subject_value)}</code></td>
          <td>${escapeHtml(permission.permission)}</td>
          <td><button class="danger" data-skill-permission-id="${escapeHtml(permission.permission_id)}">Delete</button></td>
        </tr>
      `,
    )
    .join("");
  node.innerHTML = table(["Subject type", "Subject", "Permission", ""], rows);
  $$("#skillPermissions button").forEach((button) => {
    button.addEventListener("click", () => deleteSkillPermission(button.dataset.skillPermissionId));
  });
}

async function setSkillPermission() {
  try {
    if (!state.selectedSkillName) throw new Error("Select a skill first.");
    const subjectValue = $("#skillPermissionSubjectValue").value.trim();
    if (!subjectValue) throw new Error("Enter a permission subject.");
    await api(`/permissions/skills/${encodeURIComponent(state.selectedSkillName)}`, {
      method: "POST",
      json: {
        subject_type: $("#skillPermissionSubjectType").value,
        subject_value: subjectValue,
        permission: $("#skillPermissionLevel").value,
      },
    });
    $("#skillPermissionSubjectValue").value = "";
    await refreshSkillPermissions();
    await refreshSkills();
    showAlert("Skill permission saved", "success");
  } catch (error) {
    writeOutput("#skillPermissionOutput", errorPayload(error));
  }
}

async function deleteSkillPermission(permissionId) {
  try {
    if (!state.selectedSkillName) throw new Error("Select a skill first.");
    if (!window.confirm("Delete this skill permission?")) return;
    await api(
      `/permissions/skills/${encodeURIComponent(state.selectedSkillName)}/${encodeURIComponent(permissionId)}`,
      { method: "DELETE" },
    );
    await refreshSkillPermissions();
    await refreshSkills();
    showAlert("Skill permission deleted", "success");
  } catch (error) {
    writeOutput("#skillPermissionOutput", errorPayload(error));
  }
}

async function previewSkillFile(file) {
  const preview = $("#skillPreview");
  try {
    const rawUrl = new URL(file.raw_url, API_PREFIX).toString();
    const response = await fetch(rawUrl, {
      headers: {
        Authorization: `Bearer ${state.token}`,
        "X-Request-ID": state.requestId,
      },
    });
    if (!response.ok) throw new Error(`Unable to load ${file.relative_path}`);
    $("#skillPreviewLink").textContent = file.raw_url;
    preview.innerHTML = "";
    if (file.is_text) {
      preview.textContent = await response.text();
      return;
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    if (file.mime_type.startsWith("image/")) {
      const image = document.createElement("img");
      image.src = objectUrl;
      image.alt = file.relative_path;
      image.className = "skill-preview-image";
      preview.appendChild(image);
      return;
    }
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = file.relative_path.split("/").pop();
    link.textContent = `Open original ${file.relative_path}`;
    preview.appendChild(link);
  } catch (error) {
    preview.textContent = error.message;
  }
}

function renderChat() {
  $("#chat").innerHTML = `
    <div class="grid two">
      <div class="panel">
        <div class="panel-header">
          <h2>RAG 問答</h2>
          <div class="actions">
            <button class="primary" id="askBtn">送出</button>
            <button id="streamBtn">串流</button>
            <button id="messagesBtn">訊息</button>
          </div>
        </div>
        <div class="form-grid">
          <label class="wide">知識庫<select id="chatKbId"></select></label>
          <label>Top K<input id="topK" type="number" min="1" max="50" value="8" /></label>
          <label>Session ID<input id="sessionIdInput" /></label>
          <label><input id="useRerank" type="checkbox" checked /> Rerank</label>
          <label><input id="useMasking" type="checkbox" checked /> Masking</label>
          <label><input id="useTools" type="checkbox" /> Tools</label>
          <label class="full">問題<textarea id="chatQuery">請摘要目前文件中的重點。</textarea></label>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header"><h2>答案</h2></div>
        <div id="answerOutput" class="answer"></div>
      </div>
      <div class="panel full-width">
        <div class="panel-header"><h2>回應</h2></div>
        <div id="chatOutput" class="output"></div>
      </div>
    </div>
  `;
  $("#sessionIdInput").value = state.sessionId;
  $("#askBtn").addEventListener("click", askQuestion);
  $("#streamBtn").addEventListener("click", streamQuestion);
  $("#messagesBtn").addEventListener("click", loadSessionMessages);
  syncKbSelectors();
}

function chatPayload() {
  const kbId = $("#chatKbId").value || state.selectedKbId;
  if (!kbId) throw new Error("請選擇知識庫");
  const payload = {
    session_id: $("#sessionIdInput").value.trim() || null,
    knowledge_base_ids: [kbId],
    query: $("#chatQuery").value.trim(),
    top_k: Number($("#topK").value || 8),
    use_rerank: $("#useRerank").checked,
    use_masking: $("#useMasking").checked,
    use_tools: $("#useTools").checked,
  };
  if (!payload.query) throw new Error("請輸入問題");
  return payload;
}

async function askQuestion() {
  try {
    const payload = await api("/chat/query", { method: "POST", json: chatPayload() });
    state.sessionId = payload.session_id;
    localStorage.setItem("lmAgentSessionId", state.sessionId);
    $("#sessionIdInput").value = state.sessionId;
    $("#answerOutput").textContent = payload.answer || "";
    writeOutput("#chatOutput", payload);
  } catch (error) {
    writeOutput("#chatOutput", errorPayload(error));
  }
}

async function streamQuestion() {
  try {
    const payload = chatPayload();
    $("#answerOutput").textContent = "";
    writeOutput("#chatOutput", "streaming...");
    const response = await fetch(`${API_PREFIX}/chat/stream`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${state.token}`,
        "X-Request-ID": state.requestId,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(await response.text());
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const events = [];
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
        if (!dataLine) continue;
        const data = JSON.parse(dataLine.slice(6));
        events.push(data);
        if (typeof data.delta === "string") $("#answerOutput").textContent += data.delta;
        if (data.response?.answer) {
          $("#answerOutput").textContent = data.response.answer;
          state.sessionId = data.response.session_id;
          localStorage.setItem("lmAgentSessionId", state.sessionId);
          $("#sessionIdInput").value = state.sessionId;
        }
      }
    }
    writeOutput("#chatOutput", events);
  } catch (error) {
    writeOutput("#chatOutput", errorPayload(error));
  }
}

async function loadSessionMessages() {
  try {
    const id = $("#sessionIdInput").value.trim() || state.sessionId;
    if (!id) throw new Error("請輸入 Session ID");
    writeOutput("#chatOutput", await api(`/chat/sessions/${id}/messages`));
  } catch (error) {
    writeOutput("#chatOutput", errorPayload(error));
  }
}

function renderAdmin() {
  $("#admin").innerHTML = `
    <div class="grid">
      <div class="grid two">
        <div class="panel">
          <div class="panel-header">
            <h2>敏感規則</h2>
            <div class="actions">
              <button id="listRulesBtn">列表</button>
              <button class="primary" id="saveRuleBtn">儲存</button>
              <button id="templatesBtn">範本</button>
              <button class="warning" id="toggleRuleBtn">切換</button>
            </div>
          </div>
          <div class="form-grid">
            <label>類型<input id="ruleEntity" value="customer_name" /></label>
            <label class="wide">值<input id="ruleValue" /></label>
            <label>替代<input id="ruleReplacement" value="[CUSTOMER]" /></label>
            <label>風險<select id="ruleRisk"><option>low</option><option selected>medium</option><option>high</option></select></label>
            <label>啟用<select id="ruleActive"><option value="true">true</option><option value="false">false</option></select></label>
            <label class="wide">Rule ID<input id="ruleId" /></label>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>保留政策</h2>
            <div class="actions">
              <button id="retentionPreviewBtn">預覽</button>
              <button class="danger" id="retentionApplyBtn">套用</button>
            </div>
          </div>
          <div class="form-grid">
            <label>文件天數<input id="docDays" type="number" min="1" value="365" /></label>
            <label>訊息天數<input id="chatDays" type="number" min="1" value="365" /></label>
            <label>檢索天數<input id="retrievalDays" type="number" min="1" value="90" /></label>
            <label>遮罩天數<input id="maskDays" type="number" min="1" value="180" /></label>
            <label>LLM 天數<input id="llmDays" type="number" min="1" value="180" /></label>
            <label>稽核天數<input id="auditDays" type="number" min="1" value="365" /></label>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>純 LLM 連線測試</h2>
          <button class="primary" id="llmTestBtn">測試連線</button>
        </div>
        <p class="muted">不使用 Knowledge Base、RAG、LLMWiki 或資料庫 Session。</p>
        <div class="form-grid">
          <label class="full">System Prompt<textarea id="llmTestSystem">You are a helpful software engineering assistant.</textarea></label>
          <label class="full">測試訊息<textarea id="llmTestMessage">How far is the moon from earth?</textarea></label>
        </div>
        <div id="llmTestOutput" class="output compact-output"></div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>稽核</h2>
          <div class="actions">
            <button data-audit="/audit/chat-logs">Chat</button>
            <button data-audit="/audit/masking-events">Masking</button>
            <button data-audit="/audit/retrieval-logs">Retrieval</button>
            <button data-audit="/audit/llm-logs">LLM</button>
            <button data-audit="/audit/permission-denied">Denied</button>
            <button data-audit="/admin/operations/metrics">Metrics</button>
          </div>
        </div>
        <div class="form-grid">
          <label>Limit<input id="auditLimit" type="number" min="1" max="500" value="100" /></label>
          <label class="wide">Message ID<input id="auditMessageId" /></label>
          <label class="wide">Document ID<input id="auditDocumentId" /></label>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header"><h2>回應</h2></div>
        <div id="adminOutput" class="output"></div>
      </div>
    </div>
  `;
  $("#listRulesBtn").addEventListener("click", () => adminCall("/admin/sensitive-rules"));
  $("#templatesBtn").addEventListener("click", () => adminCall("/admin/sensitive-rules/templates", { method: "POST" }));
  $("#saveRuleBtn").addEventListener("click", saveRule);
  $("#toggleRuleBtn").addEventListener("click", toggleRule);
  $("#retentionPreviewBtn").addEventListener("click", () => runRetention(false));
  $("#retentionApplyBtn").addEventListener("click", () => runRetention(true));
  $("#llmTestBtn").addEventListener("click", testLLMConnection);
  $$("#admin [data-audit]").forEach((button) => button.addEventListener("click", () => loadAudit(button.dataset.audit)));
}

async function adminCall(path, options = {}) {
  try {
    writeOutput("#adminOutput", await api(path, options));
  } catch (error) {
    writeOutput("#adminOutput", errorPayload(error));
  }
}

async function saveRule() {
  await adminCall("/admin/sensitive-rules", {
    method: "POST",
    json: {
      entity_type: $("#ruleEntity").value.trim(),
      value: $("#ruleValue").value.trim(),
      replacement: $("#ruleReplacement").value.trim() || null,
      risk_level: $("#ruleRisk").value,
      is_active: $("#ruleActive").value === "true",
    },
  });
}

async function toggleRule() {
  const id = $("#ruleId").value.trim();
  if (!id) return writeOutput("#adminOutput", { message: "請輸入 Rule ID" });
  await adminCall(`/admin/sensitive-rules/${id}`, {
    method: "PATCH",
    json: { is_active: $("#ruleActive").value === "true" },
  });
}

async function runRetention(apply) {
  await adminCall("/admin/retention", {
    method: "POST",
    json: {
      apply,
      document_archive_after_days: Number($("#docDays").value),
      chat_message_retention_days: Number($("#chatDays").value),
      retrieval_log_retention_days: Number($("#retrievalDays").value),
      masking_event_retention_days: Number($("#maskDays").value),
      llm_log_retention_days: Number($("#llmDays").value),
      audit_event_retention_days: Number($("#auditDays").value),
    },
  });
}

async function testLLMConnection() {
  const button = $("#llmTestBtn");
  button.disabled = true;
  button.textContent = "測試中…";
  try {
    const payload = await api("/admin/llm/test", {
      method: "POST",
      json: {
        system_prompt: $("#llmTestSystem").value.trim(),
        message: $("#llmTestMessage").value.trim(),
      },
    });
    writeOutput("#llmTestOutput", payload);
    showAlert(`LLM 連線成功（${payload.latency_ms} ms）`, "success");
  } catch (error) {
    writeOutput("#llmTestOutput", errorPayload(error));
  } finally {
    button.disabled = false;
    button.textContent = "測試連線";
  }
}

async function loadAudit(basePath) {
  try {
    const params = new URLSearchParams();
    const limit = $("#auditLimit").value;
    const messageId = $("#auditMessageId").value.trim();
    const documentId = $("#auditDocumentId").value.trim();
    if (basePath !== "/admin/operations/metrics") {
      if (limit) params.set("limit", limit);
      if (messageId && ["/audit/retrieval-logs", "/audit/llm-logs"].includes(basePath)) params.set("message_id", messageId);
      if (documentId && basePath === "/audit/retrieval-logs") params.set("document_id", documentId);
    }
    const path = params.toString() ? `${basePath}?${params}` : basePath;
    writeOutput("#adminOutput", await api(path));
  } catch (error) {
    writeOutput("#adminOutput", errorPayload(error));
  }
}

function renderRaw() {
  $("#raw").innerHTML = `
    <div class="grid two">
      <div class="panel">
        <div class="panel-header">
          <h2>API 呼叫</h2>
          <button class="primary" id="rawSendBtn">送出</button>
        </div>
        <div class="form-grid">
          <label>Method<select id="rawMethod"><option>GET</option><option>POST</option><option>PATCH</option><option>DELETE</option></select></label>
          <label class="wide">Path<input id="rawPath" value="/health" /></label>
          <label class="full">JSON Body<textarea id="rawBody"></textarea></label>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header"><h2>回應</h2></div>
        <div id="rawOutput" class="output"></div>
      </div>
    </div>
  `;
  $("#rawSendBtn").addEventListener("click", rawSend);
}

async function rawSend() {
  try {
    const method = $("#rawMethod").value;
    const bodyText = $("#rawBody").value.trim();
    const options = { method };
    if (bodyText) options.json = JSON.parse(bodyText);
    writeOutput("#rawOutput", await api($("#rawPath").value.trim(), options));
  } catch (error) {
    writeOutput("#rawOutput", errorPayload(error));
  }
}

function table(headers, rows) {
  const head = headers.map((item) => `<th>${escapeHtml(item)}</th>`).join("");
  const body = rows || `<tr><td colspan="${headers.length}" class="muted">沒有資料</td></tr>`;
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
