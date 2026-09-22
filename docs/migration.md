# 从 tasks.json 迁移

源文件只读。脚本不会删除或覆盖 TinySwordsPM 的 `data/tasks.json`。

```powershell
python scripts/migrate_tasks_json.py --source <tasks.json> --dry-run
python scripts/migrate_tasks_json.py --source <tasks.json> --commit --decisions decisions.json
```

先用 `taskctl admin login` 保存本机管理员会话。服务不可达时脚本退出，不会写数据库。

`--dry-run` 调用 `POST /api/v1/admin/import/preview`，输出数量、状态、owner、scope 冲突和待处理的 claimed 任务。

`decisions.json`：

```json
{
  "owner_map": {"sasaki": "cursor-machine-a"},
  "claimed_resolutions": {"TS-131": "ready"}
}
```

`claimed` 任务必须逐项选择 `restore`、`ready` 或 `review`。`restore` 要求 owner 已经映射到存在的 Agent，并且不会把明文 claim token 交给旧客户端；新负责人需要调用 `taskctl reissue`。`review` 继续占用 scope。未知历史 owner 保存在 `legacy_owner`，不会自动变成可登录 Agent。

同一个源文件得到同一个 import id。重复 commit 返回第一次的结果。
