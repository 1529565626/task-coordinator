# Task Coordinator

局域网任务协调服务。任务状态在 SQLite 里，不靠 Git 认领。

## 本地运行

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item config\service.toml.example config\service.toml
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m taskcoord.main
```

`scripts\bootstrap.ps1` 会创建虚拟环境，并在本机 `.env` 里生成管理员密码哈希。密码只在终端出现一次。

默认监听 `127.0.0.1:8787`。页面是 `http://127.0.0.1:8787/`。

## 客户端

```powershell
taskctl health
taskctl next --project map-build
taskctl claim TS-132 --branch task/TS-132-example
taskctl deliver TS-132 --commit <40-hex-sha> --tests "28/28 passed"
```

配置放在用户目录 `.taskcoord\config.toml`。API token 用环境变量 `TASKCOORD_API_TOKEN`。

## 还没做的切换

开机计划任务和防火墙脚本已写好，但没有执行。mapBuild 与 TinySwordsPM 仍使用原来的 Git 任务流，要等服务导入对账并得到确认后再改那些文档。
