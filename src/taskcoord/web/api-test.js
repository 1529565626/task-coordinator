/* 鉴权对比台：固定少量接口，左右并排「无凭证 vs 有凭证」。 */
"use strict";

const state = {
  csrf: null,
  username: null,
  bearer: null,
};

/** 代表性接口：公开 / 读 / 管理写 / Agent 写各覆盖一类。 */
const APIS = [
  {
    id: "live",
    m: "GET",
    path: "/api/v1/health/live",
    title: "探活（公开）",
    expect: "两侧都应 200",
  },
  {
    id: "tasks",
    m: "GET",
    path: "/api/v1/tasks?limit=5",
    title: "任务列表（读）",
    expect: "云端：左 401，右 200",
  },
  {
    id: "projects",
    m: "GET",
    path: "/api/v1/projects",
    title: "项目列表（读）",
    expect: "云端：左 401，右 200",
  },
  {
    id: "create-project",
    m: "POST",
    path: "/api/v1/projects",
    title: "创建项目（管理写）",
    expect: "左 401；会话+CSRF → 200/冲突；仅 Agent token → 403",
    body: () => ({
      key: "apitest-" + Date.now().toString(36).slice(-6),
      name: "鉴权对比临时项目",
      description: "由对比台创建，可忽略",
    }),
  },
  {
    id: "claim",
    m: "POST",
    path: "/api/v1/tasks/TS-900/claim",
    title: "认领任务（Agent 写）",
    expect: "左 401；Agent token → 200/业务错误；仅会话 → 401",
    body: () => ({
      agent_id: "claude-demo",
      branch_name: "task/TS-900-apitest",
      continue_from: [],
    }),
  },
];

function credMode() {
  const el = document.querySelector('input[name="cred-mode"]:checked');
  return el ? el.value : "session";
}

function refreshPill() {
  const pill = document.querySelector("#pill-cred");
  const mode = credMode();
  if (mode === "session") {
    if (state.username) {
      pill.textContent = `凭证：已登录 ${state.username}`;
      pill.className = "pill ok";
    } else {
      pill.textContent = "凭证：未登录（右侧会失败）";
      pill.className = "pill bad";
    }
  } else if (state.bearer) {
    pill.textContent = "凭证：已设置 Agent token";
    pill.className = "pill ok";
  } else {
    pill.textContent = "凭证：未设置 Agent token";
    pill.className = "pill bad";
  }
}

function describe(status, wwwAuth) {
  if (status === 401) return wwwAuth ? `401 未授权（${wwwAuth}）` : "401 未授权";
  if (status === 403) return "403 已认证但无权限";
  if (status === 409) return "409 冲突（业务拒绝，但鉴权已通过）";
  if (status === 422) return "422 参数错误（鉴权已通过）";
  if (status === 429) return "429 限流";
  if (status >= 200 && status < 300) return `${status} 通过`;
  if (status === 0) return "网络错误";
  return `HTTP ${status}`;
}

