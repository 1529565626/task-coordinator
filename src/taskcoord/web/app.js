const state = {
  csrf: null,
  tasks: [],
  projects: [],
  agents: [],
  selected: null,
  statusFilter: "",
};

const STATUS_LABELS = {
  pending_confirmation: "待确认",
  ready: "可认领",
  claimed: "已认领",
  review: "待验收",
  blocked: "阻塞",
  done: "已完成",
  cancelled: "已取消",
};

const boardOrder = ["pending_confirmation", "ready", "claimed", "review", "blocked", "done"];

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (state.csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  if (options.method && options.method !== "GET" && !headers["Idempotency-Key"]) {
    headers["Idempotency-Key"] = crypto.randomUUID();
  }
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || "请求失败");
    error.payload = payload;
    throw error;
  }
  return payload;
}

function toast(message, isError = false) {
  const node = document.querySelector("#toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => node.classList.remove("show"), 2800);
}

function filters() {
  const params = new URLSearchParams();
  const project = document.querySelector("#filter-project").value;
  const owner = document.querySelector("#filter-owner").value.trim();
  const scope = document.querySelector("#filter-scope").value.trim();
  const query = document.querySelector("#filter-query").value.trim();
  if (project) params.set("project", project);
  if (state.statusFilter) params.set("status", state.statusFilter);
  if (owner) params.set("owner", owner);
  if (scope) params.set("scope", scope);
  if (query) params.set("q", query);
  return params;
}

function renderStatusChips() {
  const host = document.querySelector("#status-chips");
  const items = [["", "全部"], ...Object.entries(STATUS_LABELS)];
  host.innerHTML = items.map(([value, label]) => {
    const active = state.statusFilter === value ? " is-active" : "";
    return `<button type="button" class="filter-chip${active}" data-status="${escapeHtml(value)}">${escapeHtml(label)}</button>`;
  }).join("");
  host.querySelectorAll("[data-status]").forEach((button) => {
    button.addEventListener("click", () => {
      state.statusFilter = button.dataset.status;
      renderStatusChips();
      refresh().catch((error) => toast(error.message, true));
    });
  });
}

async function refresh() {
  const health = await api("/api/v1/health/ready").catch(async () => api("/api/v1/health/live"));
  const version = await api("/api/v1/version");
  const status = document.querySelector("#service-status");
  status.textContent = health.status === "ready" ? `就绪 · v${version.version}` : health.status;
  status.className = `status-pill${health.status === "ready" ? "" : " error"}`;

  try {
    const me = await api("/api/v1/auth/me");
    state.csrf = me.csrf_token;
    setAuthed(true, me.username);
  } catch {
    if (!state.csrf) setAuthed(false);
  }

  const projectPayload = await api("/api/v1/projects");
  state.projects = projectPayload.projects;
  const select = document.querySelector("#filter-project");
  const current = select.value;
  select.innerHTML = `<option value="">全部项目</option>` + state.projects.map((project) =>
    `<option value="${escapeHtml(project.key)}">${escapeHtml(project.name || project.key)}（${escapeHtml(project.key)}）</option>`).join("");
  select.value = current;

  state.tasks = (await api("/api/v1/tasks?" + filters().toString())).tasks;
  state.agents = (await api("/api/v1/agents")).agents;
  renderSummary();
  renderList();
  renderBoard();
  renderAgents();
  if (state.selected) await openTask(state.selected);
  else showEmptyDetail();
}

function setAuthed(ok, username = "") {
  document.querySelector("#auth-status").textContent = ok ? `已登录 · ${username || "admin"}` : "未登录";
  document.querySelector("#auth-status").className = `status-pill${ok ? "" : " neutral"}`;
  document.querySelector("#logout-button").hidden = !ok;
  document.querySelector("#login-form").hidden = ok;
}

function renderSummary() {
  const scoped = state.tasks;
  const done = scoped.filter((task) => task.status === "done").length;
  const rate = scoped.length ? Math.round((done / scoped.length) * 100) : 0;
  const pills = boardOrder.map((status) => {
    const count = scoped.filter((task) => task.status === status).length;
    return `<span><i class="status-dot ${status}"></i>${STATUS_LABELS[status]} ${count}</span>`;
  }).join("");
  document.querySelector("#task-summary").innerHTML =
    `<strong>共 ${scoped.length} 项 · 完成率 ${rate}%</strong>${pills}`;
}

function renderList() {
  const list = document.querySelector("#task-list");
  if (!state.tasks.length) {
    list.innerHTML = `<p class="muted" style="padding:14px">没有匹配的任务。</p>`;
    return;
  }
  if (state.selected && !state.tasks.some((task) => task.id === state.selected)) {
    state.selected = state.tasks[0]?.id || null;
  }
  list.innerHTML = state.tasks.map((task) => {
    const active = task.id === state.selected ? " active" : "";
    const owner = task.owner || task.legacy_owner || "未认领";
    return `<button type="button" class="record-item${active}" data-task="${escapeHtml(task.id)}">
      <span class="record-icon">${escapeHtml(task.id.replace("TS-", ""))}</span>
      <span>
        <span class="record-name">${escapeHtml(task.title)}</span>
        <span class="record-code">${escapeHtml(task.id)} · ${escapeHtml(task.project_key || "")}</span>
      </span>
      <span class="record-status"><i class="status-dot ${escapeHtml(task.status)}"></i>${escapeHtml(STATUS_LABELS[task.status] || task.status)}<br>${escapeHtml(owner)}</span>
    </button>`;
  }).join("");
  list.querySelectorAll("[data-task]").forEach((node) => {
    node.addEventListener("click", () => openTask(node.dataset.task));
  });
}

function renderBoard() {
  const board = document.querySelector("#board");
  board.innerHTML = boardOrder.map((status) => {
    const rows = state.tasks.filter((task) => task.status === status);
    const sample = rows.slice(0, 3).map((task) => escapeHtml(task.id)).join(" · ") || "空";
    return `<div class="mini-column"><h3><span><i class="status-dot ${status}"></i>${STATUS_LABELS[status]}</span><strong>${rows.length}</strong></h3><div class="sample">${sample}${rows.length > 3 ? " …" : ""}</div></div>`;
  }).join("");
}

function renderAgents() {
  const host = document.querySelector("#agents");
  if (!state.agents.length) {
    host.innerHTML = `<p class="muted">暂无 Agent</p>`;
    return;
  }
  host.innerHTML = state.agents.map((agent) => `
    <article class="agent-card">
      <div class="name">${escapeHtml(agent.id)}</div>
      <div class="meta">${escapeHtml(agent.client_type)} · ${agent.enabled ? "启用" : "停用"}<br>
      在线 ${escapeHtml(formatTime(agent.last_seen_at))}<br>
      任务 ${escapeHtml((agent.current_task_ids || []).join(", ") || "无")}</div>
    </article>`).join("");
}

function showEmptyDetail() {
  document.querySelector("#detail-eyebrow").textContent = "SELECT A TASK";
  document.querySelector("#detail-title").textContent = "选择一条任务查看详情";
  document.querySelector("#detail-meta").textContent = "—";
  document.querySelector("#detail-actions").innerHTML = "";
  document.querySelector("#detail").className = "form-container empty-state";
  document.querySelector("#detail").innerHTML = `
    <div class="empty-card">
      <span class="empty-symbol">◇</span>
      <h3>尚未选择任务</h3>
      <p>从左侧选择任务后，这里显示描述、scope、租约、交付记录、审计与管理操作。</p>
    </div>`;
}

async function openTask(taskId) {
  state.selected = taskId;
  renderList();
  const payload = await api(`/api/v1/tasks/${taskId}`);
  const events = await api(`/api/v1/tasks/${taskId}/events`);
  const task = payload.task;
  const conflicts = findConflicts(task);

  document.querySelector("#detail-eyebrow").textContent = STATUS_LABELS[task.status] || task.status;
  document.querySelector("#detail-title").textContent = `${task.id} · ${task.title}`;
  document.querySelector("#detail-meta").textContent =
    `${task.project_key || ""} · v${task.version} · ${task.owner || task.legacy_owner || "无负责人"}`;

  const actions = document.querySelector("#detail-actions");
  actions.innerHTML = actionButtons(task).map(([action, label, klass]) =>
    `<button type="button" class="button ${klass}" data-action="${action}">${label}</button>`).join("");
  actions.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runAction(task, button.dataset.action));
  });

  const detail = document.querySelector("#detail");
  detail.className = "form-container";
  detail.innerHTML = `
    <div class="detail-meta-row">
      <span class="meta-chip accent"><i class="status-dot ${escapeHtml(task.status)}"></i>${escapeHtml(STATUS_LABELS[task.status] || task.status)}</span>
      <span class="meta-chip">优先级 ${task.priority}</span>
      <span class="meta-chip" id="lease">${escapeHtml(leaseText(task))}</span>
    </div>
    ${conflicts.length ? `<div class="error-box">${conflicts.map((item) =>
      `与 ${escapeHtml(item.id)} 冲突 · scope ${escapeHtml(item.scopes.join(", "))} · owner ${escapeHtml(item.owner || item.legacy_owner || "未知")}`
    ).join("<br>")}</div>` : ""}
    <div class="detail-grid" style="margin-top:14px">
      <section class="detail-card"><h4>任务描述与验收要求</h4><p>${escapeHtml(task.description || "未登记")}</p></section>
      <section class="detail-card"><h4>工作范围 Scope</h4><p>${escapeHtml(task.scopes.join("、") || "未登记")}</p></section>
      <section class="detail-card"><h4>分支 / 交付</h4>
        <p>分支：${escapeHtml(task.branch_name || "未登记")}</p>
        <p>Commit：${escapeHtml(task.delivery_commit || "未交付")}</p>
        <p>测试：${escapeHtml(task.delivery_tests || "未登记")}</p>
        <p>备注：${escapeHtml(task.delivery_notes || "无")}</p>
      </section>
      <section class="detail-card"><h4>原因 / 阻塞</h4>
        <p>${escapeHtml(task.status_reason || task.blocked_reason || "无")}</p>
        <p>验收：${escapeHtml(task.accepted_by || "未验收")} ${escapeHtml(formatTime(task.accepted_at))}</p>
      </section>
      <section class="detail-card"><h4>编辑字段</h4>
        <div class="edit-grid">
          <label>标题<input id="edit-title" value="${escapeHtml(task.title)}"></label>
          <label>优先级<input id="edit-priority" type="number" value="${task.priority}"></label>
          <label>Scope<textarea id="edit-scopes">${escapeHtml(task.scopes.join("\n"))}</textarea></label>
          <label>描述<textarea id="edit-description">${escapeHtml(task.description || "")}</textarea></label>
          <button type="button" class="button primary" data-action="edit">保存编辑</button>
        </div>
      </section>
      <section class="detail-card"><h4>审计时间线</h4>
        <ol class="audit-list">${events.events.map((event) =>
          `<li>${escapeHtml(formatTime(event.server_created_at))} · ${escapeHtml(event.event_type)} · ${escapeHtml(event.actor_user || event.actor_agent_id || "system")}<br>${escapeHtml(event.old_status || "—")} → ${escapeHtml(event.new_status || "—")}</li>`
        ).join("") || "<li>暂无事件</li>"}</ol>
      </section>
    </div>`;
  detail.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runAction(task, button.dataset.action));
  });
}

