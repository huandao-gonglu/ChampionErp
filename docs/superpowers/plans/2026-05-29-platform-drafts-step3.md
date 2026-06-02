# Step 3 - 平台草稿箱认领与状态机

## 目标

让商品从采集箱进入平台草稿箱后，有明确、可持久化的流程状态，避免用户在文案、图片、类目、价格没有准备好时盲目发布。

## 状态机

`collected -> claimed -> copy_ready -> images_ready -> ready_to_publish -> published`

## 已完成

1. 新增后端状态机：
   - `draft_workflow_status()`
   - `sync_product_workflow_statuses()`
   - 保存商品前自动同步各平台 `draft.status`
2. 新增批量认领能力：
   - `claim_products_to_platforms()`
   - `/api/claim-products`
   - 认领后写入 `platform_drafts.status=claimed`
3. AI 文案生成后自动标记：
   - `copy_source=ai`
   - `copy_generated_at`
   - 状态推进到 `copy_ready`
4. 商品库索引新增：
   - `workflow_status`
   - `draft_statuses`
   - `ai_copy_status / image_status / category_status / attributes_status / pricing_status`
5. 前端商品库新增：
   - “批量认领到平台草稿箱”按钮
   - “流程”状态列
   - 本地商品库文案改为 SQLite 商品母库
6. 已刷新现有 SQLite 中 7 条商品的状态字段。

## 验证

`python -m unittest discover -s tests -v`

结果：9 个测试全部通过。

## 下一步

Step 4 建议做“AI 文案批量生成真正按勾选商品执行”：现在页面上的批量 AI 文案仍更偏当前商品操作，下一步要让它读取商品库勾选项，逐条调用文案接口，回填对应 `platform_drafts`，并把状态批量推进到 `copy_ready`。
