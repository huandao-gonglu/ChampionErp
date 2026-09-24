# 国际物流模块

从独立实验 `pricing-probe-20260913` 移入。模块只依赖标准库，负责模板维护、
计费重、准入筛选与平台物流查询；不读取 ERP 数据库，不管理账号授权，不获取汇率。

`ShippingModule.quote()` 是统一入口：接收平台、包装、当前授权参数、必要的换算比例
及 `price_for_shipping` 回调，返回**全部**候选及拒绝原因。模块不选择最便宜渠道。
回调用现有核价算法计算候选运费对应的售价 CNY，避免复制利润率/加价率/手动售价公式。

ERP 在 `services/pricing_shipping.py` 中调用该入口，复用本轮核价汇率，将原币报价
换成现有物流字段的 CNY/USD，按未舍入金额选择最低价，再交给原核价流程计算与保存。
手动物流报价仍是独立能力；自动失败不回退手动值或内置表。

## 模板维护

在仓库根目录执行；`--rules-dir` 指向应用的 `AppPaths.data_dir/tariffs`。
典型本地目录是 `data/tariffs`：

```bash
.venv/bin/python -m erp_web.international_shipping --rules-dir data/tariffs --list-tariffs
.venv/bin/python -m erp_web.international_shipping --rules-dir data/tariffs --platform ozon --import-template /绝对路径/费率.xlsx --preview
.venv/bin/python -m erp_web.international_shipping --rules-dir data/tariffs --platform ozon --import-template /绝对路径/费率.xlsx
```

检查预览报告后再正式导入。Yandex 使用 `--platform yandex`。
可传 `--effective-date YYYY-MM-DD`；未来生效版本仅存档，到期重跑相同导入命令启用。
原始 XLSX、版本 JSON 和导入报告均由模块维护。旧实验的 schema=1 快照可直接读取，
不需要重新解释原表；删除下载目录中的原始模板不会影响已导入规则。

## 计算与证据

- Ozon：按模板各行决定使用实际重，或实际重与体积重取大，金额向上保留两位小数。仅保留真实 rFBS 仓库的
  ACTIVE 配送方式；核验重量、尺寸、电池、液体和人民币货值档。
- Yandex：使用大陆模板的每克费率和进位步长。轻小件的重量/货值阈值从模板读取。
  60/90 cm 尺寸限制不是模板字段，延续原实验政策并记录来源；液体准入未知时报错。
  普通件缺少完整准入资料，返回结果中明确保留“仅运费试算”的限制。
- Mercado：按每个销售国家的授权 `seller_id` 调用不带 ITEM_ID 的运费试算，最多
  8 轮校准售价与报价。只使用响应的 `list_cost` 和 `currency_id`，不重复扣 discount；
  零报价、缺汇率、无效授权和不收敛均停止自动报价。不同国家独立核价，不能互相比价
  后共用最低费用。内置重量运费表已删除。
- 每个 SKU 保存所选渠道、原币金额、换算比例、计费重、版本/接口来源与报价时间，
  放在既有 `calculation_basis.shipping_evidence` 中；多国家证据在
  `calculation_basis.destination_shipping`。完整候选通过 `shipping_candidates` 返回，
  不要求把所有候选长期保存。
- 一次逐 SKU 核价以首个成功结果的版本固定后续 SKU；新一轮重新读取当前版本。
  修改包装、成本、报价输入时继续使用项目已有核价失效机制，不新增后台监控。

## 平台接口口径

Ozon 使用 `/v2/warehouse/list`、`/v2/delivery-method/list`。
保留原实验的 `XY/兴远 → Xingyuan` 名称匹配，证据标记为
`active_method_name_mapping`，不能描述为平台已确认的 provider/template ID 绑定。
官方入口：[Ozon Seller API](https://docs.ozon.ru/api/seller/)。

Mercado 使用 `/users/{user_id}/shipping_options/free`，高度×宽度×长度为 cm，
重量为 g；报价币种使用响应值。请求沿用原实验的 `me2/drop_off` 口径，并记录在证据中；
`remote` 是授权业务类型，不拿来替代试算物流类型。刊登类型与包邮条件优先使用
当前销售目标配置，未提供时使用实验默认 `gold_pro/true`。
报价只是 API `all_country` 范围内的运费估计，不是最终跨境结算保证。
官方参数：[Mercado 运费试算](https://developers.mercadolibre.com.mx/es_ar/administra-proyectos-aplicaciones/costos-de-envios)。

模块不涉及 Agent 生命周期，无需新增 Pydantic AI loop、工具或协议。

现有模板的样本、边界及 ERP 链路核验结果见
[国际物流计算核验记录](../../docs/international-shipping-verification.md)。
Mercado 的接口连通性已实测，Global Selling 跨境费用一致性仍缺官方样本对照。
