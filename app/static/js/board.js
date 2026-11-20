// Agentic Board Client Logic
let currentOverview = null;
let currentAgents = {};
let activeAgentFilter = "all";
let activeSearchQuery = "";
let currentOpenTaskId = null;
let sseSource = null;
let liveAgents = [];
let activityItems = [];
let refreshTimer = null;

const PRIORITY_LABELS = { critical: "Crítica", high: "Alta", medium: "Media", low: "Baja" };

// Filled from /api/agents (config.py is the single source of truth).
const AGENT_COLORS = {};
const AGENT_ICONS = {};

function roleIcon(name) {
  const cls = AGENT_ICONS[name] || (String(name).startsWith("agy") ? "bi-cpu" : "bi-person");
  return `<i class="bi ${cls}" aria-hidden="true"></i>`;
}

// Log messages may start with an emoji (older entries); show an icon instead.
const MESSAGE_ICONS = [
  ["🚀", "bi-play-fill"], ["📚", "bi-journal-text"], ["🔧", "bi-tools"], ["💭", "bi-chat-left-text"],
  ["✅", "bi-check-circle"], ["👀", "bi-eye"], ["⚠️", "bi-exclamation-triangle"], ["♻️", "bi-arrow-repeat"],
  ["✉️", "bi-envelope"], ["↩️", "bi-arrow-return-left"], ["🛰️", "bi-cpu"], ["⏱️", "bi-stopwatch"]
];
const EMOJI_RE = /[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu;

function stripEmoji(text) {
  return String(text || "").replace(EMOJI_RE, "").replace(/\s{2,}/g, " ").trim();
}

function decorateMessage(text) {
  const raw = String(text || "");
  const match = MESSAGE_ICONS.find(([emoji]) => raw.trimStart().startsWith(emoji));
  const icon = match ? `<i class="bi ${match[1]}" aria-hidden="true"></i>` : "";
  return icon + escapeHtml(stripEmoji(raw));
}

// --- Theme (light by default, persisted per browser) ---
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const icon = document.getElementById("theme-icon");
  if (icon) icon.className = theme === "dark" ? "bi bi-sun" : "bi bi-moon";
  const btn = document.getElementById("btn-theme");
  if (btn) btn.title = theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
  try { localStorage.setItem("board-theme", theme); } catch (e) {}
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(currentTheme());
  document.getElementById("btn-theme").addEventListener("click", () => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  });
  initApp();
  setupEventListeners();
  initRealtime();
});

async function initApp() {
  await fetchAgents();
  await refreshBoard();
  await Promise.all([fetchLiveAgents(), fetchActivity(), refreshKbCount()]);
  // Heartbeats stop arriving when a worker dies: re-read periodically so it turns "offline".
  setInterval(fetchLiveAgents, 15000);
  // Safety net: full resync every minute in case an event was lost.
  setInterval(refreshBoard, 60000);
}

// Many log events arrive while agents stream their work; coalesce board reloads.
function scheduleRefresh() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    refreshBoard();
  }, 500);
}

async function fetchLiveAgents() {
  try {
    const res = await fetch("/api/agents/live");
    if (res.ok) {
      liveAgents = await res.json();
      renderLiveAgents();
    }
  } catch (err) {
    console.error("Error fetching live agents:", err);
  }
}

async function fetchActivity() {
  try {
    const [actRes, msgRes] = await Promise.all([fetch("/api/activity?limit=60"), fetch("/api/messages?limit=40")]);
    const acts = actRes.ok ? await actRes.json() : [];
    const msgs = msgRes.ok ? await msgRes.json() : [];
    activityItems = [
      ...acts,
      ...msgs.map(m => ({
        actor: `${m.sender} → ${m.recipient}`, message: m.body, task_id: m.task_id, timestamp: m.created_at,
        level: m.kind === "warning" ? "warning" : "info", is_message: true
      }))
    ].sort((x, y) => y.timestamp - x.timestamp).slice(0, 60);
    renderActivity();
  } catch (err) {
    console.error("Error fetching activity:", err);
  }
}

async function refreshKbCount() {
  try {
    const res = await fetch("/api/knowledge?limit=1000");
    if (res.ok) {
      const items = await res.json();
      document.getElementById("kb-count").textContent = items.length;
    }
  } catch (err) {}
}

// ==========================================================================
// API Calls & Data Fetching
// ==========================================================================
async function fetchAgents() {
  try {
    const res = await fetch("/api/agents");
    if (res.ok) {
      currentAgents = await res.json();
      // Role colors/icons come from the server config (single source of truth).
      Object.entries(currentAgents).forEach(([name, info]) => {
        AGENT_COLORS[name] = info.color;
        AGENT_ICONS[name] = info.icon;
      });
      renderAgentFleet();
    }
  } catch (err) {
    console.error("Error fetching agents:", err);
  }
}

async function refreshBoard() {
  try {
    const res = await fetch("/api/overview");
    if (res.ok) {
      currentOverview = await res.json();
      renderBoard(currentOverview);
    }
  } catch (err) {
    console.error("Error refreshing board:", err);
  }
}