function actionButtons(task) {
  const buttons = [];
  if (task.status === "pending_confirmation") buttons.push(["confirm", "确认可认领", "primary"]);
  if (task.status === "review") {
    buttons.push(["accept", "验收通过", "ok"]);
    buttons.push(["reject", "驳回", "danger"]);
  }
  if (task.status === "claimed") {
    buttons.push(["release", "释放", "secondary"]);
    buttons.push(["takeover", "强制接管", "danger"]);
  }
  if (task.status === "blocked") buttons.push(["unblock", "解除阻塞", "secondary"]);
  if (!["done", "cancelled"].includes(task.status)) {
    buttons.push(["block", "阻塞", "secondary"]);
    buttons.push(["cancel", "取消任务", "danger"]);
  }
  return buttons;
}

function findConflicts(task) {
  if (!["ready", "claimed", "review"].includes(task.status)) return [];
  return state.tasks.filter((other) =>
    other.id !== task.id
    && other.project_key === task.project_key
    && ["claimed", "review"].includes(other.status)
    && other.scopes.some((scope) => task.scopes.includes(scope)));
}

function leaseText(task) {
  if (!task.lease_expires_at) return "无短租约";
  const remaining = Math.max(0, Math.floor((new Date(task.lease_expires_at) - new Date()) / 1000));
  return `租约剩余 ${Math.floor(remaining / 60)} 分 ${remaining % 60} 秒`;
}