function colorOf(status) {
  if (!status) return "sx";
  if (status < 300) return "s2";
  if (status < 500) return "s4";
  return "s5";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

async function send(api, side) {
  const headers = { Accept: "application/json" };
  let credentials = "omit";
  let body;

  if (side === "auth") {
    const mode = credMode();
    if (mode === "session") {
      credentials = "same-origin";
      if (state.csrf) headers["X-CSRF-Token"] = state.csrf;
    } else if (state.bearer) {
      credentials = "omit";
      headers.Authorization = "Bearer " + state.bearer;
    }
  }

  if (api.m !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["Idempotency-Key"] = crypto.randomUUID();
    body = JSON.stringify(typeof api.body === "function" ? api.body() : (api.body || {}));
  }

  const started = performance.now();
  try {
    const resp = await fetch(api.path, { method: api.m, headers, body, credentials });
    const text = await resp.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
    return {
      status: resp.status,
      body: data,
      ms: Math.round(performance.now() - started),
      wwwAuth: resp.headers.get("www-authenticate") || "",
    };
  } catch (err) {
    return {
      status: 0,
      body: { message: String(err) },
      ms: Math.round(performance.now() - started),
      wwwAuth: "",
    };
  }
}

function verdictText(anon, authed) {
  const anonBlocked = anon.status === 401 || anon.status === 403;
  const authPassedGate = authed.status !== 401 && authed.status !== 0;
  const authOk = authed.status >= 200 && authed.status < 300;

  if (anonBlocked && authOk) {
    return `✓ 拦截生效：匿名 ${anon.status}，带凭证 ${authed.status}`;
  }
  if (anonBlocked && authPassedGate && !authOk) {
    return `✓ 鉴权已区分：匿名 ${anon.status}；带凭证已过门禁（${authed.status}，多为业务/权限差异，属预期）`;
  }
  if (!anonBlocked && anon.status >= 200 && anon.status < 300 && authOk) {
    return `两侧都放行（${anon.status} / ${authed.status}）：公开接口，或当前仍是局域网匿名模式`;
  }
  if (anon.status === authed.status && anon.status === 401) {
    return `两侧都是 401：请先登录或粘贴有效 Agent token，并确认上方凭证模式选对`;
  }
  return `匿名 ${anon.status}，带凭证 ${authed.status} —— 对照接口说明判断`;
}

async function compare(api) {
  const host = document.querySelector("#results");
  if (host.querySelector(".muted")) host.innerHTML = "";

  const [anon, authed] = await Promise.all([send(api, "anon"), send(api, "auth")]);

  const wrap = document.createElement("article");
  wrap.className = "compare";
  const cell = (title, r) => `
    <div class="cell">
      <span class="tag">${title}</span>
      <div class="result-head">
        <span class="status ${colorOf(r.status)}">${r.status || "ERR"}</span>
        <span class="muted">${escapeHtml(describe(r.status, r.wwwAuth))} · ${r.ms}ms</span>
      </div>
      <pre>${escapeHtml(typeof r.body === "string" ? r.body : JSON.stringify(r.body, null, 2))}</pre>
    </div>`;

  wrap.innerHTML = `
    <div class="compare-meta">
      <span class="m ${api.m}">${api.m}</span>
      <code>${escapeHtml(api.path)}</code>
      <span class="muted">${escapeHtml(api.title)}</span>
      <button type="button" class="close" title="移除">✕</button>
    </div>
    <div class="compare-grid">
      ${cell("① 无凭证（credentials: omit）", anon)}
      ${cell("② 带凭证（" + (credMode() === "session" ? "会话" : "Bearer") + "）", authed)}
    </div>`;

  const verdict = document.createElement("div");
  verdict.className = "verdict";
  verdict.textContent = verdictText(anon, authed);
  wrap.appendChild(verdict);
  wrap.querySelector(".close").addEventListener("click", () => wrap.remove());
  host.prepend(wrap);
}

async function doLogin() {
  const username = document.querySelector("#login-username").value.trim();
  const password = document.querySelector("#login-password").value;
  const resp = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ username, password }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    alert(describe(resp.status, "") + " · " + (data.error?.message || "登录失败"));
    return;
  }
  state.username = data.username;
  state.csrf = data.csrf_token;
  document.querySelector('input[name="cred-mode"][value="session"]').checked = true;
  refreshPill();
}

async function doLogout() {
  try {
    const headers = { "Content-Type": "application/json" };
    if (state.csrf) headers["X-CSRF-Token"] = state.csrf;
    await fetch("/api/v1/auth/logout", {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: "{}",
    });
  } catch {
    /* 忽略 */
  }
  state.username = null;
  state.csrf = null;
  refreshPill();
}

function renderApis() {
  const host = document.querySelector("#api-list");
  host.innerHTML = "";
  for (const api of APIS) {
    const row = document.createElement("div");
    row.className = "api-row";
    row.innerHTML = `
      <span class="m ${api.m}">${api.m}</span>
      <div class="api-info">
        <code>${escapeHtml(api.path)}</code>
        <span class="title">${escapeHtml(api.title)}</span>
        <span class="expect">${escapeHtml(api.expect)}</span>
      </div>
      <button type="button" class="button primary compare-btn">对比</button>`;
    row.querySelector(".compare-btn").addEventListener("click", () => compare(api));
    host.appendChild(row);
  }
}

document.querySelector("#btn-login").addEventListener("click", doLogin);
document.querySelector("#btn-logout").addEventListener("click", doLogout);
document.querySelector("#login-password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") doLogin();
});
document.querySelector("#bearer-input").addEventListener("input", (e) => {
  state.bearer = e.target.value.trim() || null;
  if (state.bearer) {
    document.querySelector('input[name="cred-mode"][value="bearer"]').checked = true;
  }
  refreshPill();
});
document.querySelector("#btn-bearer-clear").addEventListener("click", () => {
  document.querySelector("#bearer-input").value = "";
  state.bearer = null;
  refreshPill();
});
document.querySelectorAll('input[name="cred-mode"]').forEach((el) => {
  el.addEventListener("change", refreshPill);
});

(async function init() {
  renderApis();
  try {
    const resp = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
    if (resp.ok) {
      const data = await resp.json();
      if (data.username && data.username !== "local") {
        state.username = data.username;
        state.csrf = data.csrf_token;
      }
    }
  } catch {
    /* 忽略 */
  }
  refreshPill();
})();
