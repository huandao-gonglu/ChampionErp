# Step 19：商品库筛出可发布集合，并限制发布队列准入

完成时间：2026-05-30

## 本步目标

把 `ready_to_publish` 从“一个状态标签”继续推进成“真正的发布准入条件”：

- 商品库里可以直接筛出“校验通过”的商品
- 批量发布入口只允许这些商品进入
- 就算直接调用后端接口，未达到 `ready_to_publish` 的草稿也不能进发布队列

## 本步改动

### 1. 后端新增发布队列准入判定

文件：

- `erp_web_app.py`

新增：

- `publish_queue_platforms(product, requested_platforms=None)`

规则：

- 只有 `draft_workflow_status(product, platform) == "ready_to_publish"` 的平台，才算可入队

同时在 `product_index_status(...)` 中新增：

- `publish_queue_ready`
- `publish_queue_platforms`

这样商品库索引里会直接带上“这条商品现在能不能进发布队列”的结果。

### 2. 后端接口强制拦截未达标草稿

文件：

- `erp_web_app.py`

修改 `/api/publish-bus/enqueue`：

- 先按请求平台计算 `eligible_platforms`
- 如果一个都不合格，直接返回：
  - `PUBLISH_QUEUE_NOT_READY`
  - `eligible_platforms`
  - `rejected_platforms`
  - `workflow_statuses`
- 只有合格平台才真正调用 `PublishingBus.enqueue(...)`

这一步保证了不是只有前端“看起来锁住”，而是服务端也真正拦住。

### 3. 商品库增加“仅看校验通过”筛选

文件：

- `erp_web_template.html`

新增：

- `libraryWorkflowFilter`
  - `全部流程`
  - `仅看校验通过`
  - `未校验通过`

并且在商品标题区域增加状态提示：

- `可进入发布队列`
- `未达到发布队列条件`

### 4. 商品库新增“批量发布已通过预检”入口

文件：

- `erp_web_template.html`

新增：

- `libraryPublishReadyBtn`
- `libraryPublishSummary`
- `publishSelectedLibraryReady(...)`

逻辑：

- 只统计并入队已勾选、且 `ready_to_publish` 的商品
- 未达标商品会被跳过，并在摘要里明确写出来
- 发布队列请求会逐个调用：
  - `/api/load-product`
  - `/api/publish-bus/enqueue`

### 5. Playwright 文件改为干净 UTF-8

文件：

- `tests/e2e/category-picker.spec.js`

这次顺手把原先带乱码的 E2E 文件重写成 UTF-8，后续继续补测试会更稳定。

## 测试

### Python

- `python -m unittest discover -s tests -p "test_*.py" -v`
- 结果：`28` 个测试全部通过

新增覆盖：

- `publish_queue_ready`
- `publish_queue_platforms`
- 非 `ready_to_publish` 草稿不会被当成可入队记录

### Playwright

- `npx playwright test tests/e2e/category-picker.spec.js`
- 结果：`11 passed`

新增覆盖：

- 商品库切到“仅看校验通过”后，只显示可发布记录
- 勾选后点击“批量发布已通过预检”，只会把 `ready_to_publish` 商品送进发布队列

### 编译检查

- `python -m py_compile erp_web_app.py erp_db.py product_model.py publishing_bus.py`
- 结果：通过

## 说明

- `打开新版ERP.cmd` 未修改
- 当前批量入队已经有真实后端准入校验
- 但不同平台的真实发布适配器仍按当前阶段能力执行，Mercado Libre / WB / Ozon 的真实闭环程度依旧以各自接口接入进度为准
