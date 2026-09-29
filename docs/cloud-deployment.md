# 云端部署指南（Linux + systemd + MySQL 8）

目标：从 GitHub 拉取 Task Coordinator，用本机导出的 `cloud_init.sql` 初始化云端 MySQL，以 systemd 常驻运行。公网部署默认关闭全部匿名访问（见 `docs/authorization.md`）。

**机密不进仓库。** 实际密码、哈希、Token、数据库 URL 只写在服务器上的 `.env` / `config/service.toml`，由运维在腾讯云侧粘贴；本文一律用占位符。

## 0. 前提

| 项 | 要求 |
| --- | --- |
| 代码来源 | GitHub 仓库（占位：`https://github.com/<ORG_OR_USER>/task-coordinator.git`，以实际公开/私有地址为准） |
| 分支 | `main`（除非另有约定） |
| 服务器 | Linux x86_64，Python 3.12+，可开放业务端口（示例 8787，建议经 Nginx HTTPS 反代） |
| 数据库 | MySQL 8.x（本机或腾讯云 MySQL），库名 `taskcoord`，字符集 `utf8mb4` |
| 系统用户 / 目录 | 建议用户 `taskcoord`，代码目录 `/opt/task-coordinator` |
| 初始化 SQL | **不在 Git 中**（见 `docs/migration/README.md`）。需另行上传 `cloud_init.sql` 到服务器 |

## 1. 初始化云端数据库

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS taskcoord CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p -e "CREATE USER IF NOT EXISTS 'taskcoord'@'localhost' IDENTIFIED BY '<MYSQL_PASSWORD>';"
mysql -u root -p -e "GRANT ALL PRIVILEGES ON taskcoord.* TO 'taskcoord'@'localhost'; FLUSH PRIVILEGES;"

# 将本机生成的 cloud_init.sql 上传后导入（路径按实际上传位置改）
mysql -u taskcoord -p taskcoord < /opt/task-coordinator/data/migration/cloud_init.sql
```

脚本内容：业务表结构 + 数据（`users` / `projects` / `agents` / `tasks` / `task_scopes` / `task_events` / `import_batches` / `service_meta`）；`sessions` 与 `idempotency_records` 只建空表；`alembic_version` 写入 `0001_initial`。

以 `data/migration/cloud_init.sql`（2026-09-28 导出）为例，导入后抽查：

```sql
SELECT COUNT(*) FROM tasks;         -- 193
SELECT COUNT(*) FROM task_events;   -- 257
SELECT COUNT(*) FROM agents;        -- 37
SELECT COUNT(*) FROM projects;      -- 2
SELECT version_num FROM alembic_version;  -- 0001_initial
```

若重新导出过 SQL，以文件内 `-- data: <table> (N rows)` 注释为准。

## 2. 拉取代码并安装

```bash
sudo useradd -r -m -d /opt/task-coordinator -s /usr/sbin/nologin taskcoord || true
sudo -u taskcoord -H bash -lc '
  cd /opt/task-coordinator
  # 若目录为空：git clone <GITHUB_URL> .
  # 若已有仓库：git fetch && git checkout main && git pull --ff-only
  python3.12 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -e .
'
```

可选：`.venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest`（测试用临时 SQLite，不碰生产库）。

## 3. 配置（占位符，上云时替换）

复制示例后改成本机路径与真实连接串：

```bash
sudo -u taskcoord cp /opt/task-coordinator/config/service.toml.example /opt/task-coordinator/config/service.toml
sudo -u taskcoord cp /opt/task-coordinator/.env.example /opt/task-coordinator/.env
sudo chmod 600 /opt/task-coordinator/.env
```

`config/service.toml`：

```toml
[server]
host = "127.0.0.1"          # Nginx 反代；直接对外则 0.0.0.0 + 安全组限源
port = 8787
anonymous_read = false      # 云端必须 false
anonymous_admin = false     # 云端必须 false

[database]
url = "mysql+pymysql://taskcoord:<URL_ENCODED_MYSQL_PASSWORD>@localhost:3306/taskcoord?charset=utf8mb4"
busy_timeout_ms = 5000

[leases]
enabled = true
duration_minutes = 120
heartbeat_minutes = 15

[backup]
directory = "/opt/task-coordinator/data/backups"
retention_days = 30

[logging]
path = "/opt/task-coordinator/logs/taskcoord.log"
retention_days = 14
```

`.env`（不进 Git）：

```text
TASKCOORD_ADMIN_USERNAME=admin
TASKCOORD_ADMIN_PASSWORD_HASH=<PBKDF2_HASH>
TASKCOORD_SESSION_SECRET=<RANDOM_SECRET>
TASKCOORD_HEALTH_TOKEN=<RANDOM_HEALTH_TOKEN>
TASKCOORD_CONFIG=config/service.toml
# 可选：覆盖 toml 里的数据库 URL
# TASKCOORD_DATABASE_URL=mysql+pymysql://...
```

生成密钥（在服务器上执行，明文密码只出现一次）：

```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
.venv/bin/python -c "import sys,hashlib,os; p=sys.argv[1].encode(); s=os.urandom(16); print('pbkdf2_sha256$200000$'+s.hex()+'$'+hashlib.pbkdf2_hmac('sha256',p,s,200000).hex())" '<ADMIN_PLAIN_PASSWORD>'
```

说明：启动时 `_seed_admin` 会用 `.env` 中的哈希覆盖（或创建）`users` 表里的管理员，因此即使 SQL 里已有 `admin` 行，云端登录密码以本次 `.env` 为准。

## 4. systemd

```bash
sudo cp /opt/task-coordinator/deploy/taskcoord.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now taskcoord
sudo systemctl status taskcoord --no-pager
```

## 5. 验证

```bash
curl -s http://127.0.0.1:8787/api/v1/health/live
# ready 若配置了 HEALTH_TOKEN：
curl -s -H "X-Health-Token: <TASKCOORD_HEALTH_TOKEN>" http://127.0.0.1:8787/api/v1/health/ready
# 匿名读应 401：
curl -si http://127.0.0.1:8787/api/v1/tasks | head -n 20
```

浏览器：`http://<主机>/` 应出现登录门；`/apitest` 可做无凭证 vs 有凭证对比。

## 6. MySQL 备份

内置定时备份只支持 SQLite；MySQL 用 cron + mysqldump：

```cron
30 3 * * * mysqldump -u taskcoord -p'<MYSQL_PASSWORD>' --single-transaction taskcoord | gzip > /opt/task-coordinator/data/backups/taskcoord-$(date +\%Y\%m\%d).sql.gz
```

## 7. 安全清单

- [ ] `anonymous_read = false`、`anonymous_admin = false`
- [ ] `.env` 新哈希与 session / health secret；权限 600
- [ ] MySQL 仅本机或安全组限源；对外 HTTPS（Nginx/Caddy）
- [ ] 日志与备份目录权限合理；`cloud_init.sql` 导入后可移出 Web 可达路径
- [ ] 局域网旧实例与云端不要同时写同一事实源：停旧写入 → 确认无新事件 → 导入 → 云端验证 → 旧实例只读或下线
- [ ] **已知风险**：MySQL 认领路径尚未统一 `SELECT ... FOR UPDATE`；单实例低并发靠 version 兜底。多 Agent 高并发前需加固（见 `docs/mysql-deployment-migration.md`）

给腾讯云助手的一键提示词见：`docs/tencent-cloud-deploy-prompt.md`。
