# Step 24 - 发布前确认页

## 目标

把“发布当前平台 / 发布全部已勾选平台”改成两段式：

1. 先预检并生成 payload 预览。
2. 用户确认后才加入发布队列。

这样可以避免黑箱式直接入队，也继续保证“不假成功”。

## 已完成

1. 新增 `pendingPublishConfirmation` 状态，保存待确认的平台、商品和 payload 预览结果。
2. 新增发布前确认卡片：
   - 显示平台、标题、类目、价格、图片数量、payload 路径、发布说明。
   - 显示脱敏后的 payload JSON。
   - 提供“确认加入队列”和“取消”按钮。
3. `publishCurrentPlatform()` 和 `publishSelectedPlatforms()` 改为：
   - 先跑 `/api/publish-precheck`
   - 再跑 `/api/publish-payload-preview`
   - 显示确认卡片
   - 不再直接调用队列接口
4. 只有点击 `confirmPublishQueueBtn` 后，才调用 `/api/publish-bus/enqueue`。
5. WB/Ozon 的 payload 预览仍显示“真实接口待接入”，不会写成功日志。

## 验证

已通过：

- 前端内联脚本语法检查：`inline script syntax ok`
- Python 单测：`30 passed`
- Playwright 浏览器回归：`15 passed`
- 新增浏览器用例确认：
  - 点击发布后先出现 `publishConfirmCard`
  - 确认前不会 enqueue
  - 点击确认后才出现 `job-confirm-1`
- `打开新版ERP.cmd` 未修改，时间仍为 `2026/5/27 13:53:20`

## 下一步建议

Step 25：做“发布结果回写 + 日志面板收口”。

目标是发布队列完成后自动刷新任务状态，把成功/失败结果写回草稿状态、商品库列表和日志面板；失败时继续保留大白话错误、字段级错误和“去修复 / 重试”。

建议模型：5.4 高。这个步骤涉及任务轮询、状态回写、日志展示和测试，复杂度中高。
