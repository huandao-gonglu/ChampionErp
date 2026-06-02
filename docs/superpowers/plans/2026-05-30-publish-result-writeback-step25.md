# Step 25 - 发布结果回写

## 目标

任务面板刷新到终态后，页面状态不能只停留在任务表里；需要同步回当前商品、商品库列表和草稿状态。

## 已完成

1. 新增任务状态映射：
   - `success` -> `published`
   - `failed` -> `failed`
   - `not_ready` -> `not_ready`
   - `ready_for_real_publish` -> `ready_for_real_publish`
   - `skipped` -> `skipped`
2. 新增 `applyPublishTaskResultToProduct()`：
   - 更新 `draft.publish_status`
   - 成功时推进 `draft.status = published`
   - 写入 `draft.last_publish_task`
   - 失败时写入 `draft.validation_errors`
3. 新增 `applyPublishTaskResultsToState()`：
   - 如果任务对应当前商品，同步更新 `state.product`
   - 如果任务对应商品库记录，同步更新 `state.productsIndex`
   - 商品库成功后显示为 `published`
4. `registerPublishTaskEntries()` 在合并任务后自动执行回写，并刷新商品库和发布预览。

## 验证

已通过：

- 前端内联脚本语法检查：`inline script syntax ok`
- Python 单测：`31 passed`
- Playwright 浏览器回归：`15 passed`
- 新增断言确认：
  - 任务重试成功后，商品库记录变为 `published/published/job-retry-1`

## 当前边界

这一步先完成页面内回写；后端持久化回写还没有单独做。也就是说刷新浏览器后，最终状态仍以产品保存逻辑和后端队列/日志为准。

## 下一步建议

Step 26：做“后端任务终态持久化 + 日志合并”。

目标是发布队列完成后，后端也能把终态写回产品草稿和 `publish_logs`，这样刷新页面后不会丢失任务结果。

建议模型：5.5 中。这个步骤会碰后端队列、产品保存、日志结构，复杂度比 Step 25 高。