function formatTime(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

async function runAction(task, action) {
  try {
    if (action === "confirm") {
      await api(`/api/v1/tasks/${task.id}/confirm`, { method: "POST", body: "{}" });
      toast(`已确认 ${task.id}`);
    } else if (action === "accept") {
      if (!window.confirm("确认验收通过？")) return;
      await api(`/api/v1/tasks/${task.id}/accept`, { method: "POST", body: "{}" });
      toast(`已验收 ${task.id}`);
    } else if (action === "edit") {
      await api(`/api/v1/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          version: task.version,
          title: document.querySelector("#edit-title").value,
          description: document.querySelector("#edit-description").value,
          priority: Number(document.querySelector("#edit-priority").value),
          scopes: document.querySelector("#edit-scopes").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        }),
      });
      toast(`已保存 ${task.id}`);
    } else if (action === "unblock") {
      const reason = await askDanger("解除阻塞", "任务会回到 ready。");
      if (!reason) return;
      await api(`/api/v1/tasks/${task.id}/unblock`, { method: "POST", body: JSON.stringify({ reason }) });
      toast(`已解除阻塞 ${task.id}`);
    } else if (action === "takeover") {
      const extra = await askDanger("强制接管", "填写原因，并在补充栏写目标 agent id。", true);
      if (!extra) return;
      await api(`/api/v1/tasks/${task.id}/takeover`, {
        method: "POST",
        body: JSON.stringify({ reason: extra.reason, agent_id: extra.extra }),
      });
      toast(`已接管 ${task.id}`);
    } else if (action === "reject") {
      const extra = await askDanger("驳回", "补充栏填写 owner 或 ready。", true);
      if (!extra) return;
      await api(`/api/v1/tasks/${task.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason: extra.reason, return_to: extra.extra }),
      });
      toast(`已驳回 ${task.id}`);
    } else {
      const reason = await askDanger(STATUS_LABELS[action] || action, "此操作会写入审计日志。");
      if (!reason) return;
      await api(`/api/v1/tasks/${task.id}/${action}`, { method: "POST", body: JSON.stringify({ reason }) });
      toast(`已执行 ${action}`);
    }
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

function askDanger(title, copy, withExtra = false) {
  const dialog = document.querySelector("#danger-dialog");
  document.querySelector("#danger-title").textContent = title;
  document.querySelector("#danger-copy").textContent = copy;
  document.querySelector("#danger-reason").value = "";
  document.querySelector("#danger-extra").value = "";
  document.querySelector("#danger-extra-label").hidden = !withExtra;
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", function handle() {
      dialog.removeEventListener("close", handle);
      if (dialog.returnValue !== "confirm") resolve(null);
      else if (withExtra) {
        resolve({
          reason: document.querySelector("#danger-reason").value.trim(),
          extra: document.querySelector("#danger-extra").value.trim(),
        });
      } else {
        resolve(document.querySelector("#danger-reason").value.trim());
      }
    });
  });
}