// ==========================================================================
// Real time: WebSocket (primary) with resume, SSE as fallback
// ==========================================================================
let ws = null;
let lastSeq = 0;
let wsRetry = 0;
let wsEverOpened = false;
let wsPingTimer = null;

function setLiveIndicator(state, text) {
  const colors = { live: "var(--success)", connecting: "var(--warning)", down: "var(--danger)" };
  const indicator = document.getElementById("sse-indicator");
  indicator.style.background = colors[state];
  document.getElementById("sse-text").textContent = text;
}

function initRealtime() {
  if (!("WebSocket" in window)) {
    initSSE();
    return;
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  setLiveIndicator("connecting", "Conectando…");
  ws = new WebSocket(`${scheme}://${location.host}/api/ws`);

  ws.onopen = () => {
    wsEverOpened = true;
    wsRetry = 0;
    setLiveIndicator("live", "En vivo · WebSocket");
    if (lastSeq > 0) ws.send(JSON.stringify({ type: "resume", last_seq: lastSeq }));
    clearInterval(wsPingTimer);
    wsPingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, 25000);
  };

  ws.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    if (payload.type === "pong") return;
    if (payload.seq) {
      // Ignore duplicates that can arrive right after a resume.
      if (payload.type !== "init" && payload.seq <= lastSeq) return;
      lastSeq = payload.seq;
    }
    handleLiveEvent(payload);
  };

  ws.onclose = () => {
    clearInterval(wsPingTimer);
    if (!wsEverOpened && wsRetry >= 3) {
      initSSE();  // WebSocket blocked (proxy?): fall back to SSE
      return;
    }
    const delay = Math.min(15000, 1000 * 2 ** wsRetry++);
    setLiveIndicator("connecting", `Reconectando en ${Math.round(delay / 1000)} s…`);
    setTimeout(initRealtime, delay);
  };
}

function initSSE() {
  if (sseSource) {
    sseSource.close();
  }

  const indicator = document.getElementById("sse-indicator");
  const indicatorText = document.getElementById("sse-text");

  try {
    sseSource = new EventSource("/api/events");

    sseSource.onopen = () => {
      indicator.style.background = "var(--success)";
      indicatorText.textContent = "En vivo · SSE";
    };

    sseSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        handleLiveEvent(payload);
      } catch (e) {
        // Heartbeat or malformed comment
      }
    };

    sseSource.onerror = () => {
      indicator.style.background = "var(--warning)";
      indicatorText.textContent = "Reconectando…";
    };
  } catch (e) {
    console.warn("SSE not available, falling back to polling", e);
    setInterval(refreshBoard, 3000);
  }
}

function handleLiveEvent(payload) {
  const { type, data } = payload;
  
  if (type === "init") {
    currentOverview = data;
    renderBoard(currentOverview);
    return;
  }

  if (type === "agent_updated") {
    const idx = liveAgents.findIndex(a => a.name === data.name);
    const prev = idx >= 0 ? liveAgents[idx] : null;
    const merged = { ...data, current_task_title: lookupTaskTitle(data.current_task) || (prev && prev.current_task_title) };
    if (idx >= 0) liveAgents[idx] = merged; else liveAgents.push(merged);
    renderLiveAgents();
    return;
  }
  if (type === "agent_removed") {
    liveAgents = liveAgents.filter(a => a.name !== data.name);
    renderLiveAgents();
    return;
  }
  if (type === "message_added") {
    activityItems.unshift({
      actor: `${data.sender} → ${data.recipient}`, message: data.body, task_id: data.task_id,
      timestamp: data.created_at, level: data.kind === "warning" ? "warning" : "info", is_message: true, kind: data.kind
    });
    activityItems = activityItems.slice(0, 60);
    renderActivity();
    document.getElementById("ticker-msg").textContent = `${data.sender} → ${data.recipient}: ${stripEmoji(data.body)}`;
    return;
  }
  if (type === "activity_added") {
    activityItems.unshift(data);
    activityItems = activityItems.slice(0, 60);
    renderActivity();
    document.getElementById("ticker-msg").textContent = `${data.actor}: ${stripEmoji(data.message)}`;
    return;
  }
  if (type === "knowledge_added") {
    const counter = document.getElementById("kb-count");
    counter.textContent = Number(counter.textContent || 0) + 1;
    if (document.getElementById("modal-knowledge").style.display === "flex") loadKnowledge();
    if (currentOpenTaskId && data.task_id === currentOpenTaskId) loadTaskKnowledge(currentOpenTaskId);
    return;
  }

  // Update footer activity ticker
  const ticker = document.getElementById("ticker-msg");
  if (type === "task_created") {
    ticker.textContent = `Tarea creada: ${data.id} · ${data.title}`;
  } else if (type === "task_updated") {
    ticker.textContent = `${data.id} actualizada · ${data.status} · ${data.assignee || data.agent}`;
  } else if (type === "log_added") {
    ticker.textContent = `${data.agent} en ${data.task_id}: ${stripEmoji(data.message)}`;
  } else if (type === "task_deleted") {
    ticker.textContent = `Tarea eliminada: ${data.task_id}`;
  } else if (type === "board_reset") {
    ticker.textContent = `Tablero vaciado`;
  }

  applyEventToBoard(type, data);

  // If detail modal is open for this task, re-render modal
  if (currentOpenTaskId && (data?.id === currentOpenTaskId || data?.task_id === currentOpenTaskId)) {
    openTaskDetailModal(currentOpenTaskId, false);
  }
}

