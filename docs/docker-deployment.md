# Docker 精简部署（CentOS 7.6 + 已有 MySQL 容器）

> 背景：CentOS 7.6 自带 Python 3.6.8，yum 无 3.12 包。仓库现提供标准化的
> `Dockerfile` + `deploy/docker-compose.yml`，一条命令完成构建与启动，
> 不再需要服务器上临时手写文件。

## 部署步骤（共 4 步）

```bash
# 1. 上传代码（或 git clone）到 /root/task-coordinator
cd /root/task-coordinator

# 2. 准备配置（仓库里复制模板后填写真实值）
cp .env.example .env                # 填入管理员密码哈希、session secret、数据库 URL
cp config/service.toml.example config/service.toml

# 3. 启动（首次会自动构建镜像，服务启动时自动跑数据库迁移）
docker compose -f deploy/docker-compose.yml up -d --build

# 4. 验证
curl http://127.0.0.1:8787/api/v1/health/live
docker logs task-coordinator --tail 20
```

## 配置要点（只有两处）

| 文件 | 要改什么 |
| --- | --- |
| `.env` | `TASKCOORD_DATABASE_URL=mysql+pymysql://<用户>:<密码>@misery-rx-mysql:3306/taskcoord?charset=utf8mb4`。**主机名用容器名** `misery-rx-mysql`（同一 Docker 网络内可解析）。 |
| `config/service.toml` | 端口、日志路径保持默认即可（容器内固定挂载到 `/app`）。 |

**alembic.ini 不需要改**：运行时迁移由服务启动自动执行（`database.prepare_database`）；
手动执行 `alembic` 命令时，现在也支持 `TASKCOORD_DATABASE_URL` 环境变量，同样无需改文件。

## 关于"去掉 Docker、宿主机直跑"的建议（腾讯云 AI 方案的修正）

方向本身合理（服务只是一个 uvicorn 进程），但它给出的安装方式在 CentOS 7.6 上**大概率失败**：

- **pyenv 也是源码编译**，并不比直接编译省事；
- Python 3.12 的 ssl 模块要求 **OpenSSL ≥ 1.1.1**，CentOS 7.6 系统自带 1.0.2k，需要先手工编译新版 OpenSSL——这才是真正的复杂度来源；
- Python 3.12 要求 C11 编译器，系统 GCC 4.8.5 偏旧，编译风险高。

因此结论：**保留 Docker 跑应用 + 复用现有 MySQL 容器，就是当前服务器上最快、最省事的形态**。
"精简"的正确姿势是把临时手写的 Docker 化内容收进仓库（本次已完成），而不是去掉这一层。

若将来确实想脱离 Docker，可行的免编译路径是
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
（预编译二进制，兼容 CentOS 7 的 glibc 2.17）：下载解压后直接建 venv，服务用
`deploy/taskcoord.service`（systemd）管理，数据库地址改回 `127.0.0.1:3306` 即可。
