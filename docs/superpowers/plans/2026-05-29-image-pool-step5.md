# Step 5 - 图片池与 AI 生图状态闭环

## 目标

让 GPT / API / 本地上传进入图片池后，统一写入 SQLite 的 `media_assets`，同步到各平台草稿 `draft.images`，并推动状态机从 `copy_ready` 到 `images_ready`。

## 已完成

1. 新增统一图片池同步函数：
   - `sync_draft_images_from_pool()`
   - `save_image_pool_for_product()`
2. 新增后端接口：
   - `/api/image-pool/save`
3. 上传与同步生成图路径复用同一套状态同步逻辑：
   - `/api/image-pool/upload`
   - `/api/image-pool/sync-generated`
   - `/api/image-pool/save`
4. 图片池保存会同步：
   - `source.image_pool`
   - `media_assets`
   - 平台 `draft.images`
   - `draft.status`
   - 商品库 `workflow_status`
5. 前端图片池操作已持久化：
   - 单选/全选
   - 删除
   - 设置主图
   - 上移/下移排序
   - 平台标记
6. 已验证图片池保存后，文案就绪商品会推进到 `images_ready`。

## 验证

`python -m unittest discover -s tests -v`

结果：11 个测试全部通过。

`python -m py_compile erp_db.py erp_web_app.py product_model.py marketplace_publish.py publishing_bus.py`

结果：通过。

HTTP 冒烟：

`/api/image-pool/save` 返回 `ok=True, status=images_ready, images=1`。

## 下一步

Step 6 建议做“类目与必填属性本地 SQLite 闭环”：把当前 JSON 类目缓存逐步灌入 `category_cache` 表，前端搜索改成走 SQLite，选择类目后展示必填属性并驱动 `ready_to_publish` 前置校验。
