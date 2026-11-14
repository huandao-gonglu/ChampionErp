# 1688 跨境 ERP 解决方案 API 与消息目录

适用方案为「跨境ERP/独立站SaaS数字化解决方案」（方案编号 `1697015308755`），对应服务商应用 `4848173`。本项目也支持管理员切换回企业自用应用 `1938727`；本目录的跨境方案清单不代表自用应用获得同等权限。清单整理自 2026-11-07 提供的平台列表，接口权限以所选应用在相应环境下的「已获得能力」为准。[1688 官方 API 和消息列表](https://open.1688.com/solution/solutionDetail.htm?solutionKey=1697015308755#apiAndMessageList)

按标识去重，清单包含 **198 个 API** 和 **41 种消息**。同一分类内完全相同的行合并展示，跨分类条目和同一标识的不同名称均保留。

## ERP 已接入的接口

跨境应用使用以下 11 个业务 API；企业自用应用的商品详情改用 `com.alibaba.product / alibaba.product.get`，其他采购、支付及物流接口沿用表中协议。表中链接可打开官方接口文档，查看请求参数和响应字段。[授权配置](alibaba-authorization.md) · [商品、订单与物流客户端](../erp_web/services/alibaba_api_client.py) · [采购与支付客户端](../erp_web/services/alibaba_self_purchase_client.py)

| 业务 | API 标识 | 官方文档 |
| --- | --- | --- |
| 多语言商详 | `product.search.queryProductDetail` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.fenxiao.crossborder:product.search.queryProductDetail-1) |
| 订单详情查看(买家视角) | `alibaba.trade.get.buyerView` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.get.buyerView-1) |
| 获取交易订单的物流信息(买家视角) | `alibaba.trade.getLogisticsInfos.buyerView` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.logistics:alibaba.trade.getLogisticsInfos.buyerView-1) |
| 获取交易订单的物流跟踪信息(买家视角) | `alibaba.trade.getLogisticsTraceInfo.buyerView` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.logistics:alibaba.trade.getLogisticsTraceInfo.buyerView-1) |
| 买家获取保存的收货地址信息列表 | `alibaba.trade.receiveAddress.get` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.receiveAddress.get-1) |
| 创建订单前预览数据接口 | `alibaba.createOrder.preview` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.createOrder.preview-1) |
| 创建采购订单 | `alibaba.trade.fastCreateOrder` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.fastCreateOrder-1) |
| 订单列表查看(买家视角) | `alibaba.trade.getBuyerOrderList` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.getBuyerOrderList-1) |
| 组合收银台url获取 | `alibaba.trade.grouppay.url.get` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.grouppay.url.get-1) |
| 查询是否开通免密支付 | `alibaba.trade.pay.protocolPay.isopen` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.pay.protocolPay.isopen-1) |
| 发起免密支付 | `alibaba.trade.pay.protocolPay.preparePay` | [接口详情](https://open.1688.com/api/apidocdetail.htm?id=com.alibaba.trade:alibaba.trade.pay.protocolPay.preparePay-1) |

## API 分类索引

万邦为独立接入方案，不增加官方应用权限。`1688/custom` 转发商品、采购、支付及物流，收银台使用 `alibaba.alipay.url.get`；买家基本信息使用 `com.alibaba.account / alibaba.account.basic`。企业凭据仅由管理员维护，业务使用当前用户已有的通道凭据；万邦个人在线授权待正式协议接入，Key 的默认买家连通不代表用户已授权，见 [1688 授权与接入](alibaba-authorization.md)。

[会员](#api-会员) · [工具](#api-工具) · [商机](#api-商机) · [类目](#api-类目) · [商品](#api-商品) · [国内ERP](#api-国内erp) · [选品铺货](#api-选品铺货) · [跨境选品助手](#api-跨境选品助手) · [产品开发](#api-产品开发) · [订单](#api-订单) · [支付](#api-支付) · [物流](#api-物流) · [官方退上门取件](#api-官方退上门取件) · [CPS](#api-cps) · [采购金支付](#api-采购金支付) · [回传数据](#api-回传数据) · [退货退款](#api-退货退款) · [消息](#api-消息) · [铺货agent](#api-铺货agent) · [询盘](#api-询盘) · [88生意通](#api-88生意通) · [发票](#api-发票)

<a id="api-会员"></a>

### 会员

| 名称 | API 标识 |
| --- | --- |
| 批量添加子账号授权 | `subaccount.auth.add` |
| 批量取消子账号授权 | `subaccount.auth.cancel` |
| 批量查询子账号授权 | `subaccount.auth.list` |
| 1688会员注册 | `account.user.register` |
| 跨境场景获取非授权用户的基本信息 | `alibaba.account.agent.crossBasic` |

<a id="api-工具"></a>

### 工具

| 名称 | API 标识 |
| --- | --- |
| 拉取商品池中商品数据 | `pool.product.pull` |
| 查询商品池中商品总数 | `pool.product.total` |
| 用户loginId加密转换为Openuid接口 | `loginid.openuid.encrypt` |
| 获取唤起旺旺聊天的链接 | `account.wangwangUrl.get` |
| Openuid转换解密为旺旺昵称接口（仅可使用于用户唤起旺旺） | `wangwangnick.openuid.decrypt` |
| 买卖家分销关系添加 | `alibaba.fenxiao.relationadd` |

<a id="api-商机"></a>

### 商机

| 名称 | API 标识 |
| --- | --- |
| 商品热搜词 | `product.search.topKeyword` |
| 查询榜单列表 | `product.topList.query` |
| 获取商品每日销售数量趋势 | `product.analyze.getPerdaySellQuantityTrend` |
| 获取商品30天价格中位数指标趋势 | `product.analyze.getThirtyDayMedianPriceTrend` |
| 获取商品90天复购率指标趋势 | `product.analyze.getRepurchaseRateTrend` |
| 获取商品每日销售数量趋势（新） | `product.analyze.getPerdaySellQuantityTrendNew` |
| 获取商品30天价格中位数指标趋势（新） | `product.analyze.getThirtyDayMedianPriceTrendNew` |
| 获取商品90天复购率指标趋势(新） | `product.analyze.getRepurchaseRateTrendNew` |

<a id="api-类目"></a>

### 类目

| 名称 | API 标识 |
| --- | --- |
| 根据类目ID查询多语言类目 | `category.translation.getById` |
| 根据类目名称查询多语言类目 | `category.translation.getByKeyword` |
| 获取叶子类目属性 | `alibaba.category.attribute.get` |

<a id="api-商品"></a>

### 商品

| 名称 | API 标识 |
| --- | --- |
| 多语言关键词搜索 | `product.search.keywordQuery` |
| 多语言图搜 | `product.search.imageQuery` |
| 多语言商品店搜 | `product.search.querySellerOfferList` |
| 多语言商详 | `product.search.queryProductDetail` |
| 商品推荐 | `product.search.offerRecommend` |
| 多语言搜索导航 | `product.search.keywordSNQuery` |
| 相关性商品推荐 | `product.related.recommend` |
| 上传图片获取imageId | `product.image.upload` |
| 通过商品领取最优化的优惠券 | `coupon.optimal.claim` |
| 跨境场景根据关键字推荐商品 | `alibaba.product.suggest.crossBorder` |
| 获取已购买过商家的商品简单信息 | `alibaba.product.simple.get` |
| 增加跨境关注商品 | `product.kjdistribute.addRelation` |

<a id="api-国内erp"></a>

### 国内ERP

| 名称 | API 标识 |
| --- | --- |
| 推荐搜索跟采购商有关的供应商列表 | `alibaba.search.relation.supply` |
| 获取已购买过商家的商品简单信息 | `alibaba.product.simple.get` |
| 获取已购买过的商家信息 | `alibaba.member.getRelationUserInfo` |
| 物流公司列表-自联物流 | `alibaba.logistics.OpQueryLogisticCompanyList.offline` |
| 获取分销商品详情接口 | `alibaba.fenxiao.productInfo.get` |
| 添加分销店铺(买家) | `alibaba.fenxiao.buyer.outshop.add` |
| 删除分销店铺(买家) | `alibaba.fenxiao.buyer.outshop.delete` |
| 添加分销商品关系(买家) | `alibaba.fenxiao.buyer.outproduct.relation.add` |
| 查询分销商品关系(买家) | `alibaba.fenxiao.buyer.outproduct.relation.get` |
| 删除分销商品关系(买家) | `alibaba.fenxiao.buyer.outproduct.relation.delete` |
| 获取授权用户的基本信息 | `alibaba.account.basic` |
| 设置用户铺货次数 | `product.distribute.cnt.put` |
| 获取用户铺货次数 | `product.distribute.cnt.get` |
| 分销生成dkey | `dkey.get` |
| 关注商品 | `alibaba.product.follow` |
| 铺货单列表获取 | `fenxiao.distributebill.getList` |
| 铺货单清除 | `fenxiao.distributebill.removeall` |
| 图搜内贸代发商品 | `alibaba.public.image.similar.offer.search` |
| 商品关键词搜索接口 | `product.keyword.search` |
| 获取交易地址代码表详情 | `alibaba.trade.addresscode.get` |
| 获取交易地址的下一级信息 | `alibaba.trade.addresscode.getchild` |
| 获取营销活动价格等活动信息 | `alibaba.cps.queryOfferDetailActivity` |
| 组合收银台url获取 | `alibaba.trade.grouppay.url.get` |
| 订购的订单列表 | `alibaba.app.pieceorder.get` |
| app 在服务市场被订购的订单列表 | `app.order.get` |
| 获取某个应用最近一个月的到期订单信息 | `app.expire.get` |
| 铺货单offer列表获取(入端插件) | `alibaba.fenxiao.chosen.offerlist.get` |
| 清空铺货单 | `alibaba.fenxiao.chosen.offerlist.removeall` |
| 根据关键字搜索类目 | `alibaba.category.searchByKeyword` |
| 分销严选同款商品匹配 | `alibaba.pifatuan.product.match.get` |
| 分销严选商品列表筛选条件枚举值查询 | `jxhy.productFilter.get` |
| 商品sku信息查询 | `product.skuinfo.get` |
| 添加下游热销商品记录 | `alibaba.feedback.out.product.add` |

<a id="api-选品铺货"></a>

### 选品铺货

| 名称 | API 标识 |
| --- | --- |
| 跨境场景获取商品详情 | `alibaba.cross.productInfo` |
| 跨境场景下将商品加入铺货列表 | `alibaba.cross.syncProductListPushed` |
| 同步铺货结果 | `alibaba.product.push.syncPushProductResult` |
| 跨境场景获取商品列表 | `alibaba.cross.productList` |
| 解除关注商品 | `alibaba.product.unfollow.crossborder` |
| 根据类目Id查询类目 | `alibaba.category.get` |
| 关注商品 | `alibaba.product.follow.crossborder` |
| 【关系】分销商-查询供应商列表 | `alibaba.relation.querySuppliers` |
| 分销严选商品查询标获取 | `alibaba.pifatuan.product.search.tag.list` |
| 分销严选商品列表查询 | `alibaba.pifatuan.product.list` |
| 分销严选商品详情批量查询 | `alibaba.pifatuan.product.detail.list` |
| 分销严选商品列表查询 | `jxhy.product.getPageList` |
| 跨境图片搜索 | `product.search.imageQueryBasic` |
| 国内分销词搜 | `product.keywords.search` |

<a id="api-跨境选品助手"></a>

### 跨境选品助手

| 名称 | API 标识 |
| --- | --- |
| 1688跨境选品页token生成 | `alibaba.lpAuthToken.generate` |
| 1688跨境选品助手创建二级isv | `alibaba.lpIsvInfo.createSubcustomer` |

<a id="api-产品开发"></a>

### 产品开发

| 名称 | API 标识 |
| --- | --- |
| 跨境产品开发工具低价开发接口 | `alibaba.product.genDijiaFxUrl` |
| 跨境产品开发工具同款开发接口 | `alibaba.product.genTongkuanFxUrl` |

<a id="api-订单"></a>

### 订单

| 名称 | API 标识 |
| --- | --- |
| 跨境订单创建 | `alibaba.trade.createCrossOrder` |
| 订单详情查看(买家视角) | `alibaba.trade.get.buyerView` |
| 获取交易订单的物流信息(买家视角) | `alibaba.trade.getLogisticsInfos.buyerView` |
| 获取交易订单的物流跟踪信息(买家视角) | `alibaba.trade.getLogisticsTraceInfo.buyerView` |
| 取消交易 | `alibaba.trade.cancel` |
| 订单列表查看(买家视角) | `alibaba.trade.getBuyerOrderList` |
| 创建订单前预览数据接口 | `alibaba.createOrder.preview` |
| 修改订单备忘 | `alibaba.order.memoAdd` |
| 获取子账号列表 | `alibaba.subAccount.list` |
| 买家补充订单留言接口 | `alibaba.trade.addFeedback` |
| 买家确认收货 | `trade.receivegoods.confirm` |
| 买家删除已关闭的订单 | `trade.order.buyerdelete` |
| 创建采购订单 | `alibaba.trade.fastCreateOrder` |
| 买家申请修改收货地址 | `order.receiveAddress.buyerUpdate` |
| 复购合约查询 | `repurchase.contract.get` |

<a id="api-支付"></a>

### 支付

| 名称 | API 标识 |
| --- | --- |
| 批量获取订单的支付链接 | `alibaba.alipay.url.get` |
| 获取使用跨境宝支付的支付链接 | `alibaba.crossBorderPay.url.get` |
| 获取使用诚e赊支付的支付链接 | `alibaba.creditPay.url.get` |
| 买家查看获得的所有账期授信 | `alibaba.accountPeriod.list.buyerView` |
| 查询订单可以支持的支付渠道 | `alibaba.trade.payWay.query` |
| 查询是否开通免密支付 | `alibaba.trade.pay.protocolPay.isopen` |
| 发起免密支付 | `alibaba.trade.pay.protocolPay.preparePay` |
| 交易订单支付咨询 | `trade.orderpay.analysis` |

<a id="api-物流"></a>

### 物流

| 名称 | API 标识 |
| --- | --- |
| 商品中国国内运费预估 | `product.freight.estimate` |
| 根据运单号或无主件码查询外部订单ID | `logistics.order.getOutOrderId` |
| 运费险信息查询 | `shipping.insurance.get` |
| 获取物流模板详情 | `alibaba.logistics.myFreightTemplate.list.get` |
| 买家获取保存的收货地址信息列表 | `alibaba.trade.receiveAddress.get` |
| 根据地址解析地区码 | `alibaba.trade.addresscode.parse` |
| 查询卖家混批设置 | `alibaba.trade.OpQueryMarketingMixConfig` |
| 物流公司列表-所有的物流公司 | `alibaba.logistics.OpQueryLogisticCompanyList` |
| 海外地址解析 | `trade.address.parseText` |
| 催卖家发货 | `logistics.delivery.urge` |

<a id="api-官方退上门取件"></a>

### 官方退上门取件

| 名称 | API 标识 |
| --- | --- |
| 创建退货官方物流上门揽订单 | `refundofficialdelivery.order.create` |
| 退货官方物流上门揽订单修改 | `refundofficialdelivery.order.modify` |
| 退货官方物流上门揽订单详情获取 | `refundofficialdelivery.order.get` |
| 取消退货官方物流上门揽订单 | `refundofficialdelivery.order.cancel` |
| 官方退货上门揽方案查询 | `refundofficialdelivery.solution.get` |

<a id="api-cps"></a>

### CPS

| 名称 | API 标识 |
| --- | --- |
| 获取媒体推广位信息 | `alibaba.cps.listMediaInfo` |
| 获取联盟offer列表 | `alibaba.cps.listOfferPageQuery` |
| 获取联盟活动列表 | `alibaba.cps.listActivityPageQuery` |
| 获取联盟商家列表 | `alibaba.cps.listShopPageQuery` |
| 批量生成联盟推广url点击信息 | `alibaba.cps.genClickUrl` |
| 获取个性化推荐的cps商品(含推广链接) | `alibaba.cps.getCpsRecommendOfferList` |
| 获取同款推荐的商品(cps品含推广链接) | `alibaba.cps.getCpsRecommendSameOfferList` |
| 批量生成推广链接服务(部分条件) | `alibaba.cps.generateIntroduceUrlByParam` |
| 生成平价精选搜索页的推广链接 | `alibaba.cps.genSearchPjjxIntroduceUrlByKeyword` |
| 联盟账单列表查询 | `alibaba.cps.tradeBillList` |
| 获取联盟结算明细列表(按月) | `alibaba.cps.listCPSSettleInfoDetail` |
| 获取联盟结算摘要账单 | `alibaba.cps.getCPSSettleSummaryInfo` |
| 网盟账单列表查询(下线，请使用【联盟账单列表查询】接口) | `alibaba.cps.webUnionTradeBillList` |
| 查询实时uv，muv接口 | `alibaba.cps.queryRealtimeVisitor` |
| 查询订单的汇总信息 | `alibaba.cps.queryTradeInfoSum` |
| 关注商品 | `alibaba.product.follow` |

<a id="api-采购金支付"></a>

### 采购金支付

| 名称 | API 标识 |
| --- | --- |
| 采购金批量预充值支付咨询 | `paycredit.batchFundCharge.decison` |
| 采购金批量预充值支付申请 | `paycredit.batchFundCharge.apply` |
| 采购金预充值支付状态查询 | `paycredit.batchFundCharge.status` |
| 采购金订单核销 | `paycredit.orderVerification.pay` |
| 采购金预充值支付退款 | `paycredit.fundCharge.refund` |
| 采购金预充值支付退款状态查询 | `paycredit.refundCharge.status` |

<a id="api-回传数据"></a>

### 回传数据

| 名称 | API 标识 |
| --- | --- |
| 回传机构真实用户订单和1688订单的映射关系 | `order.relation.write` |
| 国家站物流单回传 | `trade.cross.logisticsOrderSync` |
| 下游销售订单同步 | `trade.cross.orderSync` |
| 保存账号所属业务线 | `account.business.save` |
| 店铺数据的新绑上报 | `global.shop.bind` |
| 商品操作(上架/下架到三方渠道店铺中) | `global.product.operation` |
| 商品售出通知 | `global.order.notify` |

<a id="api-退货退款"></a>

### 退货退款

| 名称 | API 标识 |
| --- | --- |
| 查询退款单详情-根据订单ID（买家视角） | `alibaba.trade.refund.OpQueryBatchRefundByOrderIdAndStatus` |
| 退款单操作记录列表（买家视角） | `alibaba.trade.refund.OpQueryOrderRefundOperationList` |
| 查询退款单详情-根据退款单ID（买家视角） | `alibaba.trade.refund.OpQueryOrderRefund` |
| 查询退款单列表(买家视角) | `alibaba.trade.refund.buyer.queryOrderRefundList` |
| 根据理赔单或保险单号查询对应的订单号 | `alibaba.trade.queryOrderByInsure` |
| 创建退款退货申请 | `alibaba.trade.createRefund` |
| 查询退款退货原因（用于创建退款退货） | `alibaba.trade.getRefundReasonList` |
| 上传退款退货凭证 | `alibaba.trade.uploadRefundVoucher` |
| 买家提交退款货信息 | `alibaba.trade.refund.returnGoods` |
| 取消退款退货申请 | `alibaba.trade.cancelRefund` |
| 申请退款时查询最大可退费用 | `alibaba.trade.getMaxRefundFee` |
| 查询商家退货地址 | `refund.address.get` |

<a id="api-消息"></a>

### 消息

| 名称 | API 标识 |
| --- | --- |
| 失败消息批量确认 | `push.message.confirm` |
| 查询式获取失败的消息列表 | `push.query.messageList` |
| 游标式获取失败的消息列表 | `push.cursor.messageList` |
| 订单列表查看(卖家视角) | `alibaba.trade.getSellerOrderList` |
| 物流发货-自己联系物流发货 | `alibaba.logistics.OpDeliverySendOrder.offline` |
| 物流发货-无需物流 | `alibaba.logistics.OpDeliverySendOrder.dummy` |

<a id="api-铺货agent"></a>

### 铺货agent

| 名称 | API 标识 |
| --- | --- |
| 用户授权绑定信息回调（用于跨境素材Agent的铺货ERP授权场景） | `global.agent.auth` |
| 按照铺货素材ID查询铺货详细 | `material.query.sync` |

<a id="api-询盘"></a>

### 询盘

| 名称 | API 标识 |
| --- | --- |
| 牛顿云-创建长程任务 | `newtoncloud.task.create` |
| 牛顿云-查询任务服务 | `newtoncloud.task.get` |
| 牛顿云-查询任务列表 | `newtoncloud.task.list` |
| 牛顿云-终止任务 | `newtoncloud.task.kill` |
| 牛顿云-查询任务表格服务 | `newtoncloud.task.fetch` |
| 牛顿云-恢复任务 | `newtoncloud.task.resume` |
| 牛顿云-上传文件 | `newtoncloud.file.upload` |
| 牛顿云-查询可用模型档位列表 | `newtoncloud.model.list` |
| 牛顿云-查询批量询盘结果 | `newtoncloud.batchInquiry.getResult` |
| 牛顿云-查询积分详情 | `newtoncloud.points.query` |

<a id="api-88生意通"></a>

### 88生意通

| 名称 | API 标识 |
| --- | --- |
| 88生意通买家起草采购单 | `syt.buyer.draftPurchaseOrder` |
| 88生意通确认交易完成 | `syt.contract.confirm` |
| 88生意通合同作废 | `syt.contract.invalid` |
| 88生意通合同退款申请 | `syt.contract.refund` |
| 88生意通合同支付获取资金管理专户 | `syt.contract.pay` |
| 88生意通万里汇转账支付-仅支持万里汇B2C账号 | `syt.contract.payTransfer` |
| 88生意通买家确认采购单 | `syt.buyer.confirmPurchaseOrder` |
| 88生意通买家查询采购单详情 | `syt.buyer.queryContractDetail` |
| 88生意通查询客户是否已认证 | `syt.customer.queryUserAuthStatus` |
| 88生意通 买家支付获取收银台 URL | `syt.contract.pay` |

<a id="api-发票"></a>

### 发票

| 名称 | API 标识 |
| --- | --- |
| 分页查询买家抬头列表 | `trade.invoiceTitle.getPageList` |
| 查询订单可开票金额 | `trade.invoiceAmount.getList` |
| 分页查询发票申请列表（买家视角） | `trade.invoiceApply.getPageListBuyerView` |
| 查询交易单下关联的所有开具的发票信息（买家视角） | `trade.invoice.getListBuyerView` |
| 买家申请开票 | `trade.invoice.apply` |
| 合并开票咨询 | `trade.invoice.consult` |
| 提交合并开票 | `trade.invoice.mergeapply` |
| 基于交易单或发票申请单查询合单关联关系 | `trade.invoice.sellerqueryrelatedorders` |
| 买家发票申请单分页查询（新） | `trade.invoice.queryBuyerApplyList` |

## 消息 分类索引

[交易](#消息-交易) · [商品](#消息-商品) · [物流](#消息-物流) · [跨境采购助手](#消息-跨境采购助手) · [88生意通](#消息-88生意通) · [询盘](#消息-询盘) · [发票](#消息-发票)

<a id="消息-交易"></a>

### 交易

| 名称 | 消息标识 |
| --- | --- |
| 1688创建订单（买家视角）/order created (buyer view) | `ORDER_BUYER_VIEW_BUYER_MAKE` |
| 1688修改订单价格（买家视角）/order price modification (buyer view) | `ORDER_BUYER_VIEW_ORDER_PRICE_MODIFY` |
| 1688交易付款（买家视角）/1688 transaction payment (buyer view) | `ORDER_BUYER_VIEW_ORDER_PAY` |
| 1688订单发货（买家视角）/1688 order delivery (buyer view) | `ORDER_BUYER_VIEW_ANNOUNCE_SENDGOODS` |
| 1688订单部分发货（买家视角）/Partial delivery of 1688 order (buyer view) | `ORDER_BUYER_VIEW_PART_PART_SENDGOODS` |
| 1688订单确认收货（买家视角）/order receipt confirmation (buyer view) | `ORDER_BUYER_VIEW_ORDER_COMFIRM_RECEIVEGOODS` |
| 1688交易成功（买家视角） | `ORDER_BUYER_VIEW_ORDER_SUCCESS` |
| 1688买家关闭订单（买家视角）/buyer closing order (buyer view) | `ORDER_BUYER_VIEW_ORDER_BUYER_CLOSE` |
| 1688卖家关闭订单（买家视角）/seller closing order (buyer view) | `ORDER_BUYER_VIEW_ORDER_SELLER_CLOSE` |
| 1688运营后台关闭订单（买家视角） | `ORDER_BUYER_VIEW_ORDER_BOPS_CLOSE` |
| 1688订单售中退款（买家视角） | `ORDER_BUYER_VIEW_ORDER_BUYER_REFUND_IN_SALES` |
| 1688订单售后退款（买家视角） | `ORDER_BUYER_VIEW_ORDER_REFUND_AFTER_SALES` |
| 1688订单阶段付款（买家视角） | `ORDER_BUYER_VIEW_ORDER_STEP_PAY` |
| 1688订单批量支付状态同步消息 | `ORDER_BATCH_PAY` |
| 1688订单备注修改（买家视角）/order remarks modification (buyer view) | `ORDER_BUYER_VIEW_MODIFY_MEMO` |
| 商家修改订单地址(买家视角) | `ORDER_BUYER_VIEW_ORDER_SELLER_MODIFY_ADRESS` |
| 采购金预充值支付结果 | `FENXIAO_PAY_CREDIT_FUND_CHARGE_RESULT` |
| 分销商品铺货消息 | `PRODUCT_FENXIAO_ADD_SUPPLY` |
| 商品变更消息(包含所有商品变更动作) | `PRODUCT_PRODUCT_CHANGE` |
| 采购金申请退款结果 | `FENXIAO_PAY_CREDIT_FUND_CHARGE_REFUND_RESULT` |

<a id="消息-商品"></a>

### 商品

| 名称 | 消息标识 |
| --- | --- |
| 1688产品下架（关系用户视角） | `PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE` |
| 1688产品新增或修改（关系用户视角） | `PRODUCT_RELATION_VIEW_PRODUCT_NEW_OR_MODIFY` |
| 1688产品删除（关系用户视角） | `PRODUCT_RELATION_VIEW_PRODUCT_DELETE` |
| 一键铺货消息 | `PRODUCT_PRODUCT_CROSSBOARD_INFORM` |
| 1688产品上架（关系用户视角） | `PRODUCT_RELATION_VIEW_PRODUCT_REPOST` |
| 1688商品库存变更消息（关系用户视角） | `PRODUCT_PRODUCT_INVENTORY_CHANGE` |
| 1688产品审核下架（关系用户视角） | `PRODUCT_RELATION_VIEW_PRODUCT_AUDIT` |
| 跨境设为货源 | `CROSSBOARD_CROSSBOARD_ADD_SUPPLY` |
| 精选货源商品价格变动消息 | `PRODUCT_PFT_OFFER_PRICE_MODIFY` |
| 精选货源商品下架消息 | `PRODUCT_PFT_OFFER_QUIT` |

<a id="消息-物流"></a>

### 物流

| 名称 | 消息标识 |
| --- | --- |
| 物流单状态变更（买家视角） | `LOGISTICS_BUYER_VIEW_TRACE` |
| 物流单号修改消息 | `LOGISTICS_MAIL_NO_CHANGE` |

<a id="消息-跨境采购助手"></a>

### 跨境采购助手

| 名称 | 消息标识 |
| --- | --- |
| 跨境采购助手一键铺货消息 | `CROSSBOARD_LP_DISTRIBUTION` |

<a id="消息-88生意通"></a>

### 88生意通

| 名称 | 消息标识 |
| --- | --- |
| 采购单/合同拒绝消息通知 | `SYT_CONTRACT_REJECT` |
| 新增交易存证通知消息 | `SYT_ADD_CONTRACT_CONTENT` |
| 合同或采购单退款结果消息 | `SYT_REFUND_FINISH_NOTICE` |
| 合同/采购单双方支付成功消息通知 | `SYT_CONTRACT_PAY_SUCCESS` |
| 合同/采购单双方签署成功通知 | `SYT_CONTRACT_SIGN_SUCCESS` |
| 合同或采购单待签署消息 | `SYT_CONTRACT_WAIT_SIGN` |

<a id="消息-询盘"></a>

### 询盘

| 名称 | 消息标识 |
| --- | --- |
| 牛顿云任务执行通知队列 | `AGENT_NEWTON_CLOUD_TASK_NOTIFY` |

<a id="消息-发票"></a>

### 发票

| 名称 | 消息标识 |
| --- | --- |
| 1688发票变更消息 | `ORDER_CBU_INVOICE_CHANGE_EVENT` |
