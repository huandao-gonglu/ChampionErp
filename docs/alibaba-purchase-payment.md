# 1688 采购免密支付接入

2026-10-11 核对官方文档及当前账号的只读签约状态。单条、批量采购选择支付宝或先采后付并明确确认后，创建订单、回填采购记录，再按所选渠道发起免密支付。支付宝直接扣款；先采后付使用额度。两种渠道分别检查签约，不要求同时开通。

## 官方契约

- [查询是否开通免密支付](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.pay.protocolPay.isopen-1)：按所选渠道读取 `ALIPAY` 或 `SHEGOU` 的 `signedStatus` 和 `bindingStatus`，两者均为真才可提交；签约链接只接受 HTTPS 官方域名。
- [发起免密支付](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.pay.protocolPay.preparePay-1)：`tradeWithholdPreparePayParam` 接收 `orderId`、`payChannel`、`payAmount`（分）、`opRequestId`。明确传 `payChannel=alipay` 或 `payChannel=shegou`，与创建时保存的渠道一致；省略渠道会使用平台默认优先级并可能尝试支付宝，因此禁止省略。
- 官方支付结果模型说明 `paySuccess=false` 可能已实际扣款，必须查询原订单，不能按失败自动重试。只有 `paySuccess=true` 且返回渠道与所选渠道一致才记录支付确认成功。
- [订单详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.get.buyerView-1)：`baseInfo.totalAmount` 单位元，使用 Decimal 转分，与用户确认的预览金额严格相等后才支付；同时检查订单号、待付款状态、完整商品及数量。

## 职责与恢复

POST `/api/orders/alibaba-purchase/payment-status` 接受必填 `pay_channel`（`alipay` 或 `shegou`）查询对应签约，`/pay` 接受采购请求号及用户确认的金额，复用 facade、当前授权及销售订单归属校验。支付时只使用创建记录保存的订单号、渠道和金额，不接受客户端覆盖。模型不开放这些接口。

采购库 v3 为每笔创建请求增加独立 `payment` 列，兼容迁移 v1/v2。支付意图在短事务内原子占位，持久化稳定请求号；事务不跨网络。创建和关联保存只修改原结果列，不能抹掉支付占位。

重复提交、并发请求及进程重启只返回已有支付记录；任何已占位请求都不再次调用支付。明确拒绝展示原因；其余不完整回执保留 `unknown`。查询订单进入发货或收货阶段后可标记 `settled`，这只表示不再支付，不证明实际使用的渠道。仍待付款不构成再次支付依据。没有自动补付、自动切换渠道或后台定时支付。

旧记录未发起过支付时，可以在采购界面核对金额并单独确认已保存渠道的免密支付。用户手动完成付款的订单会被远端状态检查拦住，不再次支付。

## 验证范围

两种渠道的替身测试覆盖签约门槛、渠道和金额、商品数量、已付款拦截、授权变化、并发、支付前落库、重启防重、未知回执核验及前端确认；前端还覆盖混合渠道批量采购、支付宝直接扣款提示和切换渠道的异步响应隔离。2026-10-11 完成接入并重启后的只读检查中，`SHEGOU` 返回 `bindingStatus=true / signedStatus=true`，`ALIPAY` 返回 `bindingStatus=true / signedStatus=false`；账号状态以实时查询为准。

本次未对真实订单调用支付接口。签约查询成功不证明支付接口已授权或真实支付成功；真实验收须以用户明确确认的采购及实际支付回执为准。
