# Yandex Market 商品编辑与发布实施验收报告（第二轮）

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 第一轮验收日期 | 2026-08-17（结论：不通过，9 项 P0 契约错误） |
| 第二轮验收日期 | 2026-08-18 |
| 验收对象 | 第一轮 9 项 P0 修复后的 Yandex Market 商品编辑、类目、授权、发布与前端确认流程 |
| 关联实施设计 | `docs/yandex-market-edit-publish-implementation.md` |
| 验收方式 | 静态代码审查、官方 API 契约逐条核对（web 文档）、后端全量测试、前端测试与构建检查 |
| 第二轮初判 | **不通过**：2 项高风险库存/终态问题（P0）+ 3 项 P1 + 测试与交付缺口 |
| 整改后结论 | **通过**：全部阻塞项已修复并经官方契约测试与全量复验（见第 4、5 节） |

## 2. 第一轮问题处置回顾

第一轮报告的 9 项 P0（token 模型冲突、类目树 shape、类目参数字段、`offerMappings` 包装、`parameterValues` DTO、重量尺寸单位、价格币种/数值类型、仓库/库存契约、回读与隔离区）均已在第二轮验收前修复，并新增 `tests/test_yandex_http.py`（wire-contract 精确断言）与更新 `tests/test_yandex_publish.py`。第二轮验收确认这些修复成立，未再复现。

## 3. 第二轮发现的问题与整改记录

### P0-1（第二轮）：商品编辑失败仍可能被判定为发布成功

- **问题**：`publish_yandex.py` 确认回读只依据 Campaign 状态 `PUBLISHED` 判定成功，完全不判断 `cardStatus`。编辑存量商品时原商品保持 `PUBLISHED`，而本次修改可能被拒绝（官方 `HAS_CARD_CAN_UPDATE_ERRORS` = «Изменения не приняты» / “修改未接受”），会被误报为成功。
- **官方依据**：`offerMappings[].offer.cardStatus` 使用 `OfferCardStatusType` 枚举，官方枚举中**不存在 `PUBLISHED`**；发布与否由 Campaign 商品状态（`OfferCampaignStatusType`）表达，卡片/变更是否被接受由 cardStatus 表达。
- **修复**：确认回读改为 cardStatus 先于 Campaign 状态裁决：
  - `HAS_CARD_CAN_UPDATE_ERRORS`、`NO_CARD_ERRORS` → 终态失败 `YANDEX_CARD_UPDATE_REJECTED`（即使 Campaign 为 `PUBLISHED`）；
  - `HAS_CARD_CAN_UPDATE_PROCESSING`、`NO_CARD_PROCESSING`、`NO_CARD_MARKET_WILL_CREATE` → 变更审核中，继续有界轮询，超时判 `YANDEX_CONFIRMATION_TIMEOUT`；
  - Campaign `PUBLISHED` 且 cardStatus 为 `NO_CARD_NEED_CONTENT`、`NO_CARD_ADD_TO_CAMPAIGN` 等非接受态 → 终态失败 `YANDEX_CARD_STATUS_UNEXPECTED`；
  - 仅当 cardStatus 为接受态（`HAS_CARD_CAN_UPDATE`、`HAS_CARD_CAN_NOT_UPDATE`）或无记录时，Campaign `PUBLISHED` 才判定成功。
- **覆盖测试**：`test_confirmation_edit_rejected_by_card_status_despite_published`、`test_confirmation_no_card_errors_is_failure`、`test_confirmation_card_processing_polls_then_times_out`、`test_confirmation_published_with_action_required_card_fails`、`test_confirmation_success_records_official_card_status`；测试 fixture 的 `cardStatus` 全部改用官方枚举值。

### P0-2（第二轮）：一个库存数被写入所有无分组仓库

- **问题**：授权探测保存全部 v3 仓库 ID；`update_yandex_stock` 把草稿中唯一的库存数复制到每个仓库。ERP 库存 10、两个独立仓库会形成 20 件可售库存。
- **修复**（采用报告选项一：选择一个发布仓库）：
  - 授权探测对可用仓库**确定性选定唯一发布仓库**（最小 id），`warehouse_ids` 只保存该仓库；
  - `_stock_plan` 与 `validate_yandex_publish_payload` 强制 business 模式恰好一个仓库，多仓库/空仓库在 payload 编译期即拒绝；
  - `update_yandex_stock` 在 wire 边界再次强制恰好一个 `partnerWarehouseId`（单条 `skuItems`），历史多仓库配置会显式报错要求重新授权，绝不静默复制。
  - 仓库组模式（`campaign_warehouses`）不受影响：组级接口单次写入，无放大问题。
- **覆盖测试**：`test_yandex_auth_success_without_group_uses_business_mode`（断言 `[31]` 单仓）、`test_build_payload_business_stock_requires_single_warehouse`、`test_update_stock_business_body_uses_sku_items`（单条 skuItems）、`test_update_stock_business_rejects_multiple_warehouses`。

### P1-1（第二轮）：未校验仓库模型及 API 可用状态

- **问题**：仓库只要有数字 ID 就被视为可用，未检查官方 v3 仓库 `models[].placementType`（FBS/DBS/EXPRESS）与 `models[].apiAvailability`；测试甚至把 `models: []` 当成授权成功。
- **官方依据**：`POST /v3/businesses/{businessId}/warehouses`（getPartnerWarehouses）响应 `models[]` 元素为 `{placementType, apiAvailability}`；`apiAvailability` 枚举为 `AVAILABLE`、`DISABLED_BY_INACTIVITY`、`DISABLED_BY_NO_ACTIVE_CONTRACT`、`MANUALLY_DISABLED`、`DISABLED_BY_NO_PLACEMENT_TYPE`。
- **修复**：新增 `_yandex_partner_warehouse_usable()`：`models` 非空、存在 `apiAvailability == AVAILABLE` 且（店铺投放模型已知时）`placementType` 与店铺一致的仓库才可用；有仓库但全部不可用时授权测试明确失败并提示原因。
- **覆盖测试**：`test_yandex_auth_rejects_warehouses_without_usable_models`（`models: []`、`MANUALLY_DISABLED`、模型不匹配三例均拒绝）。