// Apply a real-time event to the cached overview instead of re-downloading the board.
function applyEventToBoard(type, data) {
  if (!currentOverview) {
    scheduleRefresh();
    return;
  }
  const all = Object.values(currentOverview.tasks_by_column).flat();
  if (type === "task_created" || type === "task_updated") {
    const idx = all.findIndex(t => t.id === data.id);
    if (idx >= 0) all[idx] = data; else all.push(data);
  } else if (type === "task_deleted") {
    const idx = all.findIndex(t => t.id === data.task_id);
    if (idx >= 0) all.splice(idx, 1);
  } else if (type === "log_added") {
    const task = all.find(t => t.id === data.task_id);
    if (task) task.logs = [...(task.logs || []), data].slice(-5);
  } else {
    scheduleRefresh();  // board_reset and anything unknown: reload from the server
    return;
  }
  rebuildOverview(all);
}

function rebuildOverview(tasks) {
  const status = Object.fromEntries(tasks.map(t => [t.id, t.status]));
  const cols = { todo: [], in_progress: [], review: [], done: [] };
  const prio = { critical: 1, high: 2, medium: 3, low: 4 };
  tasks
    .map(t => ({ ...t, is_blocked: (t.dependencies || []).some(d => status[d] !== "done") }))
    .sort((a, b) => (prio[a.priority] || 5) - (prio[b.priority] || 5) || a.created_at - b.created_at)
    .forEach(t => { if (cols[t.status]) cols[t.status].push(t); });
  const total = tasks.length;
  const done = cols.done.length;
  currentOverview = {
    ...currentOverview,
    tasks_by_column: cols,
    total_tasks: total,
    todo_count: cols.todo.length,
    in_progress_count: cols.in_progress.length,
    review_count: cols.review.length,
    done_count: done,
    progress_percentage: total ? Math.round((done / total) * 1000) / 10 : 100,
    is_completed: total > 0 && done === total,
    active_agents: [...new Set(cols.in_progress.map(t => t.assignee || t.agent))]
  };
  renderBoard(currentOverview);
}

// ==========================================================================
// Rendering Logic
// ==========================================================================
function renderBoard(overview) {
  if (!overview) return;

  // 1. Update Metrics
  document.getElementById("stat-total").textContent = overview.total_tasks;
  document.getElementById("stat-todo").textContent = overview.todo_count;
  document.getElementById("stat-prog").textContent = overview.in_progress_count;
  document.getElementById("stat-rev").textContent = overview.review_count;
  document.getElementById("stat-done").textContent = overview.done_count;

  // 2. Update Progress Bar
  const pct = Math.round(overview.progress_percentage || 0);
  document.getElementById("global-pct").textContent = `${pct}%`;
  document.getElementById("global-progress-bar").style.width = `${pct}%`;

  // 3. Update Column Badges
  document.getElementById("badge-todo").textContent = overview.todo_count;
  document.getElementById("badge-in_progress").textContent = overview.in_progress_count;
  document.getElementById("badge-review").textContent = overview.review_count;
  document.getElementById("badge-done").textContent = overview.done_count;

  // 4. Render Cards in Columns
  const cols = ["todo", "in_progress", "review", "done"];
  cols.forEach(col => {
    const container = document.getElementById(`cards-${col}`);
    container.innerHTML = "";
    const tasks = overview.tasks_by_column[col] || [];

    // Filter by agent and search query
    const filtered = tasks.filter(t => {
      const f = activeAgentFilter.toLowerCase();
      const matchAgent = (activeAgentFilter === "all") || t.agent.toLowerCase() === f
        || (t.assignee || "").toLowerCase() === f;
      const query = activeSearchQuery.toLowerCase().trim();
      const matchQuery = !query || 
        t.title.toLowerCase().includes(query) || 
        t.id.toLowerCase().includes(query) ||
        (t.tags && t.tags.some(tag => tag.toLowerCase().includes(query)));
      return matchAgent && matchQuery;
    });

    if (filtered.length === 0) {
      container.innerHTML = `<div class="column-empty">Sin tareas</div>`;
      return;
    }

    filtered.forEach(task => {
      container.appendChild(createCardElement(task));
    });
  });

  // Update Fleet Active indicators
  renderAgentFleet();
}

function renderAgentFleet() {
  // Roles and live instances are shown in the agent dropdown (toolbar) and the fleet panel.
}

