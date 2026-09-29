# 数据库初始化 SQL（不进 Git）

`data/migration/cloud_init.sql` 由本机从生产 MySQL **只读导出**，体积较大且含业务数据，已列入 `.gitignore`，**不会随 GitHub 仓库分发**。

## 本机生成

```bash
# Windows 示例（在仓库根目录，使用已配置的源库）
.venv\Scripts\python.exe scripts\export_cloud_sql.py --source "mysql+pymysql://USER:URL_ENCODED_PASSWORD@HOST:3306/taskcoord?charset=utf8mb4"

# 默认输出：data/migration/cloud_init.sql
```

也可设环境变量 `TASKCOORD_SOURCE_URL`，或依赖脚本内可读的本机 `.env`（脚本不会打印凭据）。

## 上传到云端

任选其一：

- `scp data/migration/cloud_init.sql user@云主机:/opt/task-coordinator/data/migration/`
- 对象存储中转后再 `wget`/`coscli` 拉到上述路径
- 在腾讯云助手对话中由运维指定已上传的绝对路径

## 导入

见 `docs/cloud-deployment.md` 第 1 节。导入后按文件内 `-- data: <table> (N rows)` 做 `COUNT(*)` 对账。

## 空库备选（无历史数据时）

若不导入 `cloud_init.sql`，可只建空库，让服务启动时 Alembic `upgrade head` 建表（无任务数据）。云端仍须配置 `.env` 管理员哈希。
