// Home-Ops Agent — Web UI

let ws = null;
let conversationId = null;
let currentFilter = "";

// --- Navigation ---

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`view-${btn.dataset.view}`).classList.add("active");

    if (btn.dataset.view === "history") loadHistory();
    if (btn.dataset.view === "settings") { loadSettings(); loadPrompts(); }
  });
});

// --- WebSocket Chat ---

function connectWs() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${location.host}/ws/chat`);

  ws.onopen = () => {
    document.querySelector(".dot").classList.add("connected");
    document.getElementById("status-text").textContent = "Connected";
  };

  ws.onclose = () => {
    document.querySelector(".dot").classList.remove("connected");
    document.getElementById("status-text").textContent = "Disconnected";
    setTimeout(connectWs, 3000);
  };

  ws.onerror = () => {
    document.querySelector(".dot").classList.add("error");
    document.getElementById("status-text").textContent = "Error";
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    const messages = document.getElementById("chat-messages");

    // Remove typing indicator
    messages.querySelectorAll(".typing").forEach((el) => el.remove());

    if (data.type === "typing") {
      conversationId = data.conversation_id;
      const el = document.createElement("div");
      el.className = "message typing";
      el.textContent = "Thinking...";
      messages.appendChild(el);
      messages.scrollTop = messages.scrollHeight;
    } else if (data.type === "message") {
      conversationId = data.conversation_id;
      const el = document.createElement("div");
      el.className = "message assistant";
      el.textContent = data.content;

      if (data.tool_calls && data.tool_calls.length > 0) {
        const toolsDiv = document.createElement("div");
        toolsDiv.className = "tool-calls";
        toolsDiv.innerHTML =
          `<strong>Tools used:</strong> ` +
          data.tool_calls.map((tc) => `<span class="tool-call">${tc.tool}</span>`).join(", ");
        el.appendChild(toolsDiv);
      }

      messages.appendChild(el);
      messages.scrollTop = messages.scrollHeight;
      document.getElementById("send-btn").disabled = false;
    } else if (data.type === "error") {
      const el = document.createElement("div");
      el.className = "message error";
      el.textContent = data.message;
      messages.appendChild(el);
      messages.scrollTop = messages.scrollHeight;
      document.getElementById("send-btn").disabled = false;
    }
  };
}

function sendMessage() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  // Show user message
  const messages = document.getElementById("chat-messages");
  const el = document.createElement("div");
  el.className = "message user";
  el.textContent = text;
  messages.appendChild(el);
  messages.scrollTop = messages.scrollHeight;

  // Send to server
  ws.send(JSON.stringify({ message: text, conversation_id: conversationId }));
  input.value = "";
  document.getElementById("send-btn").disabled = true;
}

document.getElementById("send-btn").addEventListener("click", sendMessage);
document.getElementById("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

document.getElementById("new-chat-btn").addEventListener("click", () => {
  conversationId = null;
  document.getElementById("chat-messages").innerHTML = "";
});

// --- History ---

async function loadHistory(filter) {
  if (filter !== undefined) currentFilter = filter;
  const url = currentFilter ? `/api/history?task_type=${currentFilter}` : "/api/history";

  try {
    const resp = await fetch(url);
    const tasks = await resp.json();
    const list = document.getElementById("history-list");
    list.innerHTML = "";

    if (tasks.length === 0) {
      list.innerHTML = '<p style="color: var(--text-secondary); padding: 20px;">No activity yet.</p>';
      return;
    }

    for (const task of tasks) {
      const el = document.createElement("div");
      el.className = "history-item";
      el.innerHTML = `
        <div class="history-item-header">
          <span class="history-item-type type-${task.type}">${task.type.replace("_", " ")}</span>
          <span class="history-item-time">${new Date(task.created_at).toLocaleString()}</span>
        </div>
        <div class="history-item-trigger">${task.trigger}</div>
        ${task.summary ? `<div class="history-item-summary">${task.summary.substring(0, 200)}</div>` : ""}
      `;
      el.addEventListener("click", () => loadTaskDetail(task.id));
      list.appendChild(el);
    }
  } catch (e) {
    console.error("Failed to load history:", e);
  }
}

async function loadTaskDetail(taskId) {
  try {
    const resp = await fetch(`/api/history/${taskId}`);
    const task = await resp.json();
    const detail = document.getElementById("task-detail");
    detail.classList.remove("hidden");

    let messagesHtml = "";
    if (task.messages) {
      for (const msg of task.messages) {
        const text = msg.content?.text || JSON.stringify(msg.content);
        messagesHtml += `
          <div class="message ${msg.role}" style="max-width:100%">
            <strong>${msg.role}:</strong> ${text}
          </div>`;
      }
    }

    detail.innerHTML = `
      <h3>${task.trigger}</h3>
      <p style="color: var(--text-secondary); margin: 8px 0;">
        ${task.type} — ${task.status} — ${new Date(task.created_at).toLocaleString()}
      </p>
      ${task.summary ? `<p style="margin: 12px 0;">${task.summary}</p>` : ""}
      ${messagesHtml}
      <button class="btn" onclick="document.getElementById('task-detail').classList.add('hidden')" style="margin-top:12px">Close</button>
    `;
  } catch (e) {
    console.error("Failed to load task detail:", e);
  }
}

document.querySelectorAll(".filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    loadHistory(btn.dataset.filter);
  });
});

// --- Settings ---

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    const s = await resp.json();

    // PR mode
    document.querySelector(`input[name="pr_mode"][value="${s.pr_mode}"]`).checked = true;

    // Auth method
    document.querySelector(`input[name="auth_method"][value="${s.auth_method}"]`).checked = true;
    toggleAuthSections(s.auth_method);

    // API key status
    const apiKeyStatus = document.getElementById("api-key-status");
    const apiKeyBadge = document.getElementById("api-key-badge");
    const apiKeyHint = document.getElementById("api-key-hint");
    if (s.has_api_key && s.api_key_hint) {
      apiKeyStatus.classList.remove("hidden");
      apiKeyBadge.textContent = "Active";
      apiKeyBadge.className = "status-badge active";
      apiKeyHint.textContent = s.api_key_hint;
    } else {
      apiKeyStatus.classList.remove("hidden");
      apiKeyBadge.textContent = "Not set";
      apiKeyBadge.className = "status-badge inactive";
      apiKeyHint.textContent = "";
    }

    // OAuth status
    const badge = document.getElementById("oauth-status-badge");
    badge.textContent = s.oauth_status;
    badge.style.background =
      s.oauth_status === "active"
        ? "rgba(63,185,80,0.15)"
        : s.oauth_status === "expired"
          ? "rgba(248,81,73,0.15)"
          : "rgba(139,148,158,0.15)";

    if (s.oauth_token_expires) {
      document.getElementById("oauth-expires").textContent = `Expires: ${new Date(s.oauth_token_expires).toLocaleString()}`;
    }

    // Model settings
    if (s.models) {
      for (const [task, model] of Object.entries(s.models)) {
        const el = document.getElementById(`model-${task.replace("_", "-")}`);
        if (el) el.value = model;
      }
    }

    // Other settings
    document.getElementById("alert-cooldown").value = s.alert_cooldown_seconds;
    document.getElementById("ntfy-topics").value = s.ntfy_topics;
    document.getElementById("pr-interval").value = s.pr_check_interval_seconds;
  } catch (e) {
    console.error("Failed to load settings:", e);
  }
}

function toggleAuthSections(method) {
  document.getElementById("auth-api-key-section").classList.toggle("hidden", method !== "api_key");
  document.getElementById("auth-oauth-section").classList.toggle("hidden", method !== "oauth");
}

document.querySelectorAll('input[name="auth_method"]').forEach((radio) => {
  radio.addEventListener("change", (e) => toggleAuthSections(e.target.value));
});

document.getElementById("save-api-key-btn").addEventListener("click", async () => {
  const key = document.getElementById("api-key-input").value.trim();
  if (!key) return;
  await saveSetting("anthropic_api_key", key);
  document.getElementById("api-key-input").value = "";
  showSettingsStatus("API key saved");
  loadSettings(); // Refresh to show masked key
});


document.getElementById("save-settings-btn").addEventListener("click", async () => {
  const prMode = document.querySelector('input[name="pr_mode"]:checked').value;
  const authMethod = document.querySelector('input[name="auth_method"]:checked').value;
  const cooldown = document.getElementById("alert-cooldown").value;
  const topics = document.getElementById("ntfy-topics").value;
  const interval = document.getElementById("pr-interval").value;

  // Collect model settings
  const modelTasks = ["pr_review", "alert_triage", "alert_fix", "code_fix", "chat"];
  const modelSaves = modelTasks.map((task) => {
    const el = document.getElementById(`model-${task.replace("_", "-")}`);
    return el ? saveSetting(`model_${task}`, el.value) : Promise.resolve();
  });

  await Promise.all([
    saveSetting("pr_mode", prMode),
    saveSetting("auth_method", authMethod),
    saveSetting("alert_cooldown_seconds", cooldown),
    saveSetting("ntfy_topics", topics),
    saveSetting("pr_check_interval_seconds", interval),
    ...modelSaves,
  ]);
  showSettingsStatus("Settings saved");
});

async function saveSetting(key, value) {
  try {
    await fetch(`/api/settings/${key}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
  } catch (e) {
    console.error(`Failed to save setting ${key}:`, e);
  }
}