function createCardElement(task) {
  const card = document.createElement("div");
  card.className = `task-card priority-${task.priority}`;
  card.draggable = true;
  card.id = `card-${task.id}`;

  // Drag and Drop events
  card.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", task.id);
    card.classList.add("is-dragging");
  });

  card.addEventListener("dragend", () => {
    card.classList.remove("is-dragging");
  });

  // Dependencies tag
  let depHtml = "";
  if (task.dependencies && task.dependencies.length > 0) {
    if (task.is_blocked) {
      depHtml = `<div class="card-dep-tag" title="Esperando: ${task.dependencies.join(', ')}"><i class="bi bi-lock"></i> Esperando ${task.dependencies.length} dependencia(s)</div>`;
    } else {
      depHtml = `<div class="card-dep-tag dep-ok" title="Dependencias satisfechas"><i class="bi bi-unlock"></i> Dependencias listas</div>`;
    }
  }

  // Latest log message
  let tickerHtml = "";
  if (task.logs && task.logs.length > 0) {
    const lastLog = task.logs[task.logs.length - 1];
    tickerHtml = `
      <div class="card-activity-ticker" title="${escapeHtml(stripEmoji(lastLog.message))}">
        <span>${roleIcon(lastLog.agent)}</span>
        <span>${decorateMessage(lastLog.message)}</span>
      </div>
    `;
  }

  const agentAvatar = roleIcon(task.agent);
  const assigneeHtml = task.assignee ? `<span class="card-assignee" title="Instancia que ejecuta la tarea"><i class="bi bi-cpu"></i> ${escapeHtml(task.assignee)}</span>` : "";
  const kbHtml = task.knowledge_count ? `<span class="card-kb-badge" title="Entradas en la base de conocimiento"><i class="bi bi-journal-text"></i> ${task.knowledge_count}</span>` : "";
  const agentColor = AGENT_COLORS[task.agent] || "var(--text-muted)";

  card.innerHTML = `
    <div class="card-top">
      <span class="card-id">${task.id}</span>
      <span class="card-priority prio-${task.priority}">${PRIORITY_LABELS[task.priority] || escapeHtml(task.priority)}</span>
    </div>

    <div class="card-title">${escapeHtml(task.title)}</div>
    <div class="card-desc">${escapeHtml(task.description)}</div>

    ${depHtml}
    ${tickerHtml}

    <div class="card-footer">
      <div class="card-agent-badge" style="color: ${agentColor};">
        <span>${agentAvatar}</span>
        <span>@${escapeHtml(task.agent)}</span>
        ${kbHtml}
      </div>
      ${assigneeHtml}

      <div class="card-actions-hover">
        <button class="btn-card-action" onclick="openTaskDetailModal('${task.id}')" title="Ver detalles" aria-label="Ver detalles"><i class="bi bi-eye"></i></button>
        ${task.status !== 'done' ? `<button class="btn-card-action" onclick="quickCompleteTask('${task.id}', '${task.agent}')" title="Marcar como hecha" aria-label="Marcar como hecha"><i class="bi bi-check2"></i></button>` : ''}
      </div>
    </div>
  `;

  // Click card to open modal
  card.addEventListener("click", (e) => {
    if (!e.target.closest(".btn-card-action")) {
      openTaskDetailModal(task.id);
    }
  });

  return card;
}

// ==========================================================================
// Drag and Drop Logic
// ==========================================================================
window.allowDrop = function(e) {
  e.preventDefault();
  const col = e.currentTarget.closest(".kanban-column");
  if (col) col.classList.add("drag-over");
};

document.querySelectorAll(".kanban-column").forEach(col => {
  col.addEventListener("dragleave", () => col.classList.remove("drag-over"));
});

