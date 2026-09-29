# 授权方案（云端部署版）

目标：所有接口默认拒绝匿名访问，同时保证 AI Agent 客户端的机器对机器（M2M）访问尽量顺畅。

## 1. 三种角色与凭证

| 角色 | 凭证 | 谁在用 | 凭证形态 |
| --- | --- | --- | --- |
| Agent | API token | AI Agent 客户端、`taskctl` | `Authorization: Bearer <token>` |
| Admin | 会话 Cookie + CSRF | 人类（Web 管理页）、运维脚本 | `POST /auth/login` 签发，默认 12 小时有效（`session_ttl_hours` 可调），**滑动续期**：剩余不足一半时任何已登录请求自动续满窗口并重新下发 Cookie |
| Anonymous | 无 | 任何人 | **云端模式下所有 /api 接口拒绝** |

Agent token 全库只存 SHA-256 哈希（`agents.token_hash`），明文只在创建/轮换时返回一次。

**前端会话过期处理**：Web 工作台的 API 封装层对 401 做全局拦截——会话在页面打开期间过期时，自动清空登录态、弹出全屏登录门并提示"登录状态已过期"，无需手动刷新。

## 2. 接口管控矩阵（改造后）

| 接口 | 匿名 | Agent token | Admin 会话 |
| --- | --- | --- | --- |
| `GET /api/v1/health/live` | ✅（探活） | ✅ | ✅ |
| `GET /api/v1/health/ready` | 配置令牌后需 `X-Health-Token` | ✅ | ✅ |
| `GET /api/v1/version` | ✅（版本协商，无敏感信息） | ✅ | ✅ |
| `POST /api/v1/auth/login` | ✅（凭密码换会话，限流保护） | — | — |
| `GET /tasks` `/tasks/next` `/projects` `/agents` `/events` 等**全部读接口** | ❌ 401 | ✅ | ✅ |
| `POST /tasks/{id}/claim` `heartbeat` `deliver` `reissue-claim-token` | ❌ 401 | ✅（且校验 claim token） | ❌ |
| 建任务/改任务/confirm/accept/reject/cancel/unblock/项目管理/Agent 管理/admin/* | ❌ 401 | ❌（仅 block/release 走 Agent 路径） | ✅（且校验 CSRF） |

额外的纵深防御：写接口在进入路由和参数校验**之前**先做前置凭证检查，匿名请求一律最先拿到 401（而不是 422），不给未认证方任何参数校验探针。所有 401 响应都带 `WWW-Authenticate: Bearer`，Agent 程序可以据此统一触发重新认证逻辑。

## 3. 为什么这套方案对 AI Agent 最稳妥

1. **无状态 Bearer token**：Agent 不需要 Cookie、不需要 CSRF、不需要 OAuth 舞蹈。任何能发 HTTP 头的客户端（包括各种 Agent 框架）一行代码接入。服务端按哈希查库即可验证，无会话表、无过期续期问题。
2. **最小权限内置在状态机里**：Agent token 天然不能做管理操作（建任务、验收、管理其他 Agent）；即使 token 泄漏，攻击面被限制在"认领/交付自己名下的任务"。
3. **两层 token 设计**：API token（长期身份）+ claim token（单次任务认领凭据）。拿到 API token 也无法操作别人正在做的任务；claim token 只在认领响应中出现一次。
4. **可撤销、可轮换**：管理员随时 `POST /agents/{id}/rotate-token` 轮换（旧 token 立即失效）、停用 Agent（`enabled=false` → 403 `AGENT_DISABLED`）。泄漏处置路径清晰。
5. **机器可读的错误语义**：401 `AUTH_REQUIRED`（token 缺失/无效 → 检查凭证配置）、403 `AGENT_DISABLED` / `FORBIDDEN`（token 有效但无权限 → 不要重试）、429 `RATE_LIMITED`（登录限流 → 退避重试）。Agent 客户端按 error.code 分支处理即可，不用解析人类文案。
6. **幂等与重试友好**：写接口全部支持 `Idempotency-Key`，网络重试不会产生重复认领/重复交付——这对不稳定网络上的 Agent 尤其重要。

## 4. Agent 接入流程（管理员操作）

```text
1. 管理员登录 Web 页 → 创建 Agent（id/display_name/machine_name/client_type）
   → API token 只显示一次，复制交给对应 Agent 的配置
2. Agent 侧配置（环境变量或 config.toml）：
   TASKCOORD_SERVER=https://taskcoord.example.com
   TASKCOORD_AGENT_ID=codex-machine-a        # 必须与 token 对应的 agent id 一致
   TASKCOORD_API_TOKEN=<token>
3. 验证：taskctl health && taskctl next --project map-build
4. 可选轮换：管理员 rotate-token 后，同步更新 Agent 配置
```

失败排查速查：

| 现象 | 原因 |
| --- | --- |
| 401 `AUTH_REQUIRED` | 未带 Authorization 头 / token 错误 / agent_id 与 token 不匹配 |
| 403 `AGENT_DISABLED` | Agent 被停用，联系管理员 |
| 403 `FORBIDDEN`（agent_id 不一致） | 请求体里的 agent_id 和 token 属主不一致 |
| 403 `CSRF_FAILED` | Admin 会话写请求缺 `X-CSRF-Token` 头（Agent 不会遇到，走 Bearer） |

## 5. 部署配置要求（云端必须项）

`config/service.toml`：

```toml
[server]
anonymous_read = false    # 关闭匿名读
anonymous_admin = false   # 关闭匿名管理
```

`.env`（都在服务端，不进 Git）：

```text
TASKCOORD_ADMIN_PASSWORD_HASH=<pbkdf2_sha256 哈希>
TASKCOORD_SESSION_SECRET=<随机串>
TASKCOORD_HEALTH_TOKEN=<随机串>   # 设置后 /health/ready 需要携带
```

配合运维措施：

- **必须 HTTPS**：Bearer token 是"持有即身份"，明文 HTTP 传输等于公开。用 Nginx/Caddy 终结 TLS。
- **登录限流**：已内置（同源 60 秒 5 次失败 → 429）。uvicorn 多 worker 时各进程独立计数，如需全局限制在反代层（如 Nginx `limit_req`）再做。
- **健康检查令牌**：负载均衡探活 `/health/live` 保持开放；`/health/ready` 会暴露备份路径等运维细节，配置 `TASKCOORD_HEALTH_TOKEN` 后必须携带 `X-Health-Token`（或 Bearer）才能访问。
- **token 只经私密通道分发**：不要把 API token 写进对话记录、Issue 或文档；Agent 配置文件权限 600。

## 6. 局域网模式兼容说明

个人局域网实例此前用 `anonymous_read = true` + `anonymous_admin = true`（免登录）。本次改造**不删除**这两个开关，局域网试用可继续；但两者的组合等于无鉴权，`docs/cloud-deployment.md` 的安全清单要求公开部署前必须全部关闭。改造后开关语义不变，测试覆盖了两种模式。
