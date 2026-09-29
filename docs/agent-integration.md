# Codex 与 Cursor 接入

每个客户端使用稳定的 `agent_id`，例如 `codex-machine-a`、`codex-machine-b`、`cursor-machine-a`。不要共用 `codex` 或 `cursor`。

## 领取 API token（管理员签发）

Agent token 由管理员签发，Agent 本身不注册、不登录。云端部署的三种方式（等价）：

1. **网页后台**：登录工作台 → Agent 页签 → 新建 → 复制一次性 `api_token`；
2. **API 测试台** `http://<服务器>:8787/apitest`：先登录拿会话，再发送"创建 Agent"卡片；
3. **curl**（需管理员会话 Cookie + CSRF，见 `api.md`）：

```bash
curl -X POST http://<服务器>:8787/api/v1/agents \
  -H "Cookie: taskcoord_session=<会话>" -H "X-CSRF-Token: <csrf>" \
  -H "Content-Type: application/json" -H "Idempotency-Key: <唯一键>" \
  -d '{"id":"codex-machine-a","display_name":"Codex A","machine_name":"machine-a","client_type":"codex"}'
```

`api_token` 明文**只在创建响应里出现一次**，服务端只存 SHA-256 哈希。泄露或需轮换时 `POST /api/v1/agents/{id}/rotate-token`（旧 token 立即失效）；临时停用把 `enabled` 置 false（返回 `403 AGENT_DISABLED`）。

用户目录 `~/.taskcoord/config.toml`：

```toml
server_url = "http://<云端服务器IP>:8787"
agent_id = "codex-machine-a"
project_key = "map-build"
```

`TASKCOORD_API_TOKEN` 放在用户环境，不写进业务仓库。claim token 由 CLI 放在用户目录的 `claims.json`，交付或释放成功后删除。

## 开工

1. `taskctl health`。失败就停止，不能离线认领。
2. `taskctl next --project map-build` 或 `taskctl show TS-132`。
3. `taskctl claim TS-132 --branch task/TS-132-example`。
4. 只有命令成功并且本地保存了 claim token，才能创建任务分支。
5. `409` 时停止。不要改本地文件去覆盖 owner。

服务不可用时可以继续写当前任务的本地代码，但不能认领、释放、交付或宣称状态已经更新。

## 交付

代码仓库里的测试和 commit 仍由各项目的 Git 规则负责。服务只登记分支、完整 SHA 和实际测试结果，不执行 Git 命令，也不检查提交是否已推送。

```powershell
taskctl deliver TS-132 --branch task/TS-132-example --commit <40位SHA> --tests "专项测试 28/28；等待人工视觉验收"
```

交付后任务进入 `review` 并继续占用 scope。验收由人在页面执行 accept 或 reject。驳回给原负责人后，该 Agent 执行 `taskctl reissue TS-132` 领取新的 claim token。

## 凭证与错误处理

- API token 长期有效但可被轮换/停用：收到 `401` 说明 token 失效（已轮换或 Agent 被停用），停止重试，联系管理员重新签发；`403 AGENT_DISABLED` 同理。
- 服务器返回 401 时带 `WWW-Authenticate: Bearer` 头，可据此与业务错误区分。
- 任务级 `409 CLAIM_TOKEN_STALE` 表示 claim token 已被新租约取代，执行 `taskctl reissue` 领新 token，不要重放旧 token。
- 所有写请求必须带唯一 `Idempotency-Key`，网络超时用相同键重试是安全的（幂等）。