window.handleDrop = async function(e, newStatus) {
  e.preventDefault();
  const col = e.currentTarget.closest(".kanban-column");
  if (col) col.classList.remove("drag-over");

  const taskId = e.dataTransfer.getData("text/plain");
  if (!taskId) return;

  try {
    const res = await fetch(`/api/tasks/${taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus })
    });
    if (res.ok) {
      // Immediate optimistic update
      refreshBoard();
    }
  } catch (err) {
    console.error("Error moving task:", err);
  }
};

// ==========================================================================
// Modals & User Actions
// ==========================================================================
function setupEventListeners() {
  // Agent filter change
  document.getElementById("agent-filter").addEventListener("change", (e) => {
    activeAgentFilter = e.target.value;
    renderBoard(currentOverview);
  });

  // Search input
  document.getElementById("search-input").addEventListener("input", (e) => {
    activeSearchQuery = e.target.value;
    renderBoard(currentOverview);
  });

  // Buttons
  document.getElementById("btn-new-task").addEventListener("click", () => {
    openModal("modal-new-task");
  });

  document.getElementById("btn-templates").addEventListener("click", () => {
    openModal("modal-templates");
  });

  document.getElementById("btn-reset").addEventListener("click", async () => {
    if (confirm("¿Vaciar el tablero? Se borran tareas y logs; la base de conocimiento se conserva.")) {
      await fetch("/api/board/reset?reseed=false", { method: "POST" });
      refreshBoard();
    }
  });

  document.getElementById("btn-knowledge").addEventListener("click", () => {
    openModal("modal-knowledge");
    loadKnowledge();
  });
  let kbTimer = null;
  document.getElementById("kb-search").addEventListener("input", () => {
    clearTimeout(kbTimer);
    kbTimer = setTimeout(loadKnowledge, 250);
  });
  document.getElementById("kb-kind").addEventListener("change", loadKnowledge);
  document.getElementById("kb-project").addEventListener("change", loadKnowledge);

  document.getElementById("btn-simulate").addEventListener("click", simulateAgentWorker);

  // Column quick add buttons
  document.querySelectorAll(".col-add-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      openModal("modal-new-task");
    });
  });

  // Modal Props Save
  document.getElementById("modal-btn-save-props").addEventListener("click", saveModalTaskProperties);
  document.getElementById("modal-btn-mark-done").addEventListener("click", () => {
    if (currentOpenTaskId) {
      const agent = document.getElementById("modal-edit-agent").value;
      quickCompleteTask(currentOpenTaskId, agent);
      closeModal("modal-task-detail");
    }
  });

  document.getElementById("modal-btn-delete-task").addEventListener("click", async () => {
    if (currentOpenTaskId && confirm(`¿Eliminar definitivamente la tarea ${currentOpenTaskId}?`)) {
      await fetch(`/api/tasks/${currentOpenTaskId}`, { method: "DELETE" });
      closeModal("modal-task-detail");
      refreshBoard();
    }
  });
}

window.openModal = function(id) {
  document.getElementById(id).style.display = "flex";
};

window.closeModal = function(id) {
  document.getElementById(id).style.display = "none";
  if (id === "modal-task-detail") {
    currentOpenTaskId = null;
  }
};

// ==========================================================================
// Task Detail Modal
// ==========================================================================
async function openTaskDetailModal(taskId, shouldShow = true) {
  currentOpenTaskId = taskId;
  try {
    const res = await fetch(`/api/tasks/${taskId}`);
    if (!res.ok) return;
    const task = await res.json();

    document.getElementById("modal-task-id").textContent = task.id;
    document.getElementById("modal-task-title").textContent = task.title;
    document.getElementById("modal-task-desc").textContent = task.description;

    document.getElementById("modal-edit-status").value = task.status;
    document.getElementById("modal-edit-agent").value = task.agent;
    document.getElementById("modal-edit-priority").value = task.priority;
    document.getElementById("modal-task-assignee").textContent = task.assignee || "Sin reclamar";
    document.getElementById("modal-task-project").textContent = task.project || "-";
    document.getElementById("modal-task-workdir").textContent = task.workdir || "-";
    loadTaskKnowledge(task.id);

    // Timestamps
    document.getElementById("modal-time-created").textContent = task.created_at ? new Date(task.created_at * 1000).toLocaleTimeString() : "-";
    document.getElementById("modal-time-started").textContent = task.started_at ? new Date(task.started_at * 1000).toLocaleTimeString() : "-";
    document.getElementById("modal-time-completed").textContent = task.completed_at ? new Date(task.completed_at * 1000).toLocaleTimeString() : "-";

    // Dependencies
    const depsBox = document.getElementById("modal-task-deps");
    depsBox.innerHTML = "";
    if (task.dependencies && task.dependencies.length > 0) {
      task.dependencies.forEach(depId => {
        const depStatus = task.dependencies_status ? task.dependencies_status[depId] : "unknown";
        const isDone = depStatus === "done";
        const chip = document.createElement("span");
        chip.className = `card-dep-tag ${isDone ? "dep-ok" : ""}`;
        chip.style.marginRight = "0.4rem";
        chip.innerHTML = `<i class="bi ${isDone ? "bi-check-circle" : "bi-hourglass-split"}"></i> ${escapeHtml(depId)} (${escapeHtml(depStatus)})`;
        depsBox.appendChild(chip);
      });
    } else {
      depsBox.innerHTML = `<span class="text-muted" style="font-size: 0.78rem;">Ninguna (Tarea raíz sin bloqueos)</span>`;
    }

    // Terminal Logs
    const logBox = document.getElementById("modal-task-logs");
    logBox.innerHTML = "";
    if (task.logs && task.logs.length > 0) {
      task.logs.forEach(log => {
        const timeStr = new Date(log.timestamp * 1000).toLocaleTimeString();
        const line = document.createElement("div");
        line.className = "term-line";
        line.innerHTML = `
          <span class="term-time">[${timeStr}]</span>
          <span class="term-agent">@${log.agent}:</span>
          <span class="term-msg term-${log.level || 'info'}">${decorateMessage(log.message)}</span>
        `;
        logBox.appendChild(line);
      });
      // Scroll to bottom
      logBox.scrollTop = logBox.scrollHeight;
    } else {
      logBox.innerHTML = `<div class="text-muted">Esperando actividad de ejecución...</div>`;
    }

    // Artifacts Box
    const artBox = document.getElementById("modal-task-artifacts");
    if (task.artifacts && Object.keys(task.artifacts).length > 0) {
      artBox.innerHTML = `<pre style="font-family: var(--font-mono); font-size: 0.72rem; color: #a5b4fc; overflow-x: auto;">${JSON.stringify(task.artifacts, null, 2)}</pre>`;
    } else {
      artBox.innerHTML = `<span class="text-muted" style="font-size: 0.78rem;">Ningún artefacto subido aún por el agente.</span>`;
    }

    if (shouldShow) {
      openModal("modal-task-detail");
    }
  } catch (err) {
    console.error("Error opening task detail:", err);
  }
}

async function saveModalTaskProperties() {
  if (!currentOpenTaskId) return;
  const updates = {
    status: document.getElementById("modal-edit-status").value,
    agent: document.getElementById("modal-edit-agent").value,
    priority: document.getElementById("modal-edit-priority").value
  };

  try {
    const res = await fetch(`/api/tasks/${currentOpenTaskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates)
    });
    if (res.ok) {
      refreshBoard();
      openTaskDetailModal(currentOpenTaskId, false);
    }
  } catch (err) {
    console.error("Error updating task properties:", err);
  }
}

