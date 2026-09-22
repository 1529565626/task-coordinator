# Codex 与 Cursor 接入

每个客户端使用稳定的 `agent_id`，例如 `codex-machine-a`、`codex-machine-b`、`cursor-machine-a`。不要共用 `codex` 或 `cursor`。

管理员在服务主机上创建身份，token 只出现一次：

```powershell
taskctl admin create-agent --id codex-machine-a --name "Codex A" --machine machine-a --client codex
```

用户目录 `~/.taskcoord/config.toml`：

```toml
server_url = "http://192.168.31.163:8787"
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
