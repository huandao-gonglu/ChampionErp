# Step 18：预检结果驱动草稿状态机，并同步回商品库

完成时间：2026-05-30

## 本步目标

把“类目/属性填写完成”与“真正可发布”分开：

- 字段齐全时，草稿最多只到 `images_ready`
- 只有发布预检通过后，才进入 `ready_to_publish`
- 商品库列表与发布预检卡片同步显示最新流程状态

## 本步改动

### 1. 后端状态机收紧

文件：

- `erp_web_app.py`

新增 `_draft_precheck_ready(product, platform, draft)`，并修改 `draft_workflow_status(...)`：

- `copy_ready`：AI 文案完成
- `images_ready`：图片完成，且发布字段已基本齐全
- `ready_to_publish`：在上面基础上，再要求：
  - `publish_preview[platform].ok == true`
  - 或 `draft.publish_status == ready`

这样可以避免“字段看起来填完了，但实际上还没跑预检”时，状态被误判为可发布。

### 2. 预检成功后写回 SQLite 与商品库索引

文件：

- `erp_web_app.py`

沿用 `apply_precheck_to_product(...) + save_product(...)` 这条链路：

- `publish_preview`
- `draft.publish_status`
- `draft.status`
- `workflow_statuses`
- `products_index`

现在会一起同步，商品库里的流程状态能直接更新成“校验通过”。

### 3. 前端显示更直观

文件：

- `erp_web_template.html`

新增：

- 发布预检卡片显示：`流程状态：校验通过`
- 商品库 `precheck_status` 显示为：
  - `已通过`
  - `未通过`
  - `待预检`

不再直接显示 `true / false / pending`。

## 测试

### Python

- `python -m unittest discover -s tests -p "test_*.py" -v`
- 结果：`26` 个测试全部通过

### Playwright

- `npx playwright test tests/e2e/category-picker.spec.js`
- 结果：`10 passed`

新增覆盖：

- 发布预检成功后，发布页显示“流程状态：校验通过”
- 切到商品库后，列表同步显示“校验通过”

### 编译检查

- `python -m py_compile erp_web_app.py erp_db.py product_model.py`
- 结果：通过

## 说明

- `打开新版ERP.cmd` 未修改
- 这一步完成后，Step 17 的字段级高亮已经和 Step 18 的状态机推进串起来了
- 下一步可以继续做“状态驱动发布按钮和列表筛选”的收口
