# 国际物流模块

从独立实验 `pricing-probe-20260913` 移入。模块只依赖标准库，负责模板维护、
计费重、准入筛选与平台物流查询；不读取 ERP 数据库，不管理账号授权，不获取汇率。

`ShippingModule.quote()` 是统一入口：接收平台、包装、当前授权参数、必要的换算比例
及 `price_for_shipping` 回调，返回**全部**候选及拒绝原因。模块不选择最便宜渠道。
回调用现有核价算法计算候选运费对应的售价 CNY，避免复制利润率/加价率/手动售价公式。

ERP 在 `services/pricing_shipping.py` 中调用该入口，复用本轮核价汇率，将原币报价
换成现有物流字段的 CNY/USD，按未舍入金额选择最低价，再交给原核价流程计算与保存。
Mercado 手动物流报价仍是独立能力；Ozon 统一使用 OGL，Yandex 使用已配置的发布仓方案。自动失败不回退手动值。

## 模板维护

在仓库根目录执行；`--rules-dir` 指向应用的 `AppPaths.data_dir/tariffs`。
典型本地目录是 `data/tariffs`：

```bash
.venv/bin/python -m erp_web.international_shipping --rules-dir data/tariffs --list-tariffs
.venv/bin/python -m erp_web.international_shipping --rules-dir data/tariffs --platform ozon --import-template /绝对路径/OGL费率.xlsx --effective-date 2026-08-24 --preview
.venv/bin/python -m erp_web.international_shipping --rules-dir data/tariffs --platform ozon --import-template /绝对路径/OGL费率.xlsx --effective-date 2026-08-24
```

检查预览报告后再正式导入。Yandex 使用 `--platform yandex`。
OGL 必须传已核实的 `--effective-date YYYY-MM-DD`；Yandex 可选。未来生效版本仅存档，到期重跑相同导入命令启用。
原始 XLSX、版本 JSON 和导入报告均由模块维护。旧 schema=1 快照仍可读取。Ozon 旧第三方表不再用于新核价，缺省或旧索引明确转用内置 OGL 表；显式指定旧版则要求重新核价。删除下载目录中的原始模板不会影响已导入规则。

## 计算与证据

- Ozon：`ogl.py` 按 CNY 货值与实重分组，Big/Premium Big 体积重为 cm³ ÷ 12 克。
  使用包装后数据，实重上限 35kg、最长边 200cm、CNY 货值上限 22,525。重量向上进整克，
  运输费沿用保守估算向上进到分。自送、揽收两组官方运价必须完全一致才能导入。
  按 15–200 RUB 限额分别求电子服务费的三个区间，再与真实销售佣金档及目标利润共同求解，
  选择最低达标售价。手动售价只回算、不自动抬价。按单件单包预估，不将单价冒充合包费用。
  版本、来源核实范围与结算限制见 [OGL 核价说明](../../docs/ozon-ogl-pricing.md)。
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
- 一次 SKU 批次共用同一个 `ShippingModule`，首次加载的平台费率快照固定用于本批后续规格。公共查询失败也在本批复用错误，新一轮
  创建实例后重新读取当前费率，不跨批次缓存店铺凭据。
  修改包装、成本、报价输入时继续使用项目已有核价失效机制，不新增后台监控。

## 平台接口口径

OGL 运费预估不调用仓库/配送方式接口，不推测真实仓库是否可交货。本次未修改运单、订单或仓库履约流程。

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
