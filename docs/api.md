# API

统一前缀 `/api/v1`。时间是带时区的 ISO 8601。每个 JSON 响应都有 `request_id`。写请求带 `Idempotency-Key`。

Agent 使用 `Authorization: Bearer <api token>`。管理员登录 `POST /auth/login` 后使用 HttpOnly cookie `taskcoord_session`，写操作再带 `X-CSRF-Token`。页面不保存 API token 或 claim token。

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

## 错误

客户端看 `error.code`，不要解析中文 `message`。

`400` 字段不合法，`401` 未认证，`403` 权限或 CSRF，`404` 不存在，`409` 冲突或非法状态，`422` 业务校验，`503` 未就绪或数据库忙。
