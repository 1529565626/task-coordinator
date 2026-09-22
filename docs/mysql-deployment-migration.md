# Task Coordinator 本次项目改造交接说明

日期：2026-09-22。交接对象：另一台服务器的维护者或实施 Agent。

## 1. 目标和当前结论

目标是在两台机器分别运行完整 Task Coordinator API 实例，各自客户端可请求本机 127.0.0.1:8787；两个实例连接 192.168.31.163:3306/taskcoord，共享任务状态。不是代理，不是两个 SQLite 数据库互相同步。

本次已完成 MySQL 基础兼容、本机 SQLite 数据复制和本机启动。尚未完成双实例并发安全改造与验证，不能视为完整的分布式版本发布。另一台服务器应先接收代码、核对数据，再完成下文门禁后启用双实例写入。

## 2. 本次实际代码修改

| 文件 | 改动及原因 |
| --- | --- |
| pyproject.toml | 添加 PyMySQL>=1.1，作为 SQLAlchemy MySQL 驱动 |
| src/taskcoord/database.py | schema_version 在 MySQL 使用 ON DUPLICATE KEY UPDATE；修订版本检查不再直接查询 sqlite_master；非 SQLite 跳过 PRAGMA integrity_check |
| src/taskcoord/services/backup_service.py | MySQL 健康检查使用 SELECT 1；非 SQLite 跳过原 SQLite 定时文件备份 |
| migrations/versions/0001_initial.py | import_batches 的 report_json、payload_json、committed_result_json 在 MySQL 使用 LONGTEXT，容纳历史导入内容 |
| AGENTS.md | 更新数据库后端说明；其中 MySQL 行锁要求属于工程要求，当前代码尚未完成相应并发实现 |
| docs/agent-integration.md | 之前已将任务 API 地址改为远程服务地址；这不是 MySQL 连接地址 |

上述代码当前保留在本机工作区，尚未为这次改造提交和推送。仅在另一台服务器 git pull 不能保证取得这些修改。需要先交付经核对的上述文件或后续对应提交，保留接收端已有修改，禁止复制整个目录覆盖。

## 3. 本机部署动作（不随 Git 分发）

- 项目虚拟环境已安装 PyMySQL，MySQL 服务版本验证为 8.0.44。
- 已创建并填充共享 taskcoord 数据库。
- 本机 .env 已设置 TASKCOORD_DATABASE_URL；不得复制到文档、提交或日志中。
- 本机旧服务已重启，入口为 http://127.0.0.1:8787/。
- SYSTEM 计划任务及登录计划任务创建均返回 Access is denied，未安装成功。
- 实际自启动方式是当前用户 Startup 文件夹的 TaskCoordinator.lnk，仅在该用户登录后启动，不是未登录时的系统开机服务。
- 远程服务器的运行进程、数据库配置、自启动均未在本次操作中更新。

## 4. 数据迁移范围及限制

复制来源是本机 E:\godot\task-coordinator\data\taskcoord.sqlite3，不是从远程 API 导出的数据库。目标为共享 MySQL taskcoord。导入成功时核对的数量如下，后续业务变化可能改变数量：

| 表 | 数量 |
| --- | ---: |
| projects | 2 |
| agents | 32 |
| tasks | 190 |
| task_scopes | 481 |
| task_events | 235 |
| idempotency_records | 39 |
| import_batches | 1 |
| users / sessions | 0 / 0 |
| service_meta | 原 3 条非版本记录，加初始化的 schema_version，共 4 条 |

仅核对过以上数量，未完成与远程服务器最新数据的逐条核对。远程健康响应曾显示 admin_configured=true，而本机来源 users=0，说明不能假定两份来源完全一致。迁移工具当时为一次性命令，尚未提交可复用的迁移脚本。

**接收端不要再次整库导入、清空或覆盖共享 MySQL。** 切换前暂停旧服务写入，备份远程原数据库和目标 MySQL；对比任务 ID、version、状态、更新时间、审计事件、Agent 身份及管理员数据。若远程有独有或更新记录，应制定有审计的差异合并方案后处理，不能按本机记录直接覆盖。

## 5. 已知未完成项（启用双实例写入前必须解决）

