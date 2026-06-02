# Step 4 - 商品库批量 AI 文案

## 目标

让商品库里的“批量 AI 生成标题描述”真正按勾选商品逐条执行，而不是只处理当前打开的商品。

## 已完成

1. 新增后端批量文案函数：
   - `batch_generate_copy_for_products()`
2. 新增后端接口：
   - `/api/generate-copy-batch`
3. 批量流程会逐条：
   - 从 SQLite 商品母库加载商品
   - 调用现有 `generate_ai_copy_bundle()`
   - 复用 `save_copy_result()` 回填 `platform_drafts`
   - 自动写入 `copy_source=ai`
   - 自动写入 `copy_generated_at`
   - 状态推进到 `copy_ready`，如已有图片则按状态机继续推进到 `images_ready`
4. 前端商品库按钮已改造：
   - 勾选商品时调用 `/api/generate-copy-batch`
   - 未勾选时保留当前商品生成逻辑
5. 批量结果返回：
   - `success_count`
   - `failed_count`
   - 每条商品的 `status/title/warning/error`
   - 最新 `productsIndex`

## 验证

`python -m unittest discover -s tests -v`

结果：10 个测试全部通过。

`python -m py_compile erp_db.py erp_web_app.py product_model.py marketplace_publish.py publishing_bus.py`

结果：通过。

HTTP 冒烟：

`/api/generate-copy-batch` 返回 `ok=True, success_count=1, failed_count=0, status=copy_ready`。

## 下一步

Step 5 建议做“图片池与 AI 生图状态闭环”：让 GPT 生图任务包/API 生图回填后，把图片写入 `media_assets`，并把草稿状态从 `copy_ready` 推进到 `images_ready`。
