# 运维

数据库、备份和日志都在服务主机本地：

- `data/taskcoord.sqlite3`
- `data/backups/`
- `logs/taskcoord.log`

不要把 SQLite 放到网盘或共享目录。客户端只走 HTTP。

## 启动

`scripts/run_server.ps1` 会读取 `.env` 再启动。监听地址在 `config/service.toml`。初次使用 `127.0.0.1`。局域网阶段再改成固定地址或 `0.0.0.0`，并只对局域网来源开放 TCP `8787`。

就绪检查是 `GET /api/v1/health/ready`。

## 备份与恢复

服务大约每天备份一次，并删除超过 `retention_days` 的文件。也可以执行 `scripts/backup.ps1` 或 `taskctl admin backup`。

`taskctl admin restore --dry-run --file <backup>` 只做完整性检查。真正替换数据库前要先停掉服务进程，确认 `data/server.lock` 不存在。恢复不会自动执行。

日志按天轮转，默认保留 14 天。Authorization、Cookie、claim token 和密码会被脱敏。

## 开机自启动

`scripts/install_autostart.ps1` 和 `scripts/uninstall_autostart.ps1` 会改 Windows 计划任务。没有明确授权不要运行。卸载只删除名为 `TaskCoordinator` 的计划任务，不删除数据。