window.addManualLogPrompt = async function() {
  if (!currentOpenTaskId) return;
  const msg = prompt("Mensaje de log del Operador:");
  if (msg && msg.trim()) {
    await fetch(`/api/tasks/${currentOpenTaskId}/log`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent: "Operator",
        message: msg.trim()
      })
    });
  }
};

window.quickCompleteTask = async function(taskId, agent) {
  try {
    await fetch(`/api/tasks/${taskId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent: agent || "Operator",
        summary: "Tarea completada satisfactoriamente por el operador o agente.",
        status: "done"
      })
    });
    refreshBoard();
  } catch (err) {
    console.error("Error completing task:", err);
  }
};

// ==========================================================================
// Create Task Form Handler
// ==========================================================================
window.handleCreateTask = async function(e) {
  e.preventDefault();
  const title = document.getElementById("new-task-title").value.trim();
  const desc = document.getElementById("new-task-desc").value.trim();
  const agent = document.getElementById("new-task-agent").value;
  const priority = document.getElementById("new-task-priority").value;
  const depsRaw = document.getElementById("new-task-deps").value.trim();
  const tagsRaw = document.getElementById("new-task-tags").value.trim();

  const dependencies = depsRaw ? depsRaw.split(",").map(d => d.trim()).filter(Boolean) : [];
  const tags = tagsRaw ? tagsRaw.split(",").map(t => t.trim()).filter(Boolean) : [];

  try {
    const res = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        description: desc,
        agent,
        priority,
        dependencies,
        tags
      })
    });

    if (res.ok) {
      document.getElementById("form-create-task").reset();
      closeModal("modal-new-task");
      refreshBoard();
    }
  } catch (err) {
    console.error("Error creating task:", err);
  }
};

// ==========================================================================
// Templates Injection
// ==========================================================================
window.applyTemplate = async function(templateKey) {
  let plan = null;
  if (templateKey === "fullstack") {
    plan = {
      project_name: "Desarrollo App Fullstack Web",
      tasks: [
        {
          title: "Diseño de Arquitectura y Base de Datos",
          description: "Definir modelos de datos, contratos de API OpenAPI y seguridad.",
          agent: "Backend",
          priority: "critical",
          dependencies: []
        },
        {
          title: "Implementación de Backend y Modelos",
          description: "Desarrollar la API REST en FastAPI con validación Pydantic.",
          agent: "Backend",
          priority: "high",
          dependencies: []
        },
        {
          title: "Construcción de Interfaz Web Responsiva",
          description: "Crear componentes UI accesibles y modernos con conexión a la API.",
          agent: "Backend",
          priority: "high",
          dependencies: []
        },
        {
          title: "Batería de Pruebas Unitarias y E2E",
          description: "Escribir tests automáticos de integración y verificar tiempos de respuesta.",
          agent: "QA",
          priority: "medium",
          dependencies: []
        },
        {
          title: "Auditoría de Seguridad y Code Review",
          description: "Revisar vulnerabilidades OWASP y optimizaciones de rendimiento.",
          agent: "Security",
          priority: "critical",
          dependencies: []
        }
      ]
    };
  } else if (templateKey === "bugfix") {
    plan = {
      project_name: "Hotfix y Mitigación de Bug",
      tasks: [
        {
          title: "Reproducción del Bug y Creación de Test Fallido",
          description: "Aislar la condición de carrera y documentar el test unitario que falla.",
          agent: "QA",
          priority: "critical",
          dependencies: []
        },
        {
          title: "Aplicación de Patch de Corrección",
          description: "Modificar la lógica de negocio para resolver el issue sin efectos colaterales.",
          agent: "Backend",
          priority: "critical",
          dependencies: []
        },
        {
          title: "Verificación de No-Regresión",
          description: "Validar que toda la suite de tests pasa limpiamente.",
          agent: "Security",
          priority: "high",
          dependencies: []
        }
      ]
    };
  } else if (templateKey === "devops") {
    plan = {
      project_name: "Despliegue y CI/CD en Producción",
      tasks: [
        {
          title: "Configuración de Dockerfile y Multi-stage Build",
          description: "Generar imagen mínima optimizada de producción.",
          agent: "Backend",
          priority: "high",
          dependencies: []
        },
        {
          title: "Pipeline de Integración Continua (CI/CD)",
          description: "Automatizar linters, tests y escaneo de vulnerabilidades.",
          agent: "Backend",
          priority: "high",
          dependencies: []
        },
        {
          title: "Smoke Tests en Staging",
          description: "Lanzar peticiones de verificación y validar métricas de salud.",
          agent: "QA",
          priority: "medium",
          dependencies: []
        }
      ]
    };
  }

  if (plan) {
    try {
      const res = await fetch("/api/tasks/batch-plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(plan)
      });
      if (res.ok) {
        closeModal("modal-templates");
        refreshBoard();
      }
    } catch (err) {
      console.error("Error applying template:", err);
    }
  }
};

// ==========================================================================
// Autonomous Agent Simulation (Demonstration for Operator)
// ==========================================================================
async function simulateAgentWorker() {
  const btn = document.getElementById("btn-simulate");
  btn.disabled = true;
  btn.innerHTML = `<i class="bi bi-hourglass-split"></i><span class="btn-label">Reclamando…</span>`;

  const availableAgents = ["Backend", "Frontend", "UX", "QA", "Supervisor", "Security", "BusinessLogic"];
  let claimedTask = null;
  let chosenAgent = null;

  for (const agent of availableAgents) {
    try {
      const res = await fetch(`/api/tasks/claim-next?agent_name=${agent}`, { method: "POST" });
      const data = await res.json();
      if (data.claimed && data.task) {
        claimedTask = data.task;
        chosenAgent = agent;
        break;
      }
    } catch (e) {}
  }

  if (!claimedTask) {
    alert("No hay tareas pendientes ('todo') listas para ser reclamadas por agentes en este momento.");
    btn.disabled = false;
    btn.innerHTML = `<i class="bi bi-play-circle"></i><span class="btn-label">Simular</span>`;
    return;
  }

  btn.innerHTML = `<i class="bi bi-gear"></i><span class="btn-label">${chosenAgent} trabajando…</span>`;

  // Step 1: Intermediate log
  await new Promise(r => setTimeout(r, 1200));
  await fetch(`/api/tasks/${claimedTask.id}/log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      agent: chosenAgent,
      message: `Inspeccionando contexto y ejecutando pasos de: '${claimedTask.title}'...`
    })
  });

  // Step 2: Intermediate log
  await new Promise(r => setTimeout(r, 1500));
  await fetch(`/api/tasks/${claimedTask.id}/log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      agent: chosenAgent,
      message: `Generación de código/análisis finalizada con éxito. Preparando entregable...`
    })
  });

  // Step 3: Complete task
  await new Promise(r => setTimeout(r, 1200));
  await fetch(`/api/tasks/${claimedTask.id}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      agent: chosenAgent,
      summary: `Ejecución finalizada satisfactoriamente. Todos los requisitos implementados.`,
      artifacts: {
        agent: chosenAgent,
        status: "success",
        timestamp: new Date().toISOString()
      },
      status: "done"
    })
  });

  btn.disabled = false;
  btn.innerHTML = `<i class="bi bi-play-circle"></i><span class="btn-label">Simular</span>`;
  refreshBoard();
}

