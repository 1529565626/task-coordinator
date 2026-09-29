# 腾讯云 DeepSeek 部署提示词（复制整段发送）

把下面从「=== 提示词开始 ===」到「=== 提示词结束 ===」的内容整段复制发给腾讯云 DeepSeek 机器人。  
发送前先把文中所有 `⟦…⟧` 占位符换成你的真实值（或明确告诉机器人「这些值稍后我粘贴到文件里，你先写好文件骨架」）。

**不要**把真实密码、token、哈希贴进 GitHub Issue；可以贴进腾讯云侧私密对话或服务器文件。

---

=== 提示词开始 ===

你是腾讯云 CVM 上的运维助手。请按下列 runbook **逐步执行** Task Coordinator 的首次部署（不要跳步、不要臆造密码）。每完成一步简短汇报命令与关键输出；遇到需要我提供的机密时暂停并列出待填项。

## 背景

- 项目：局域网/云端任务协调服务 Task Coordinator（Python 3.12 + FastAPI + Uvicorn + SQLAlchemy + Alembic + MySQL 8）。
- 任务状态唯一事实源是本服务连接的 MySQL，不依赖 git pull/commit/push 做业务。
- 公网必须关闭匿名访问：`anonymous_read = false`、`anonymous_admin = false`。
- Agent 用 `Authorization: Bearer <api_token>`；管理员用登录会话 Cookie + `X-CSRF-Token`。
- 初始化 SQL **不在 Git 仓库里**，由我另行上传到服务器。

## 由我填写的参数（占位符）

请先确认下列值；缺任何一项就停下来问我：

| 占位符 | 含义 | 我的值 |
| --- | --- | --- |
| `⟦GITHUB_URL⟧` | Git 仓库地址（HTTPS 或 SSH） | （填写，例如 https://github.com/ORG/task-coordinator.git） |
| `⟦GIT_BRANCH⟧` | 分支 | main |
| `⟦GIT_TOKEN_OR_KEY⟧` | 私有仓凭证（若需要） | （填写或说明已配置 deploy key） |
| `⟦INSTALL_DIR⟧` | 代码目录 | /opt/task-coordinator |
| `⟦APP_USER⟧` | 运行用户 | taskcoord |
| `⟦MYSQL_ROOT_PASSWORD⟧` | MySQL root 密码（仅建库时用） | （填写或说明用 sudo mysql） |
| `⟦MYSQL_APP_PASSWORD⟧` | 应用库用户密码 | （填写） |
| `⟦MYSQL_HOST⟧` | MySQL 主机 | 127.0.0.1（本机）或云数据库内网地址 |
| `⟦MYSQL_PORT⟧` | 端口 | 3306 |
| `⟦CLOUD_INIT_SQL_PATH⟧` | 已上传的 cloud_init.sql 绝对路径 | （填写，例如 /opt/task-coordinator/data/migration/cloud_init.sql） |
| `⟦ADMIN_PLAIN_PASSWORD⟧` | 云端管理员登录明文密码（只用于生成哈希，勿写入日志） | （填写） |
| `⟦LISTEN_HOST⟧` | 服务监听 | 127.0.0.1（有 Nginx）或 0.0.0.0 |
| `⟦LISTEN_PORT⟧` | 端口 | 8787 |
| `⟦PUBLIC_URL⟧` | 对外访问入口（验证用） | （填写，例如 https://taskcoord.example.com） |

密码里的 `@ # % : / ?` 等写入数据库 URL 时必须做 **URL 编码**。

## 约束（必须遵守）

1. 不要把 `.env`、真实密码、token、哈希提交到 Git；不要 `echo` 明文密码到聊天记录以外的共享日志。
2. 不要执行 `rm -rf` 清空业务数据目录，除非我明确授权。
3. 不要改安全组/防火墙为「0.0.0.0/0 开放 MySQL」；MySQL 尽量仅本机或内网。
4. 不要同时让局域网旧实例与云端实例对同一业务写入；本任务默认「云端新库 + 导入 SQL」为唯一写入口。
5. 安装依赖用 Python 3.12 虚拟环境：`pip install -e .`（不要全局 pip install）。
6. systemd 单元用仓库自带 `deploy/taskcoord.service`，按 `⟦INSTALL_DIR⟧` 校对路径后安装。

## 执行步骤

### A. 系统与用户

