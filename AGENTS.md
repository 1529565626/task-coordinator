# Task Coordinator

本仓库是局域网任务协调服务。任务状态的唯一事实源是本服务配置的关系数据库（本地测试可用 SQLite，部署环境使用 MySQL）。运行时不依赖 Git pull、commit 或 push。

## 边界

- 本服务只记录任务状态、分支名和提交号。它不执行 `git pull`、`commit`、`merge`、`push`，也不修改任何游戏工程。
- 不把 SQLite、备份或日志放到 OneDrive、NAS 或 Windows 共享目录。生产 MySQL 由数据库服务负责持久化。
- 不把真实密码、API token、claim token、session secret 写入源码、文档、测试夹具或日志。
- `data/*.sqlite3`、`data/backups/`、`logs/`、`.env` 和本机 `config/service.toml` 不进入 Git。
- 不修改、覆盖或删除 TinySwordsPM 的 `data/tasks.json`。迁移只读源文件。
- 不安装开机计划任务、不改防火墙、不改系统配置，除非用户在对话中明确授权。脚本可以存在，但不能擅自执行。

## 技术栈

Python 3.12、FastAPI、Uvicorn、SQLAlchemy 2、Alembic、Pydantic 2、Pytest、MySQL（生产）/ SQLite（测试）。页面使用 Vue 2.7.16 + Element UI 2.15.14，本地静态依赖，无 Node 构建。

## Web 界面约定

- `src/taskcoord/web/index.html` 为正式入口；`dashboard.js` 管理 Vue 状态与 API 交互，`dashboard.css` 定义布局与蓝/绿/橙三套主题。
- `web/vendor/` 保存固定版本发行文件、字体及许可证，运行时不请求 CDN；升级依赖时更新版本说明。
- `demo.html` 保留设计预览，不作为真实任务来源；正式页只显示 API 数据。
- 管理员操作沿用会话、CSRF、幂等键和任务版本检查；界面不得绕过服务端状态机。
- 不覆盖正在编辑的表单；失败显示明确错误，未登录保留可用的只读入口。
- 主题只在浏览器本地保存，不存储密码或令牌。验证应包含三套主题、窄屏、空数据、登录失效与实际任务流程。
- 本项目的个人局域网实例使用 `anonymous_admin = true`，不启用账户权限；公开部署前必须关闭该选项并恢复管理员认证。

## 目录

- `src/taskcoord/`：服务、API、CLI、页面
- `migrations/`：Alembic
- `tests/unit`、`tests/integration`、`tests/concurrency`：测试
- `scripts/`：本机安装、启动、备份、迁移
- `docs/`：API、运维、迁移、Agent 接入

## 写入规则

- 所有写接口放在数据库事务里；SQLite 测试使用 `BEGIN IMMEDIATE`，MySQL 使用 InnoDB 事务和行锁。
- 写接口必须校验调用者、状态机和 version 或 claim token。
- 每次状态变化追加 `task_events`。审计行只插入，不更新、不删除。
- Agent 写操作必须支持 `Idempotency-Key`。相同键和相同请求重放第一次的结果；相同键不同请求体返回 `409 IDEMPOTENCY_KEY_REUSED`。
- 服务端时间决定租约。不信任客户端时钟。
- 认领失败时整体回滚。客户端只有拿到成功响应和 claim token 才算认领成功。
- 服务不可用时，CLI 必须以非 0 退出，不能用本地缓存假装认领成功。

## 状态机

`pending_confirmation` 确认后进入 `ready`。只有 `ready` 可以普通认领。`claimed` 必须有 owner、claim token 哈希和租约。`review` 继续占用 scope，但不因短租约自动释放。`done` 必须有验收者和验收时间。`cancelled` 为终态。管理员把非 `done` 任务标为 `blocked` 或 `cancelled` 时必须填写原因。

## 开发与验证

- 使用仓库 `.venv` 和 Python 3.12。
- 改动服务逻辑后运行 `pytest`。并发认领测试必须保持通过。
- 页面只通过 `/api/v1` 读写，不直接打开数据库。
- 默认监听 `127.0.0.1:8787`。改成局域网绑定前先看 `docs/operations.md`。

## 阶段

1. 数据库、状态机、原子认领、租约、审计、认证、幂等和并发测试。
2. `taskctl` 与 Agent 接入说明。
3. Web 管理页。
4. `tasks.json` 迁移工具。切换 mapBuild / TinySwordsPM 文档前，服务必须已运行且导入对账完成。
5. 开机自启动和防火墙只在用户明确授权后执行。