// ==========================================================================
// Live agents, orchestrator feed and knowledge base
// ==========================================================================
const STATE_LABELS = {
  working: "Trabajando",
  waiting: "Esperando dependencias",
  idle: "Libre",
  offline: "Sin señal",
  stopped: "Detenido",
  finished: "Terminó (tablero completo)",
  error: "Error"
};

function lookupTaskTitle(taskId) {
  if (!taskId || !currentOverview) return null;
  for (const col of Object.values(currentOverview.tasks_by_column || {})) {
    const t = col.find(x => x.id === taskId);
    if (t) return t.title;
  }
  return null;
}

function timeAgo(ts) {
  if (!ts) return "-";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `hace ${s}s`;
  if (s < 3600) return `hace ${Math.round(s / 60)}m`;
  return `hace ${Math.round(s / 3600)}h`;
}

function renderInstanceOptions() {
  const group = document.getElementById("agent-filter-instances");
  if (!group) return;
  group.innerHTML = liveAgents
    .map(a => `<option value="${escapeHtml(a.name)}">${escapeHtml(a.name)} · ${STATE_LABELS[a.status] || escapeHtml(a.status)}${a.current_task ? " · " + escapeHtml(a.current_task) : ""}</option>`)
    .join("");
  document.getElementById("agent-filter").value = activeAgentFilter;
}

