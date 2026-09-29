# API

统一前缀 `/api/v1`。时间是带时区的 ISO 8601。每个 JSON 响应都有 `request_id`。写请求带 `Idempotency-Key`。

Agent 使用 `Authorization: Bearer <api token>`（由管理员在后台创建 Agent 时签发，明文只显示一次，详见 `agent-integration.md`）。

**云端模式（默认）**：`anonymous_read = anonymous_admin = false`，除 `GET /health/live` 和 `POST /auth/login` 外所有接口都需要凭证——Agent 带 Bearer token，管理写另需会话 Cookie + `X-CSRF-Token`（登录 `POST /auth/login` 签发，会话 12 小时滑动续期）。登录失败连续 5 次/分钟触发 `429 RATE_LIMITED`。管理凭证与 Agent token 互不相通。页面不保存 API token 或 claim token。

**局域网遗留模式**：`anonymous_admin = true` 时页面和本机管理写免认证，仅供个人内网实例，公网部署禁止使用。

## 健康

- `GET /health/live`
- `GET /health/ready`
- `GET /version`

## 任务

- `GET /tasks` 支持 `project`、`status`、`owner`、`scope`、`q`
- `GET /tasks/next?project=` 只推荐，不认领
- `GET /tasks/{id}` 当前负责人带 `X-Claim-Token` 时续租
- `POST /tasks/{id}/claim|heartbeat|release|deliver|takeover|reissue-claim-token`
- `POST /tasks/{id}/confirm|block|unblock|cancel|accept|reject`

认领成功只在响应里返回一次 `claim_token`。之后 owner 操作要同时提供 Agent token 和 claim token。旧 token 返回 `409 CLAIM_TOKEN_STALE`。

`review` 不因短租约自动释放。租约到期只把 `claimed` 恢复为 `ready`，并写 `task.claim_expired`。

### Agent 认领请求示例

```http
GET /api/v1/health/ready
GET /api/v1/tasks/next?project=project_0001
Authorization: Bearer <agent-api-token>

POST /api/v1/tasks/TS-132/claim
Authorization: Bearer <agent-api-token>
Idempotency-Key: <unique-request-id>
Content-Type: application/json

{"agent_id":"agent-fox","branch_name":"task/TS-132-example","continue_from":[]}
```

认领成功后，服务只在响应中返回一次明文 `claim_token`。后续 `heartbeat`、`release`、`deliver` 请求除 Agent token 外，还必须带 `X-Claim-Token: <claim-token>`。所有写请求都使用唯一 `Idempotency-Key`，网络超时可用相同键重试。

## 错误

客户端看 `error.code`，不要解析中文 `message`。

`400` 字段不合法，`401` 未认证（Agent token 无效/过期或未携带），`403` 权限不足、CSRF 失败或 Agent 已停用（`AGENT_DISABLED`），`404` 不存在，`409` 冲突或非法状态，`422` 业务校验，`429` 登录限流，`503` 未就绪或数据库忙。所有 401 响应都带 `WWW-Authenticate: Bearer` 头，客户端可据此触发重新认证。
