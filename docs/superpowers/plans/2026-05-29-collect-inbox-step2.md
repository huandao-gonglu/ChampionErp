# Step 2 - 采集箱接入 SQLite 商品母库

## 目标

把采集箱从前端循环调用单条接口，收束为后端统一批量采集入口，并确保采集结果写入 SQLite 商品母库。

## 已完成

1. 1688 采集图片在适配层强制只保留前 5 张原图。
2. 新增 `parse_collect_urls()`，支持多链接换行、空格、逗号拆分并去重。
3. 新增 `collect_batch_products()`，逐条返回 `success / partial / failed` 状态、错误码、下一步建议、商品 ID、商品数据。
4. 新增 `/api/collect-batch` 后端接口，前端批量采集按钮已接入该接口。
5. 修复完全采集失败的新链接会复用旧商品的问题：现在新链接失败也会创建一条 `collect_status=failed` 的 SQLite 商品记录，并保留失败诊断。
6. 商品库索引记录补充 `collect_status` 字段，方便采集箱和商品库列表直接展示状态。

## 验证

`python -m unittest discover -s tests -v`

结果：7 个测试全部通过。

`python -m py_compile erp_db.py erp_web_app.py product_model.py marketplace_publish.py publishing_bus.py`

结果：通过。

## 下一步

Step 3 建议做“批量认领到平台草稿箱 + 状态机字段”：把采集成功/失败/已认领/copy_ready/images_ready/ready_to_publish 这些状态正式落到 `platform_drafts.status`，让前端按钮按状态点亮或置灰。