function renderLiveAgents() {
  renderInstanceOptions();
  const box = document.getElementById("live-agents");
  const summary = document.getElementById("fleet-summary");
  if (!liveAgents.length) {
    box.innerHTML = `<div class="live-empty">Ningún agente conectado. El orquestador lanza la flota con <code>agents/fleet.py start</code>.</div>`;
    summary.textContent = "sin agentes conectados";
    return;
  }
  const counts = {};
  liveAgents.forEach(a => { counts[a.status] = (counts[a.status] || 0) + 1; });
  summary.textContent = Object.entries(counts).map(([k, v]) => `${v} ${STATE_LABELS[k] || k}`).join(" · ");

  box.innerHTML = "";
  liveAgents.forEach(a => {
    const el = document.createElement("div");
    el.className = `live-agent state-${a.status}`;
    const icon = `<i class="bi ${a.kind === "orchestrator" ? "bi-diagram-3" : "bi-cpu"}"></i>`;
    const taskHtml = a.current_task
      ? `<div class="live-agent-task" onclick="openTaskDetailModal('${escapeHtml(a.current_task)}')" title="${escapeHtml(a.current_task_title || "")}"><span class="card-id">${escapeHtml(a.current_task)}</span>${escapeHtml(a.current_task_title || lookupTaskTitle(a.current_task) || "")}</div>`
      : `<div class="live-agent-task text-muted">Sin tarea asignada</div>`;
    el.innerHTML = `
      <div class="live-agent-top">
        <div class="live-agent-name"><span class="state-dot"></span>${icon} ${escapeHtml(a.name)}</div>
        <span class="state-label">${STATE_LABELS[a.status] || escapeHtml(a.status)}</span>
      </div>
      ${taskHtml}
      <div class="live-agent-msg" title="${escapeHtml(stripEmoji(a.last_message))}">${decorateMessage(a.last_message || "…")}</div>
      <div class="live-agent-meta">
        <span>${escapeHtml(a.model || a.kind)}</span>
        <span><i class="bi bi-check2"></i> ${a.tasks_done || 0} · ${timeAgo(a.last_heartbeat)}</span>
      </div>`;
    box.appendChild(el);
  });
}

function renderActivity() {
  const box = document.getElementById("activity-feed");
  if (!activityItems.length) {
    box.innerHTML = `<div class="live-empty">Sin actividad todavía.</div>`;
    return;
  }
  box.innerHTML = activityItems.map(item => `
    <div class="activity-item lvl-${escapeHtml(item.level)}${item.is_message ? " is-message" : ""}">
      <div class="activity-meta"><i class="bi ${item.is_message ? "bi-envelope" : "bi-diagram-3"}"></i>${new Date(item.timestamp * 1000).toLocaleTimeString()} · ${escapeHtml(item.actor)}${item.task_id ? " · " + escapeHtml(item.task_id) : ""}</div>
      <div>${decorateMessage(item.message)}</div>
    </div>`).join("");
}

const KB_KINDS = {
  summary: { label: "Resumen", icon: "bi-card-text" },
  conclusion: { label: "Conclusión", icon: "bi-lightbulb" },
  decision: { label: "Decisión", icon: "bi-signpost-split" },
  gotcha: { label: "Problema conocido", icon: "bi-exclamation-octagon" },
  memory: { label: "Memoria", icon: "bi-bookmark" },
  note: { label: "Nota", icon: "bi-sticky" }
};

function renderKnowledgeEntries(entries, emptyText) {
  if (!entries.length) return `<span class="text-muted" style="font-size: 0.78rem;">${emptyText}</span>`;
  return entries.map(k => `
    <div class="kb-entry">
      <div class="kb-entry-head">
        <span class="kb-kind kind-${escapeHtml(k.kind)}"><i class="bi ${KB_KINDS[k.kind]?.icon || "bi-sticky"}"></i> ${KB_KINDS[k.kind]?.label || escapeHtml(k.kind)}</span>
        <span class="kb-entry-title">${escapeHtml(k.title)}</span>
        <span class="kb-entry-meta">${escapeHtml(k.agent)}${k.task_id ? " · " + escapeHtml(k.task_id) : ""}${k.project ? " · " + escapeHtml(k.project) : ""} · ${new Date(k.created_at * 1000).toLocaleString()}</span>
      </div>
      <div class="kb-entry-body">${escapeHtml(k.content)}</div>
    </div>`).join("");
}

async function loadTaskKnowledge(taskId) {
  const box = document.getElementById("modal-task-knowledge");
  try {
    const res = await fetch(`/api/knowledge?task_id=${encodeURIComponent(taskId)}`);
    const entries = res.ok ? await res.json() : [];
    box.innerHTML = renderKnowledgeEntries(entries, "Aún no hay conocimiento registrado para esta tarea.");
  } catch (err) {
    console.error("Error loading task knowledge:", err);
  }
}

async function loadKnowledge() {
  const params = new URLSearchParams();
  const q = document.getElementById("kb-search").value.trim();
  const kind = document.getElementById("kb-kind").value;
  const project = document.getElementById("kb-project").value;
  if (q) params.set("q", q);
  if (kind) params.set("kind", kind);
  if (project) params.set("project", project);
  params.set("limit", "200");
  try {
    const res = await fetch(`/api/knowledge?${params}`);
    const entries = res.ok ? await res.json() : [];
    document.getElementById("kb-results").innerHTML = renderKnowledgeEntries(entries, "Sin resultados.");

    // Keep the project filter in sync with what exists in the KB.
    const select = document.getElementById("kb-project");
    if (!project) {
      const all = await (await fetch("/api/knowledge?limit=1000")).json();
      const projects = [...new Set(all.map(k => k.project).filter(Boolean))].sort();
      select.innerHTML = `<option value="">Todos los proyectos</option>` +
        projects.map(p => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join("");
    }
  } catch (err) {
    console.error("Error loading knowledge:", err);
  }
}

window.openTaskDetailModal = openTaskDetailModal;

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
