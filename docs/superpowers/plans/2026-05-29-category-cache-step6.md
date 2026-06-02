# Step 6 - SQLite 类目缓存与必填属性闭环

## 本步目标

把原来依赖 JSON 文件读取的类目缓存，接入 SQLite 的 `category_cache` 表。前端搜索类目、读取必填属性、AI 自动填属性、发布前校验，都优先走 SQLite 本地库。

## 已完成

1. 在 `erp_db.py` 增加类目缓存读写服务：
   - `import_category_cache`
   - `search_category_records`
   - `find_category_record`
   - `category_cache_status`

2. 在 `erp_web_app.py` 增加 SQLite 优先的类目缓存包装层：
   - 首次访问某平台类目时，把现有 JSON 缓存导入 `erp.sqlite3`
   - `/api/category-search` 使用 SQLite 搜索，支持中文关键词
   - `/api/category-attrs` 使用 SQLite 返回必填/选填属性
   - `/api/category-ai-fill` 和 `/api/category-precheck` 使用 SQLite 类目记录

3. 修复必填属性统计问题：
   - 之前 `_required_attribute_summary` 把 `attributes_cache` 字典当列表遍历，容易导致必填项数量为 0
   - 现在按 `attributes_cache.required` 正确统计

4. 补上可见的前端分类选择体验：
   - 增加“选择分类”弹窗
   - 支持中文关键词搜索本地 SQLite 类目
   - 搜索结果按路径列表展示，命中词标红
   - 点击结果后自动回填 `category_id`、类目路径，并渲染必填/选填属性

## 当前真实导入结果

- Mercado Libre: 2 条类目记录
- Wildberries: 1 条类目记录
- Ozon: 1 条类目记录

当前数据量来自项目现有 JSON 缓存。后续 Mercado Libre Mexico 真实闭环时，需要用官方 Category/Attributes API 扩充完整类目库。

注意：当前只是跑通“本地 SQLite 搜索和属性回填链路”，不是完整 Mercado Libre 官方类目库。因为现有缓存量很小，搜索结果不会像成熟 ERP 那样一次返回几十个分类。

## 验证点

- SQLite 能导入类目缓存
- 中文“项链”能命中 Mercado Libre 类目
- 必填属性能从 SQLite 读取
- Web 层 `mock_category_attrs` 返回的 `cache_status.storage` 为 `sqlite`
- 必填属性摘要能正确统计缺失项
- 前端存在图二式“选择分类”弹窗，并能正确渲染 `attributes_cache.required/optional`

## 下一步切入点

Step 7 建议做“编辑发布页强校验”：

1. 前端选择类目后，直接展示 SQLite 返回的必填属性，并标红缺失项。
2. 将尺寸、重量、品牌、型号自动填入属性草稿。
3. 发布按钮根据 `ready_to_publish` 状态启用，缺必填项时只允许保存草稿，不允许假发布成功。