async function createTask(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        id: form.get("id"),
        project_key: form.get("project_key"),
        title: form.get("title"),
        description: form.get("description"),
        priority: Number(form.get("priority") || 100),
        scopes: String(form.get("scopes") || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
      }),
    });
    event.target.reset();
    toast("任务已创建为待确认");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const payload = await api("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    });
    state.csrf = payload.csrf_token;
    setAuthed(true, payload.username);
    event.target.reset();
    toast("登录成功");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  try {
    await api("/api/v1/auth/logout", { method: "POST", body: "{}" });
  } catch {
    // ignore
  }
  state.csrf = null;
  setAuthed(false);
  toast("已退出");
});

document.querySelector("#create-project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({ key: form.get("key"), name: form.get("name"), description: "" }),
    });
    event.target.reset();
    toast("项目已创建");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});

document.querySelector("#create-task-form").addEventListener("submit", createTask);
document.querySelector("#refresh-button").addEventListener("click", () => {
  refresh().catch((error) => toast(error.message, true));
});
document.querySelector("#toggle-create").addEventListener("click", () => {
  const panel = document.querySelector("#create-panel");
  panel.hidden = !panel.hidden;
});

["#filter-project", "#filter-owner", "#filter-scope"].forEach((selector) => {
  document.querySelector(selector).addEventListener("change", () => {
    refresh().catch((error) => toast(error.message, true));
  });
});
document.querySelector("#filter-query").addEventListener("keydown", (event) => {
  if (event.key === "Enter") refresh().catch((error) => toast(error.message, true));
});
let queryTimer = null;
document.querySelector("#filter-query").addEventListener("input", () => {
  clearTimeout(queryTimer);
  queryTimer = setTimeout(() => refresh().catch((error) => toast(error.message, true)), 250);
});

setInterval(() => {
  const lease = document.querySelector("#lease");
  if (!lease || !state.selected) return;
  const task = state.tasks.find((item) => item.id === state.selected);
  if (task) lease.textContent = leaseText(task);
}, 1000);

renderStatusChips();
refresh().catch((error) => {
  document.querySelector("#service-status").textContent = "连接失败";
  document.querySelector("#service-status").className = "status-pill error";
  toast(error.message, true);
});