1. 原子认领：当前主要依赖 SQLite BEGIN IMMEDIATE；本次没有增加 MySQL 行锁。必须覆盖同一任务竞争，以及不同任务的重叠 scope 竞争。小规模可在事务内按项目加锁后完成冲突检查和认领。
2. 事务一致性：任务变更、token/租约校验、审计事件和幂等记录必须同事务完成；并发重放不能重复生效。
3. 多实例后台任务：核验租约清理、管理员初始化、健康写入和启动迁移的并发行为，避免重复处理和启动竞争。
4. 正式增量迁移：目前修改的是 0001_initial，并对目标库手工 ALTER 了 LONGTEXT。已有 0001 revision 的其他数据库不会自动重跑旧迁移，需补新增 Alembic revision，并同步 ORM 字段类型。
5. MySQL 备份恢复：只是跳过了 SQLite 定时备份；没有实现 MySQL 自动备份。现有手动备份/恢复仍为 SQLite 专用，应明确拒绝不支持的后端或实现独立方案。健康返回旧 SQLite 备份路径不能当作 MySQL 已备份。
6. 跨后端检查：补充 MySQL 时区、大小写/排序规则、长文本、连接断开与死锁场景的验证。
7. 安装脚本：install_autostart.ps1 未正确检查 schtasks 非零退出码，会在失败时打印成功；run_server.ps1 也应传播 Python 退出码，确保计划任务可按失败重启。

## 6. 另一台服务器接收及切换顺序

1. 检查工作区差异、运行进程、当前数据来源及启动方式，保存现有配置和数据库备份。
2. 接收本次源代码改动并核对文件；不复制 .env、.venv、data、logs，也不覆盖服务器原有认证配置。
3. 在现有项目虚拟环境安装更新后的依赖，无需重建虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

4. 完成第 4 节的数据核对及第 5 节并发与迁移修复。在验证通过前，不让两个实例同时接受业务写入。
5. 在接收端已有 .env 中仅增加或更新以下项，保留其他配置。密码中的特殊字符需要 URL 编码：

```text
TASKCOORD_DATABASE_URL=mysql+pymysql://<USER>:<URL_ENCODED_PASSWORD>@192.168.31.163:3306/taskcoord?charset=utf8mb4
```

6. 保留该服务器原监听地址、端口和认证策略。若其他客户端需要访问 192.168.31.163:8787，不要误改成仅监听 loopback；本地客户端仍可访问本机 loopback。不要照抄本机 anonymous_admin=true。
7. 停止已确认属于该项目的旧进程，再启动更新后的服务：

```powershell
.\scripts\run_server.ps1
```

8. 验证健康接口与抽样任务，确认实际加载的数据库后端及库名（不要输出完整带密码连接串）。只看到 ready 不足以证明连接了正确数据库。
9. 管理员权限安装自启动时，必须核对任务实际存在并试运行。若使用 Startup 快捷方式，明确记录为用户登录自启动。

## 7. 验证范围和验收标准

本次已验证：MySQL 登录、schema 初始化、本机数据复制数量、MySQL TestClient 生命周期启动与健康 HTTP 200，以及既有 pytest 套件通过。既有套件使用 SQLite 测试后端，其通过不能证明 MySQL 双实例并发正确；先前回复中的测试数量未以 collect-only 核实，不作为验收依据。

接收端需补验证：

- 两个独立服务进程连接同一个隔离测试库，竞争同一任务时只允许一次认领成功。
- 两个不同任务具有重叠 scope 时，同样只允许一方成功。
- heartbeat、deliver、accept/reject、过期 token 和版本冲突符合状态机。
- 幂等重试不重复事件；服务重启后状态与审计一致。
- 一个实例写入，另一个实例可读取提交后的相同结果。
- 数据库断开时明确失败，不回退本地 SQLite。
- 备份及恢复演练通过，再允许业务双实例写入。

## 8. 回退

切换前出现问题时保留旧服务和原数据库，暂缓切换。MySQL 已产生新业务写入后，不能直接改回旧 SQLite，否则会丢失新状态并形成两份事实源。应先停止写入、备份 MySQL、核对增量，再执行有记录的回退。不要删除现有数据库或历史备份。
