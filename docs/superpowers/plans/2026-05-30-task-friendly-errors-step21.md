# Step 21：任务面板错误大白话与“去修复”入口

完成时间：2026-05-30

## 本步目标

把批量发布任务面板里的失败状态从“只有技术状态码”继续推进成能直接指导修复的业务界面：

- 把 `failed / not_ready` 翻译成大白话
- 提供 `去修复` 按钮
- 点击后直接跳到对应商品和对应编辑区块

## 本步改动

### 1. 任务面板新增错误翻译

文件：

- `erp_web_template.html`

新增：

- `taskFieldErrorMap(...)`
- `taskErrorItems(...)`
- `taskFriendlyError(...)`
- `taskFixSection(...)`

当前已覆盖的常见情况：

- `images`
  - 显示：`缺少主图，请先去图片区设置主图。`
- `price / pricing`
  - 显示：`价格或核价结果还没准备好，请先去价格区补齐并重新预检。`
- `attributes`
  - 显示：`类目属性还没补齐，请先去属性区完成必填项。`
- `category / category_id`
  - 显示：`类目还没有选对，请先去类目区重新选择并预检。`
- `not_ready`
  - 显示：`这条草稿还没达到发布条件，请先补齐类目、属性、价格或图片。`

### 2. 任务面板新增“去修复”按钮

文件：

- `erp_web_template.html`

在失败任务行里新增：

- `去修复`
- `重试`

其中 `去修复` 会根据错误字段自动判断跳转位置。

### 3. 失败任务可直接定位到商品编辑页

文件：

- `erp_web_template.html`

新增：

- `goFixPublishTask(...)`

逻辑：

1. 根据任务找到对应 `product_id`
2. 调 `/api/load-product` 重新加载商品
3. 自动切换到 `商品编辑` 页
4. 根据错误类别跳转到对应区块：
   - `images`
   - `pricing`
   - `attributes`
   - `category`
   - `basic`

也就是说，现在从任务失败到修复入口已经连起来了。

## 测试

### Python

- `python -m unittest discover -s tests -p "test_*.py" -v`
- 结果：`29` 个测试全部通过

### Playwright

- `npx playwright test tests/e2e/category-picker.spec.js`
- 结果：`13 passed`

新增覆盖：

- 任务面板能显示大白话错误：`缺少主图，请先去图片区设置主图。`
- 点击 `去修复` 后，会跳到商品编辑页

### 前端脚本语法检查

- 读取 `erp_web_template.html` 中的内联 `<script>`，用 `new Function(...)` 校验
- 结果：`inline script syntax ok`

## 说明

- `打开新版ERP.cmd` 未修改
- 这一步完成后，任务面板已经不是“只看状态”，而是具备：
  - 看失败原因
  - 直接定位修复
  - 修完后再重试

下一步继续做的话，最值得推进的是把“去修复”进一步细化成字段级定位和自动高亮。
