# Task Coordinator · 生产镜像
# 适用：宿主机 Python 版本过旧（如 CentOS 7.6 自带 3.6）时，用容器提供 Python 3.12 运行时。
# 构建：docker build -t task-coordinator:latest .
# .env / config / data / logs 不打进镜像，由宿主机挂载（见 deploy/docker-compose.yml）。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 以可编辑模式安装：taskcoord 始终解析到 /app/src，
# 使 settings.ROOT_DIR = /app，.env / config / data / logs 的默认路径全部落在挂载点上。
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir -e .

EXPOSE 8787
CMD ["python", "-m", "taskcoord.main"]