1. 确认 `python3.12`、`git`、`mysql` 客户端可用；缺则用发行版包管理器安装（Ubuntu：`python3.12-venv`、`git`、`mysql-server` 或仅 client + 云数据库）。
2. 创建系统用户 `⟦APP_USER⟧`，家目录/工作目录 `⟦INSTALL_DIR⟧`，shell `nologin`。
3. 创建目录：`⟦INSTALL_DIR⟧`、`data/backups`、`data/migration`、`logs`，属主为 `⟦APP_USER⟧`。

### B. 拉取代码

```bash
cd ⟦INSTALL_DIR⟧
# 空目录则：
sudo -u ⟦APP_USER⟧ git clone -b ⟦GIT_BRANCH⟧ ⟦GITHUB_URL⟧ .
# 已有仓库则：
sudo -u ⟦APP_USER⟧ git fetch origin
sudo -u ⟦APP_USER⟧ git checkout ⟦GIT_BRANCH⟧
sudo -u ⟦APP_USER⟧ git pull --ff-only origin ⟦GIT_BRANCH⟧
```

确认存在：`pyproject.toml`、`src/taskcoord/`、`deploy/taskcoord.service`、`config/service.toml.example`、`.env.example`、`docs/cloud-deployment.md`。

### C. 虚拟环境与依赖

```bash
sudo -u ⟦APP_USER⟧ -H bash -lc '
  cd ⟦INSTALL_DIR⟧
  python3.12 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -e .
'
```

### D. MySQL 建库并导入 SQL

1. 确认我已把 `cloud_init.sql` 放到 `⟦CLOUD_INIT_SQL_PATH⟧`；若文件不存在则 **停止并通知我上传**（该文件不在 Git 中）。
2. 建库与用户（按你环境改 root 登录方式）：

```bash
mysql -u root -p'⟦MYSQL_ROOT_PASSWORD⟧' -e "CREATE DATABASE IF NOT EXISTS taskcoord CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p'⟦MYSQL_ROOT_PASSWORD⟧' -e "CREATE USER IF NOT EXISTS 'taskcoord'@'localhost' IDENTIFIED BY '⟦MYSQL_APP_PASSWORD⟧';"
mysql -u root -p'⟦MYSQL_ROOT_PASSWORD⟧' -e "GRANT ALL PRIVILEGES ON taskcoord.* TO 'taskcoord'@'localhost'; FLUSH PRIVILEGES;"
```

若 `⟦MYSQL_HOST⟧` 不是本机，把 `'taskcoord'@'localhost'` 改成云数据库允许的来源主机，并在腾讯云 MySQL 控制台授权。

3. 导入：

```bash
mysql -u taskcoord -p'⟦MYSQL_APP_PASSWORD⟧' -h ⟦MYSQL_HOST⟧ -P ⟦MYSQL_PORT⟧ taskcoord < ⟦CLOUD_INIT_SQL_PATH⟧
```

4. 对账（以 2026-09-28 导出为例；若 SQL 更新过，以文件内 `-- data: … (N rows)` 为准）：

```sql
SELECT COUNT(*) AS tasks FROM tasks;                 -- 期望 193
SELECT COUNT(*) AS task_events FROM task_events;     -- 期望 257
SELECT COUNT(*) AS agents FROM agents;               -- 期望 37
SELECT COUNT(*) AS projects FROM projects;           -- 期望 2
SELECT version_num FROM alembic_version;             -- 期望 0001_initial
```

数量不对则停止，不要启动服务。

### E. 写入配置（机密由我提供或你在服务器本地生成后只写入文件）

1. 复制示例：

```bash
sudo -u ⟦APP_USER⟧ cp ⟦INSTALL_DIR⟧/config/service.toml.example ⟦INSTALL_DIR⟧/config/service.toml
sudo -u ⟦APP_USER⟧ cp ⟦INSTALL_DIR⟧/.env.example ⟦INSTALL_DIR⟧/.env
sudo chmod 600 ⟦INSTALL_DIR⟧/.env
```

2. 编辑 `config/service.toml`：

- `[server] host = "⟦LISTEN_HOST⟧"`, `port = ⟦LISTEN_PORT⟧`
- `anonymous_read = false`, `anonymous_admin = false`
- `[database] url = "mysql+pymysql://taskcoord:<URL编码后的⟦MYSQL_APP_PASSWORD⟧>@⟦MYSQL_HOST⟧:⟦MYSQL_PORT⟧/taskcoord?charset=utf8mb4"`
- `[leases] enabled = true`
- `[backup] directory = "⟦INSTALL_DIR⟧/data/backups"`
- `[logging] path = "⟦INSTALL_DIR⟧/logs/taskcoord.log"`

