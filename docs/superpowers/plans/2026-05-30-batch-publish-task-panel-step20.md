# Step 20：批量发布任务面板、状态刷新与失败重试

完成时间：2026-05-30

## 本步目标

把商品库里的“批量发布已通过预检”从一次性动作，推进成可追踪的任务面板：

- 看得到每条任务的 `job_id`
- 看得到每个平台当前状态：`queued / running / success / failed`
- 失败项可以直接重试

## 本步改动

### 1. 商品库增加批量任务面板

文件：

- `erp_web_template.html`

新增区域：

- `publishTaskPanel`

展示内容：

- 商品标题
- 平台
- `job_id`
- 状态
- 阶段
- 尝试次数
- 错误信息
- `重试` 按钮

同时增加：

- `publishTaskRefreshBtn`

用于手动刷新整批任务状态。

### 2. 前端新增任务登记与状态汇总

文件：

- `erp_web_template.html`

新增变量与函数：

- `publishTaskEntries`
- `registerPublishTaskEntries(...)`
- `createPublishTaskEntriesFromJob(...)`
- `renderPublishTaskPanel(...)`
- `refreshPublishTaskPanel(...)`
- `retryPublishTask(...)`

实现逻辑：

- 每次加入发布队列后，把任务登记到本地任务面板
- 通过已有接口 `/api/publish-bus/status?job_id=...` 轮询单个任务状态
- 把任务按 `product_id + platform + job_id` 做唯一标识
- 在面板顶部汇总：
  - queued 数
  - running 数
  - success 数
  - failed 数

### 3. 批量发布动作接入任务面板

文件：

- `erp_web_template.html`

修改：

- `enqueuePublish(...)`
- `publishSelectedLibraryReady(...)`

现在无论是：

- 发布预检页单商品入队
- 商品库批量入队

都会同步写入任务面板。

### 4. 失败任务支持直接重试

文件：

- `erp_web_template.html`

重试逻辑：

1. 读取失败任务对应的 `product_id`
2. 调 `/api/load-product` 重新加载商品
3. 调 `/api/publish-bus/enqueue` 重新入队对应平台
4. 生成新的 `job_id`
5. 用新的任务替换旧失败任务

这样失败重试不会混淆旧任务和新任务。

## 测试

### Python

- `python -m unittest discover -s tests -p "test_*.py" -v`
- 结果：`29` 个测试全部通过

### Playwright

- `npx playwright test tests/e2e/category-picker.spec.js`
- 结果：`12 passed`

新增覆盖：

- 批量任务面板能显示失败任务的 `job_id / failed / error`
- 点击 `重试` 后，会生成新的 `job_id`
- 重试后的任务能显示 `success`

### 编译检查

- `python -m py_compile erp_web_app.py publishing_bus.py`
- 结果：通过

## 说明

- `打开新版ERP.cmd` 未修改
- 当前任务面板基于现有 `PublishingBus` 状态接口工作
- 这一步优先落地了：
  - 面板可见
  - 状态可刷新
  - 失败可重试

后续如果继续做，可以再补：

- 自动轮询动画
- 多商品多平台任务聚合页
- 按商品维度折叠
- 失败原因翻译成大白话
