# 鉴权测试自助指南（本地演示实例）

演示实例正在运行：`http://127.0.0.1:18787`（SQLite，云端鉴权模式，不影响局域网生产库）。

- 管理员账号：`admin` / `DemoAdmin2026!`（临时演示凭证）
- 健康检查令牌（演示）：`q3ISQxPavTTM798omqbzmmtdBeB8tzdK`
- 停止服务：结束 python 进程即可（演示实例跑在后台任务里）

## 0. 图形化方式（新增）

- **工作台登录门**：打开 `http://127.0.0.1:18787/`，未登录会直接显示全屏登录页，登录成功后才进入工作台。
- **鉴权对比台**：打开 `http://127.0.0.1:18787/apitest`
  1. 顶部二选一配置右侧凭证：管理员登录（Cookie + CSRF），或粘贴 Agent Bearer token。
  2. 固定 5 个代表性接口（探活 / 读 tasks / 读 projects / 管理写建项目 / Agent 写认领），每个只有一个「对比」按钮。
  3. 左侧始终 `credentials: omit`（不带 Cookie、不带 Token）；右侧按所选凭证发送。结果并排展示并附结论行。
  4. 建议流程：先不登录点「对比」确认左栏 401 → 再登录（或贴 Token）再点一次看右栏放行。

## 1. 浏览器方式（最快）

1. 打开 `http://127.0.0.1:18787/` —— 匿名状态下页面无数据（读接口已关）。
2. 用 `admin / DemoAdmin2026!` 登录 → 数据出现，可建任务/建 Agent。
3. 建一个 Agent，页面会显示一次性 API token，复制它。

## 2. 命令行方式

```bash
B=http://127.0.0.1:18787

# 匿名被拒（预期 401，响应头含 WWW-Authenticate: Bearer）
curl -i $B/api/v1/tasks

# 登录（预期 200，返回 csrf_token 并写入 Cookie）
curl -i -c /tmp/c.txt -X POST $B/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"DemoAdmin2026!"}'

# 已登录但缺 CSRF 的写请求（预期 403 CSRF_FAILED）
curl -i -b /tmp/c.txt -X POST $B/api/v1/projects \
  -H "Content-Type: application/json" \
  -d '{"key":"t1","name":"t1","description":""}'

# health/ready 令牌校验（预期：无头 401 / 带头 200）
curl -i $B/api/v1/health/ready
curl -i -H "X-Health-Token: q3ISQxPavTTM798omqbzmmtdBeB8tzdK" $B/api/v1/health/ready

# Agent token 访问（把 <TOKEN> 换成页面里创建 Agent 得到的 token）
curl -i -H "Authorization: Bearer <TOKEN>" $B/api/v1/tasks   # 预期 200
curl -i -H "Authorization: Bearer bad-token" $B/api/v1/tasks # 预期 401

# 登录限流：连续错 5 次后第 6 次预期 429 RATE_LIMITED
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST $B/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"nope"}'
done
```

## 3. 已由我实测通过的项目（2026-09-29）

| 场景 | 结果 |
| --- | --- |
| 匿名读 tasks/projects/agents | 401 + WWW-Authenticate: Bearer |
| 匿名写（无凭证、无参数） | 401（前置拦截，先于 422） |
| health/ready 无令牌 / 错令牌 | 401 |
| health/ready 正确令牌（X-Health-Token 或 Bearer） | 200 |
| 登录：错误密码 / 正确密码 | 401 / 200（返回 csrf_token） |
| 管理写缺 CSRF / 带 CSRF | 403 / 200 |
| Agent 错 token / 正确 token | 401 / 200 |
| Agent claim → 相同 Idempotency-Key 重放 → heartbeat | 200 / 200（幂等）/ 200 |
| Agent 调管理员接口 accept | 403 |
| 连续 5 次登录失败后 | 429 RATE_LIMITED |

对应 pytest：`tests/integration/test_auth_modes.py`（5 个用例），全量 45/45 通过。
