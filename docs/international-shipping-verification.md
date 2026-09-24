# 国际物流计算核验记录

核验日期：2026-09-22。目标是验证现有独立物流模块到 ERP 核价的链路。按本次约定保留官方 Excel 的费率和计费步长；SKU 提供包装参数，模块负责计算，ERP 负责汇率换算、选择最低费用和保存。

## 结论与范围

| 环节 | 核验结果 |
|---|---|
| SKU 输入 | 前端逐 SKU 读取采购成本和包装资料，草稿覆盖值只覆盖该 SKU；kg 转 g，尺寸使用 cm |
| Ozon | 当前 133 条费率与原始 Excel 一致；按各行重量规则计算，没有统一套用体积重 |
| Yandex | 当前大陆 22 条费率、单位和步长与原始 Excel 的主费率表一致 |
| ERP 费用使用 | 返回全部候选，使用现有汇率统一币种后选最低；自动失败不回退内置表 |
| Mercado 多国家 | 每个国家使用对应授权分别请求、换算和保存，不跨国家选择最低价 |
| Mercado 跨境口径 | 未完成独立对照；接口响应成功不能证明与 Global Selling 跨境成本一致 |

本轮未发现需要修改的表格计费公式，因此没有改写业务算法、费率、包装资料流程或主项目边界。新增的是边界及链路回归测试。本记录不能作为三平台全部通过跨境费用验收的结论。

## 官方模板比对

使用应用归档的原始 XLSX，与当前已导入快照逐行比较，不依赖已不存在的下载实验目录。

| 平台 | 当前快照 | 原文件 SHA-256 |
|---|---|---|
| Ozon | `ozon-2b32c06c01ac519310ad` | `547bf780d6e9c4b9527a8462d5d523cee6fe4f402d0808bb5dd344ff215ab119` |
| Yandex | `yandex-d1749fe4d569da571a33` | `4ce21ed73446e2349e91836bde92baef722159ee24d21504699a652d0de62435` |

每条规则使用 16 个重量样本和 2 组尺寸，独立从原单元格提取费率、单位和重量规则后计算期望值。Ozon 4,256 组、Yandex 704 组，合计 4,960 组全部一致。这是金额计算检查；大尺寸样本也用于验证实际重渠道不会误用体积重，不代表这些尺寸都满足渠道准入条件。

重量样本（g）：1、1.01、2、2.01、99.99、100、100.01、367、500、500.01、501、2000、2001、5000、5001、30000。尺寸样本（cm）：20×10×5、80×50×30。

代表性结果已固化在 `tests/test_international_shipping.py`：

| 原始费率行 | 输入 | 计算结果 |
|---|---|---|
| Ozon 兴远 Economy Extra Small，G29 | 367 g | 3.37 + 0.0281 × 367，向上至分：13.69 CNY |
| Ozon 兴远 Standard Big，G93 | 3000 g，80×50×30 cm | 按表内除数 12000，计费 10000 g：321.44 CNY |
| Yandex ATC Economy Other，W12:X12、Z12 | 367 g | 100 g 步长进位至 400 g：400.40 RUB |
| Yandex CEL Express Other，W18:X18、Z18 | 367 g | 100 g 步长进位至 400 g：447.00 RUB |
| Yandex CEL Economy Extra Small，W26:X26、Z26 | 367 g | 1 g 步长：183.33 RUB |

Yandex 的 AA 示例总价不能作为统一基准：部分单元格只有缓存值；AA25 将 AA8 的 kg 输入直接乘轻小件的每克费率；部分有效公式又向上取整至整数 RUB，与现有模块向上保留两位的约定不同。本轮按既定范围保留费率列、步长列和分币精度，不宣称逐格复现 Excel 的 AA 展示值。普通件完整准入限制及轻小件 60/90 cm 政策仍有既有资料限制。

## Mercado 只读实测

当前入口为 `/users/{seller_id}/shipping_options/free`。使用现有授权，不创建、修改或发布商品；核验记录不保存令牌、账号 ID 或完整响应。

墨西哥样本：`dimensions=5x10x20,367`、`listing_type_id=gold_pro`、`condition=new`、`mode=me2`、`free_shipping=true`、`verbose=true`。

| 变体 | HTTP | `list_cost` |
|---|---|---|
| `item_price=30`、`currency_id=USD`、`logistic_type=drop_off` | 200 | 133.10 MXN |
| `item_price=30`、不传 `currency_id` | 200 | 66.55 MXN |
| `item_price=30`、`currency_id=MXN` | 200 | 66.55 MXN |
| `item_price=600`、`currency_id=MXN` | 200 | 133.10 MXN |
| `item_price=30`、`currency_id=USD`、`logistic_type=remote` | 200 | 133.10 MXN |

这说明该样本的币种参数会影响返回结果，不能因为文档未列出参数就直接删除 `currency_id=USD`。两种物流类型在单个样本上结果相同，也不足以据此改写业务口径。

[Global Selling 官方发货文档](https://global-selling.mercadolibre.com/devsite/manage-shipments)展示的商品成本接口为 `/marketplace/items/{ITEM_ID}/shipping_options/cost`，响应使用 `shipping_fee`；该文档要求商品已创建且激活。文档同节另列的 `/users/{USER_ID}/shipping_options/cost` 使用当前全球账号和市场子账号均返回 404；没有将它替换进生产代码。

本地 publication 索引没有已发布商品；市场账号 `/users/{seller_id}/items/search` 返回 403，因此尚未取得可与当前试算比较的商品成本。这里没有把接口错误理解为重量不足，也没有新增 SKU 重量推断或计费重响应字段的强制要求。

完成 Mercado 验收还需要同一市场、同一店铺、同一重量/尺寸及售价和刊登条件的一组官方跨境报价，或可查询成本的活跃跨境商品 ID。取得后应比较费用包含范围、币种、包邮和售价档位，再决定是否替换接口；仅凭本轮 HTTP 200 不足以下结论。

## 回归覆盖

- 后端：模板更新、全部候选、换汇后最低价、不同国家独立报价；补充官方费率样本、重量进位和 500 g 边界，以及 SKU 单位、授权子账号、刊登类型和包邮条件传递。
- 前端：原单 SKU 预览/应用场景扩展为单 SKU 与双 SKU 参数化测试，第二 SKU 使用不同成本、尺寸及重量覆盖值，验证运费、证据和国家报价独立保存；错误时不保存整组报价。
- 离线测试验证本地计算与编排契约，无法替代 Mercado 的跨境费用对照。

本轮执行结果：后端全量 `pytest tests -q` 为 1,762 passed、60 subtests passed；前端全量 Vitest 为 458 passed；`vue-tsc --noEmit` 与 `git diff --check` 通过。
