# Step 26 - 后端任务终态持久化

## 目标

把发布任务终态从前端临时回写，推进到后端持久化。刷新页面后，发布成功/失败、任务信息和日志不能丢。

## 已完成

1. 新增 `persist_publish_bus_terminal_results(job_state)`：
   - 读取发布队列状态。
   - 找到终态平台任务。
   - 将结果写回产品草稿。
   - 将结果写入 `publish_logs.json`。
2. 新增任务状态映射：
   - `success` -> `published`
   - `failed` -> `failed`
   - `not_ready` -> `not_ready`
   - `ready_for_real_publish` -> `ready_for_real_publish`
   - `skipped` -> `skipped`
3. 新增产品草稿回写：
   - `draft.publish_status`
   - `draft.status`
   - `draft.last_publish_task`
   - 失败时写入 `draft.validation_errors`
4. 新增日志幂等保护：
   - 同一个 `job_id + platform` 只写一条日志。
   - 重复刷新任务状态不会刷出重复日志。
5. `/api/publish-bus/status` 现在返回状态前，会先执行后端终态同步。

## 验证

已通过：

- 前端内联脚本语法检查：`inline script syntax ok`
- Python 单测：`32 passed`
- Playwright 浏览器回归：`15 passed`
- 新增后端单测确认：
  - 任务成功后 SQLite 产品草稿变成 `published`
  - `last_publish_task.job_id` 写入
  - 重复同步不会重复写日志
- `打开新版ERP.cmd` 未修改，时间仍为 `2026/5/27 13:53:20`

## 下一步建议

Step 27：做“页面验收清理”。

目标是把发布页和商品库里仍然偏调试的内容收一收：保留业务按钮、确认卡片、任务面板、日志入口；弱化或隐藏大段 debug JSON，让页面更接近芒果店长/店小秘式操作台。

建议模型：5.4 高。这个步骤主要是前端体验清理和回归测试，复杂度中等偏高。
