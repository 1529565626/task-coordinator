# API

统一前缀 `/api/v1`。时间是带时区的 ISO 8601。每个 JSON 响应都有 `request_id`。写请求带 `Idempotency-Key`。

Agent 使用 `Authorization: Bearer <api token>`。当前个人局域网实例启用 `anonymous_admin = true`，页面和本机管理写操作不需要账户、Cookie 或 CSRF；Agent 认领仍使用 Agent token 与 claim token。多人或公网部署必须关闭该选项，恢复管理员登录 `POST /auth/login`、HttpOnly cookie `taskcoord_session` 和写操作 CSRF 校验。页面不保存 API token 或 claim token。

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

`400` 字段不合法，`401` 未认证，`403` 权限或 CSRF，`404` 不存在，`409` 冲突或非法状态，`422` 业务校验，`503` 未就绪或数据库忙。