function showSettingsStatus(msg) {
  const el = document.getElementById("settings-status");
  el.textContent = msg;
  el.style.color = "var(--green)";
  setTimeout(() => (el.textContent = ""), 3000);
}

// --- Prompts ---

const PROMPT_LABELS = {
  cluster_context: "Cluster Context (shared by all agents)",
  pr_review: "PR Review Agent",
  alert_response: "Alert Response Agent",
  chat: "Chat Agent",
};

async function loadPrompts() {
  try {
    const resp = await fetch("/api/prompts");
    const prompts = await resp.json();
    const container = document.getElementById("prompt-editors");
    container.innerHTML = "";

    for (const [name, info] of Object.entries(prompts)) {
      const card = document.createElement("div");
      card.className = "prompt-card";
      card.innerHTML = `
        <div class="prompt-card-header">
          <span class="prompt-card-name">${PROMPT_LABELS[name] || name}</span>
          <div class="prompt-card-actions">
            ${info.is_customized ? '<span class="prompt-customized">Customized</span>' : ""}
            <button class="btn" onclick="savePrompt('${name}')">Save</button>
            ${info.is_customized ? `<button class="btn" onclick="resetPrompt('${name}')">Reset</button>` : ""}
          </div>
        </div>
        <textarea class="prompt-textarea" id="prompt-${name}">${info.custom || info.default}</textarea>
      `;
      container.appendChild(card);
    }
  } catch (e) {
    console.error("Failed to load prompts:", e);
  }
}

async function savePrompt(name) {
  const textarea = document.getElementById(`prompt-${name}`);
  if (!textarea) return;
  await saveSetting(`prompt_${name}`, textarea.value);
  showSettingsStatus(`Prompt "${PROMPT_LABELS[name] || name}" saved`);
  loadPrompts();
}

async function resetPrompt(name) {
  try {
    await fetch(`/api/prompts/${name}`, { method: "DELETE" });
    showSettingsStatus(`Prompt "${PROMPT_LABELS[name] || name}" reset to default`);
    loadPrompts();
  } catch (e) {
    console.error("Failed to reset prompt:", e);
  }
}

// --- Init ---

connectWs();

// Check for OAuth callback
if (location.search.includes("auth=success")) {
  // Switch to settings view to show success
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.querySelector('[data-view="settings"]').classList.add("active");
  document.getElementById("view-settings").classList.add("active");
  loadSettings();
  history.replaceState(null, "", "/");
}

// Load initial status
fetch("/api/status")
  .then((r) => r.json())
  .then((status) => {
    if (status.has_credentials) {
      document.querySelector(".dot").classList.add("connected");
      document.getElementById("status-text").textContent = "Ready";
    }
  })
  .catch(() => {});
