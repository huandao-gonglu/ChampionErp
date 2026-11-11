# 共用业务数据读取

普通查询定义一次，同时提供页面 API 和 AI 数据目录。新增普通查询在
`erp_web/business_query_composition.py::BUSINESS_QUERIES` 添加 `BusinessQuery`：名称、
用途、参数 Schema、真实业务 loader、权限及默认视图属于同一业务定义，不再另加 AI 登记。
loader 接收当前账号的 `context` 和已校验参数，业务结果仍由原 Store/service 负责。

## 入口和职责

- `services/business_queries.py` 定义普通查询及显式集合；组合根的每个定义默认生成
  `GET /api/business-queries/{name}`，已有 API 用 `http_path` 保留路径和完整结果形状。
  `http_route_units/business_query_routes.py` 从定义生成显式 handler map，无逐功能路由胶水。
- `BusinessQueries.bind(context)` 从同一组定义派生共用数据目录；页面普通 GET 读取完整
  业务结果，`POST /api/business-data/read` 和 AI 按需投影、过滤、分页及展开。
  HTTP 参数和 AI 的 `params` 使用同一 Schema；未知字段、owner 覆盖和重复标量参数拒绝读取。
- `GET /api/business-data/catalog` 和 AI 的 `business_data_catalog` 自动发现这些普通查询，
  不维护第二张 AI 资源表。HTTP 覆盖清单和主 Agent 的查询权限也由同一声明派生。
- AI 仍只有 `business_data_catalog` 和 `business_data_read` 两个薄读取入口。基础入口通过
  原生 `ToolDefinition.defer_loading=False` 保持可见；其他领域工具按需发现，原生
  ToolSearch 每次最多返回三个候选。具体读取复核资源权限；账号、店铺及草稿归属沿用
  真实 Runtime，不接收账号 owner、数据库路径或会话凭据。
- 查询引擎 `services/business_data.py` 不依赖平台、HTTP、Agent 或领域 runtime；
  普通查询声明与 HTTP 适配位于外层。显式组合不执行查询，不扫描包或依靠 import 注册。
- 工具发现、Python 执行和历史仍使用 Pydantic AI 原生 ToolSearch / CodeMode，
  没有新增 Agent 执行或恢复协议。

这是普通查询的开发入口统一，不是对任意数据库或全部 HTTP 接口的自动开放。既有查询
分批迁移；刷新、授权、配置写入及领域事务保留显式入口。新增普通查询不用再做一次 AI
接入；开发者仍须完成该查询本身的参数、业务结果与权限定义。

## 报单货代配置

- `logistics.settings` 对应原 `GET /api/logistics/settings`：读取当前空间/货代授权的
  收货地址、启用/默认状态、承接规则和合作仓；默认摘要不带完整联系人地址，按需展开可读。
  `carriers` 只是可配置枚举，`warehouses` 是合作目录，不能当作实际已启用的配置。
- `fulfillment.settings` 对应原 `GET /api/crossborderbus/settings`：读取授权状态、
  合作渠道与仓库目录、已保存默认报单规则和订单配送来源。Yandex 的收货仓/承接配置
  在前者中，不能因为本资源 `rules` 为空就断言没有配置。
- 两者均读取本地资料，不刷新远端、不自动报单。普通页面和 AI 使用完全相同的公开
  业务结果；内部 `bus_identity` / 目录 `identity` 指纹只留在领域持久化中。
- 原七类查询也统一采用普通查询声明。其他现有 focused 读取和写能力尚未全部迁移。

## 按需展开与上下文

目录默认只返回名称、用途和默认字段；指定 `resource` 才返回参数 Schema。
读取默认每页 10 项，上限 50 项。用 `params` 定位对象，`filters` 精确匹配，
`fields` 选择字段。点路径投影以点路径作为返回列名；`path` 展开字典字段或数组
下标，`$` 表示根。`available_fields` 列出当前位置可进一步读取的字段。

数据响应预算为 12,000 字符，分页不会截断单条 JSON 事实。记录过大时明确返回
`limited` 和提示，使用字段投影或展开路径读取；`next_offset` 指向尚未返回的记录。
大文本按字符位置分段，可继续读取。摘要不是可读数据上限；没有不可展开的模型专用摘要。
复杂分析用已有 Python 入口筛选结果，向模型只输出本轮需要的结论与必要样本。

现有九类查询包括运费规则/版本、Yandex 发布仓、核价模板、草稿完整内容、逐 SKU
核价明细、商品 SKU 事实及上述两类报单配置。其他现有 focused 工具保留各自的分页、平台规则与写入边界；
不能把本次接通的资源目录描述为已覆盖所有业务查询。
目录关键词按空格分词匹配；参数和路径错误携带可操作的原始业务说明与目录提示，
不能退化为“稍后重试”并迫使模型循环猜测。运费结果同时提供固定费、实重/体积重、
未知准入值的共用计费说明，避免仅凭字段名猜测业务含义。

线上真实预览还复现了 Harness 0.34.0 的输出边界缺口：脚本末尾返回
`type(...)` 时，CodeMode 返回 Python 类型对象，随后原生消息 JSON 编码失败，
不是核价失败。`PythonOutputLimits.after_tool_execute` 在官方公开输出 hook 中
调用 Pydantic Core 的 `to_jsonable_python(..., fallback=repr)`，仅将脚本对外结果
转换为可编码值，再交回原生输出限制。内核变量、嵌套业务回执、Agent run、工具循环
与重试均未改写；上游 CodeMode 自带等价结果规范化后应移除此适配。

## 核价解释依据

`draft.pricing` 读取已保存报价；传 `preview_id` 可读取尚未应用的原生核价预览，
核对其草稿归属和原有十分钟有效期，不重新核价。默认只返回售价、包装及物流摘要，
完整 `calculation_basis`、`shipping_candidates` 可继续展开。

新 Yandex 报价的 `shipping_evidence.decision` 保存实际包装、配置候选、指定/自动
选择方式及逐渠道排除事实。轻小件明确保存测量值与上限，售价超限保存实际售价。
候选证据记录采用的费率和轻小件规则；普通件仍保留“完整准入限制缺失、仅试算”的事实，
本次不扩大或猜测承运范围。

批量核价保存共同仓库的覆盖数量与每个 SKU 一件的合计费用，并保留统一选仓前的
候选报价。统一选仓后的最终依据重新计算指纹，原有发布/应用一致性校验继续有效。
历史报价没有这些新字段时保留读取能力，不事后补造排除记录；当前规则与当前包装
只能作为现状参考，不能替代历史执行依据。

## 验证

`tests/test_business_queries.py` 覆盖新普通查询一次声明后自动提供页面和 AI 查询、
报单配置同源读取、不刷新、授权隔离及原生模型的直接/Python 两条路径；
`tests/test_accounts.py` 通过真实 HTTP 验证同货代账号下不同 ERP 空间仍隔离。
`tests/test_business_data.py` 覆盖同源读取、发现/投影/分页、预算后继续展开、
权限与对象归属、预览明细、正常新增资源无需新增 AI 工具，以及经过原生对话的直接/
Python 两条读取路径。物流回归覆盖实际排除事实、指定渠道、合计选仓依据与指纹。
架构守卫防止通用查询引擎反向依赖领域或 Agent。

发布环境的真实业务问答、上下文测量和回归结果见[验收记录](business-data-verification.md)。

普通查询统一入口与报单配置的发布实测见[1.2.59 验收记录](business-query-entry-verification.md)。