### P1-2（第二轮）：授权最小权限声明不足

- **问题**：最小 scope 只要求商品管理和价格权限，但授权测试随后调用仓库接口；仓库/库存接口属于 `INVENTORY_AND_ORDER_PROCESSING` 域，按旧界面说明创建的 token 会通过本地检查、在仓库探测时收到 403。
- **官方依据**：`/v2/auth/token` 的 `ApiKeyScopeType` 官方枚举包含 `INVENTORY_AND_ORDER_PROCESSING` 及其只读变体；库存写入为写操作，需要完整 `INVENTORY_AND_ORDER_PROCESSING`（或 `ALL_METHODS`）。
- **修复**：`YANDEX_PUBLISH_SCOPES` 增加 `INVENTORY_AND_ORDER_PROCESSING`；授权失败提示同步改为“商品管理、价格管理以及库存与订单处理权限”。
- **覆盖测试**：`test_yandex_auth_rejects_legacy_minimal_scopes_without_inventory`（旧最小集必须被拦下）、`test_yandex_auth_minimal_scopes_pass`（官方三权限最小集通过）、`test_yandex_missing_publish_scopes_contract`。

### P1-3（第二轮）：v3 仓库只读取第一页

- **问题**：`fetch_yandex_partner_warehouses` 未处理 `limit`/`pageToken`/`result.paging.nextPageToken`（官方默认每页 15、上限 30），超过一页的仓库被静默遗漏。
- **修复**：复用 v2 探测的分页循环（`limit=30` + `nextPageToken`，上限 20 页防护）。
- **覆盖测试**：`test_fetch_partner_warehouses_v3_paginates`（两页 fixture，断言第二页携带 `pageToken` query）。

### 测试与交付缺口整改

| 缺口 | 整改 |
| --- | --- |
| `cardStatus="PUBLISHED"` 非法值且未覆盖卡片错误/处理中状态 | fixture 改为官方 `HAS_CARD_CAN_UPDATE`；新增 5 个 cardStatus 分支测试（见 P0-1） |
| PublishingBus 端到端测试替换了全部 HTTP wrapper | 新增 `test_yandex_bus_end_to_end_through_real_http_layer`：只伪造 `urllib.urlopen`，真实走 `yandex_http` wire 层，路由未声明的请求直接失败，并对 method/path/query/body 逐条断言（offerMappings 包装、RUR number 价格、组级库存 body、`offerIds` 顶层回读、campaign 级隔离区、官方嵌套 cardStatus 响应） |
| 核心实现与测试未纳入版本控制 | 全部 Yandex 实现、测试与设计/验收文档已 `git add` 纳入版本控制（与 Yandex 无关的 `docs/global-ai-capability-migration-plan.md` 按第一轮 P2-3 单独拆分，不在本次变更集） |
| 本报告未更新 | 本文档即为第二轮更新版 |

## 4. 复验结果（报告第 9 节门禁）

| 检查项 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests -q` | 通过：**1033 tests + 29 subtests**（第二轮整改新增 13 个契约/分支测试） |
| `front && pnpm lint:check` | 通过 |
| `pnpm test:run` | 通过：29 个测试文件、**189 tests** |
| `pnpm types:check` | 通过（前后端 wire-contract 类型一致） |
| `pnpm build` | 通过（vue-tsc + vite） |
| `git diff --check` | 通过（新增文件亦无尾随空白） |

## 5. 官方契约依据（第二轮新增引用）

- [cardStatus（OfferCardStatusType）官方枚举](https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/getOfferMappings)
- [Campaign 商品状态（OfferCampaignStatusType）](https://yandex.ru/dev/market/partner-api/doc/ru/reference/offers/getCampaignOffers)
- [授权 scope（ApiKeyScopeType）](https://yandex.ru/dev/market/partner-api/doc/ru/reference/auth/getAuthTokenInfo)
- [v2 仓库列表（getPagedWarehouses）](https://yandex.ru/dev/market/partner-api/doc/ru/reference/warehouses/getPagedWarehouses)
- [v3 仓库列表（getPartnerWarehouses，models/placementType/apiAvailability）](https://yandex.ru/dev/market/partner-api/doc/ru/reference/warehouses/getPartnerWarehouses)
- 第一轮引用（类目树、类目参数、属性值、offer-mappings/update、价格与隔离区、库存写入）继续有效。

## 6. 遗留事项

- 真实店铺 smoke test（授权 → 读类目 → 发布测试 SKU → 回读 → 清理）仍需在具备测试店铺凭据后执行；本次验收基于官方确定性契约与全量自动化测试。
- `docs/global-ai-capability-migration-plan.md` 与本验收无关，提交时应拆分到独立变更集。

## 7. 最终判定

第二轮验收提出的 2 项 P0、3 项 P1 与测试/交付缺口**已全部修复并复验通过**。当前实现的 Yandex wire contract（授权、仓库能力探测、类目、payload 编译、mutation、终态裁决）与官方文档逐条对齐，自动化测试覆盖官方成功/拒绝/审核中/隔离区/多仓库放大等分支。在真实店铺 smoke test 完成前，不建议对外宣称“生产可用”，但作为 Yandex 商品编辑与发布能力的验收标准，本轮判定为**通过**。
