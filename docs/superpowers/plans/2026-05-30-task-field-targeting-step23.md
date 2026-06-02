# Step 23 - 任务失败字段级定位

## 目标

把批量发布任务里的“去修复”从整块区域提示，升级成按失败字段打开正确页面并高亮具体控件。

## 已完成

1. 任务错误字段现在会解析为具体目标：
   - `images` / `picture` -> 商品编辑页图片区
   - `price` / `pricing` -> 核价器页面的价格输入项和核价结果
   - `attributes.BRAND` / `attributes.MODEL` -> 发布预检页的具体属性输入框
   - `category_id` / `category` -> 发布预检页类目输入区
2. `taskFixContextBanner` 移到全局顶部，不再只藏在商品编辑页里。
   - 修价格时打开核价器也能看到当前修复上下文。
   - 修类目属性时打开发布预检页也能保留提示。
3. 新增字段级高亮能力：
   - 普通 DOM id 继续支持。
   - 新增 `attr:BRAND` 这类属性字段定位，自动查找 `[data-attr="BRAND"]`。
4. `goFixPublishTask()` 会根据失败类型打开对应页面：
   - pricing -> `page-pricing`
   - category / attributes -> `page-publish`
   - images / basic -> `page-edit`

## 验证

已通过：

- 前端内联脚本语法检查：`inline script syntax ok`
- Python 单测：`29 passed`
- Playwright 浏览器回归：`14 passed`
- `打开新版ERP.cmd` 未修改，时间仍为 `2026/5/27 13:53:20`

## 下一步建议

Step 24：做“发布前确认页 + 真实 Payload 可视化”。

目标是在进入真实发布队列前，先让用户看到每个平台将要提交的标题、价格、类目、必填属性、图片数量和 payload 文件路径。确认页必须继续坚持“不假成功”：WB/Ozon 未接真实 API 时只能保存草稿或标记待接入，不能写发布成功日志。

建议模型：5.4 高。这个步骤涉及前端页面、payload 组装、状态判断和测试，复杂度中高。
