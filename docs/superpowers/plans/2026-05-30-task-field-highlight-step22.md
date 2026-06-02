# Step 22：任务修复上下文与字段级高亮

完成时间：2026-05-30

## 本步目标

把 Step 21 的“去修复”继续细化：

- 保留从任务面板进入修复的上下文
- 在商品编辑页显示修复提示条
- 根据失败原因自动高亮对应入口或字段

## 本步改动

### 1. 新增任务修复上下文

文件：

- `erp_web_template.html`

新增状态：

- `activeTaskFixContext`

当用户在任务面板点击 `去修复` 时，会记录：

- 商品 ID
- 商品标题
- 平台
- 错误大白话
- 修复区块
- 需要高亮的目标控件

### 2. 商品编辑页新增修复提示条

文件：

- `erp_web_template.html`

新增元素：

- `taskFixContextBanner`

显示内容：

- 当前正在修复哪个商品
- 哪个平台
- 为什么失败
- 下一步应该修哪里

例如：

- `缺少主图，请先去图片区设置主图。`

### 3. 新增字段/入口高亮逻辑

文件：

- `erp_web_template.html`

新增：

- `taskFixTargets(...)`
- `clearTaskFixHighlights(...)`
- `renderTaskFixContext(...)`
- `applyTaskFixHighlights(...)`

当前支持的高亮目标：

- 图片/主图错误：
  - `section-images`
  - `imagePoolCard`
- 价格/核价错误：
  - `section-pricing`
  - `purchaseCost`
  - `commissionRate`
  - `platformShippingCost`
  - `targetMargin`
  - `pricingResultCard`
- 类目属性错误：
  - `section-attributes`
  - `attrsBox`
- 类目错误：
  - `section-category`
  - `categoryId`
  - `categoryPath`
- 库存/SKU/UPC：
  - `stock`
  - `sku`
  - `upc`

### 4. “去修复”跳转后自动应用高亮

文件：

- `erp_web_template.html`

更新：

- `goFixPublishTask(...)`

现在会：

1. 加载对应商品
2. 设置 `activeTaskFixContext`
3. 渲染商品编辑页
4. 显示修复提示条
5. 高亮对应入口/字段
6. 滚动到对应区块

## 测试

### Python

- `python -m unittest discover -s tests -p "test_*.py" -v`
- 结果：`29` 个测试全部通过

### Playwright

- `npx playwright test tests/e2e/category-picker.spec.js`
- 结果：`13 passed`

新增覆盖：

- 点击任务面板 `去修复`
- 商品编辑页显示 `taskFixContextBanner`
- 图片错误会让 `section-images` 带上 `task-fix-highlight`

### 前端脚本语法

- 内联脚本语法检查通过

## 说明

- `打开新版ERP.cmd` 未修改
- 当前已经做到：任务失败 -> 大白话 -> 去修复 -> 页面提示 -> 字段/入口高亮
- 下一步可以把高亮进一步细到“属性具体字段”和“价格具体输入项”的自动定位