3. 在服务器上生成并写入 `.env`（不要把结果完整贴回公开聊天，只确认「已写入」）：

```bash
cd ⟦INSTALL_DIR⟧
# SESSION_SECRET / HEALTH_TOKEN：
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
# ADMIN 哈希（传入 ⟦ADMIN_PLAIN_PASSWORD⟧）：
.venv/bin/python -c "import sys,hashlib,os; p=sys.argv[1].encode(); s=os.urandom(16); print('pbkdf2_sha256$200000$'+s.hex()+'$'+hashlib.pbkdf2_hmac('sha256',p,s,200000).hex())" '⟦ADMIN_PLAIN_PASSWORD⟧'
```

`.env` 内容模板：

```text
TASKCOORD_ADMIN_USERNAME=admin
TASKCOORD_ADMIN_PASSWORD_HASH=<上一步哈希>
TASKCOORD_SESSION_SECRET=<随机串>
TASKCOORD_HEALTH_TOKEN=<随机串>
TASKCOORD_CONFIG=config/service.toml
```

说明：服务启动时会用 `.env` 哈希覆盖/创建 `users.admin`，云端登录密码以本次为准。

### F. systemd 启动

1. 若 `deploy/taskcoord.service` 内路径不是 `⟦INSTALL_DIR⟧`，先改再安装。
2. 安装并启动：

```bash
sudo cp ⟦INSTALL_DIR⟧/deploy/taskcoord.service /etc/systemd/system/taskcoord.service
sudo systemctl daemon-reload
sudo systemctl enable --now taskcoord
sudo systemctl status taskcoord --no-pager -l
sudo journalctl -u taskcoord -n 50 --no-pager
```

3. 失败则根据日志修复配置后 `systemctl restart taskcoord`，不要反复盲目重启超过 3 次而不分析日志。

### G. 验证（全部做完再汇报）

```bash
curl -sS http://127.0.0.1:⟦LISTEN_PORT⟧/api/v1/health/live
curl -si http://127.0.0.1:⟦LISTEN_PORT⟧/api/v1/tasks | head -n 25
# 期望：匿名 /tasks 返回 401，且含 WWW-Authenticate: Bearer

TOKEN=$(grep TASKCOORD_HEALTH_TOKEN ⟦INSTALL_DIR⟧/.env | cut -d= -f2-)
curl -sS -H "X-Health-Token: $TOKEN" http://127.0.0.1:⟦LISTEN_PORT⟧/api/v1/health/ready | head
```

登录验证（不要在日志里打印密码）：

```bash
curl -sS -c /tmp/tc_cookies.txt -X POST http://127.0.0.1:⟦LISTEN_PORT⟧/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"⟦ADMIN_PLAIN_PASSWORD⟧"}'
# 期望 200 且返回 csrf_token；再用 Cookie + X-CSRF-Token 请求 GET /api/v1/tasks 期望 200
```

浏览器：打开 `⟦PUBLIC_URL⟧/` 应见登录门；`⟦PUBLIC_URL⟧/apitest` 可用对比台验证「无凭证 401 / 登录后 200」。

### H. 建议收尾

1. 配置 Nginx/Caddy HTTPS 反代到 `127.0.0.1:⟦LISTEN_PORT⟧`（若尚未配置，给出一段可用 server 块草稿即可，等我确认再改系统）。
2. 添加 mysqldump 日备 cron（密码用 mysql option file，避免写进 crontab 明文更佳）。
3. 汇报：Git commit、服务状态、四表 COUNT、匿名 401 是否通过、管理员登录是否通过、对外 URL。

## 参考文档（仓库内）

- `docs/cloud-deployment.md` — 完整 runbook
- `docs/authorization.md` — 鉴权矩阵
- `docs/migration/README.md` — SQL 为何不在 Git、如何导出上传
- `deploy/taskcoord.service` — systemd 单元

现在从 **A. 系统与用户** 开始执行；每步结束用几句话总结，然后继续下一步，直到 H 完成或被机密/缺失文件阻断。

=== 提示词结束 ===
