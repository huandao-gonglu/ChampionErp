# 类目 Provider 统一与草稿 Schema 分离实施计划

## 1. 文档信息

- 状态：实施中（2026-08-23 重新验收整改）
- 日期：2026-08-22
- 触发对话：`conversation_global_chat_c890e88eeb894aadae091e0ce6c8fc48`
- 触发事故：turn claim `claim_941360b4268e4b4cba86384965130cdb`
  以 `TOOL_OUTPUT_TOO_LARGE` 失败，工具输出 344,808 字节，超过 262,144
  字节上限，trace 为 `ebbccd15cab5eff365d3e79dd2bffb43`
- 适用范围：类目 Provider、类目属性读取、类目匹配、属性填充、草稿与商品模型、
  发布预检与 payload 编译、前端属性编辑、Agent 工具、缓存、数据库版本门禁与
  全新数据库切换
- 优先级：P0
- 关联：`docs/todo.md`「AI / 实战对话持续优化 —— 避免将无关详情塞入上下文，
  例如属性、类目信息」

## 2. 核心结论

本次事故的根因不是工具上限过小，而是平台规则被当成商品事实持久化：

```text
平台类目接口返回完整属性规则
    ↓
类目匹配 / 属性填充把完整 record 写入商品 local_platform_categories
    ↓
capability 又把完整 category_attribute_schema 写入草稿
    ↓
草稿顶层与 target_sites[0] 各保存一份
    ↓
draft_read 返回完整草稿，工具输出超过 256 KiB
```

最终方案必须同时完成两件事：

1. **平台规则统一归 `CategoryProvider` 抽象所有。** Mercado Libre、Ozon、
   Yandex 分别实现该抽象；所有类目属性消费者通过统一 Catalog/Provider 入口读取。
2. **商品与草稿不再持有平台规则。** 它们只保存类目身份和商品实际填写的属性值。

最终边界：

```text
商品 / 草稿       = 商品事实和用户决策
CategoryProvider  = 平台类目事实的抽象端口
平台 Provider     = 平台 API、字段归一化、缓存与 stale 策略
CategoryCatalog   = Provider 解析、统一读取、指纹和有界公共视图
发布上下文         = 草稿 + 当次临时 CategoryDefinition
Agent / 前端输出   = 有界摘要和分页枚举值
```

不得以提高工具输出上限、只过滤 `draft_read`、继续复制精简版 Schema 或保留旧
Schema fallback 替代本次数据模型修复。

## 3. 已确认事实与证据等级

### 3.1 事故事实

- `ai_chat_turn_claims` 中对应 claim 状态为 `failed`，错误码为
  `TOOL_OUTPUT_TOO_LARGE`，trace ID 与本文一致。
- 失败 run 未提交新的 Pydantic message history；对话停在原 history version。
- 事故时工具输出为 344,808 字节，超过全局对话配置的 256 KiB 工具输出上限。
- 当前 claim 的 `last_tool_name` 为空，失败 run 的 tool call 没有形成持久消息。
- 根据工具输出体积、草稿实测大小和调用场景，高置信推断失败工具为
  `draft_read`，但该工具名不是 turn claim 中的持久化事实。实现与验收不依赖这一
  推断成立：任何完整读取超大草稿的入口都存在同样风险。

### 3.2 草稿体积构成

事故草稿中同一份 Schema 同时存在于：

```text
draft.category_attribute_schema
draft.target_sites[0].category_attribute_schema
```

以 `d5c8f09c565e1` 为例，两份对象完全相等，每份约 157,013 UTF-8 字节。
Schema 只有 19 个属性，但两个枚举属性内同时保存了 `options`、规范化 `values`
和平台原始 `raw.values`，占据绝大多数体积。

### 3.3 当前类目抽象

项目已经具备统一抽象的雏形：

- `erp_web/marketplaces/category_provider.py::CategoryProvider` 是 Protocol，定义
  `resolve_site`、`detail(include_attributes=...)` 和 `attribute_values`。
- `erp_web/runtime_units/category_providers.py` 注册了：
  - `MercadoLibreCategoryProvider`
  - `OzonCategoryProvider`
  - `YandexCategoryProvider`
- `erp_web/runtime_units/category_store.py` 通过 `require_category_provider` 分发
  `fetch_category_record`、`fetch_category_attributes` 和
  `fetch_category_attribute_values`。
- 类目关键字搜索另有 `CategorySearcher` Protocol 和平台绑定 Searcher。

当前不足：

1. Provider 是结构化 Protocol，各平台类没有显式继承，必要方法缺失不能在实例化
   阶段失败。
2. `detail(include_attributes=True)` 用布尔开关混合类目详情和属性定义，职责不清晰。
3. `search`、`discover`、`roots`、`browse` 被具体 Searcher 调用，但没有完整出现在
   Provider 类型契约中。
4. 返回对象仍包含平台原始 `raw` 和完整枚举 `values`，没有区分内部规则视图、
   前端视图和 Agent 视图。
5. Yandex 持久 stale 缓存覆盖类目树，不覆盖类目参数；参数定义当前只有 15 分钟
   内存缓存。
6. Ozon 持久 stale 缓存覆盖类目树，不覆盖类目属性定义；
   `include_attributes=True` 当前每次请求实时属性接口。

### 3.4 事故版本的平台规则持久化副本

需要退役的不只有草稿 Schema：

| 副本 | 事故版本写入点 | 问题 |
|---|---|---|
| `draft.category_attribute_schema` | `category_capabilities.py`、`attribute_fill_capabilities.py` | 平台规则进入草稿 |
| `target_sites[*].category_attribute_schema` | `draft_publish_context.py`、`merge_model.py` | 同一 Schema 再复制一份 |
| `product.local_platform_categories[platform]` | `category_model.apply_category_selection`、`apply_ai_attribute_fill` | 完整 category record 进入商品模型 |

`local_platform_categories` 目前还被 Mercado Libre 发布、通用必填摘要和草稿默认值
回落使用。若只删除草稿 Schema 而保留该字段，平台规则仍然存在第二个持久化事实
来源，无法实现统一 Provider 所有权。

## 4. 目标抽象

### 4.1 强制实现的 Provider 基类

将属性读取的核心契约改为显式抽象基类。所有已注册平台必须继承并实现：

```python
from abc import ABC, abstractmethod


class CategoryProvider(ABC):
    platform: str

    @abstractmethod
    def resolve_site(self, site: str = "") -> str:
        ...

    @abstractmethod
    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        ...

    @abstractmethod
    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDefinition:
        ...

    @abstractmethod
    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributeValuePage:
        ...
```

直接删除 `detail(include_attributes=...)` 旧契约，不保留双路径或 compatibility
fallback。`category_detail` 与 `attribute_definitions` 的用途必须显式。

类目搜索与树导航属于可选能力，继续使用小接口，避免把所有平台强塞进同一个胖
基类：

```python
class CategorySearchProvider(Protocol):
    def search_categories(...) -> CategorySearchResult: ...


class CategoryNavigationProvider(Protocol):
    def root_categories(...) -> CategoryBrowseResult: ...
    def browse_categories(...) -> CategoryBrowseResult: ...
```

Provider 注册时执行契约测试：每个注册项必须继承 `CategoryProvider`、平台键唯一，
且 Registry 与 marketplace capability 声明一致。

### 4.2 统一 CategoryCatalog

业务模块不得自行解析注册表，也不得直接调用平台 API。统一入口为
`CategoryCatalog`：

```python
class CategoryCatalog:
    def category_detail(...) -> CategoryDetail: ...
    def attribute_definitions(...) -> CategoryDefinition: ...
    def public_attribute_page(...) -> CategoryAttributePage: ...
    def attribute_values(...) -> CategoryAttributeValuePage: ...
```

调用链：

```text
业务消费者
    ↓
注入的 CategoryCatalog / CategoryDefinitionLoader
    ↓
require_category_provider(platform)
    ↓
MercadoLibre / Ozon / Yandex CategoryProvider
    ↓
平台 API 与 Provider 所有的缓存
```

Capability、发布编排和测试继续使用注入的 Loader/Port，禁止业务模块直接导入
`yandex_category_api`、`ozon_category_api` 或 Mercado Libre 类目 HTTP 函数。

### 4.3 CategoryDefinition 数据形状

`CategoryDefinition` 是临时平台规则，不是持久化商品数据：

```python
class CategoryDefinition(BaseModel):
    platform: str
    site: str
    category_id: str
    category_path: str
    description_category_id: str
    fingerprint: str
    cache: CategoryCacheState
    required: tuple[CategoryAttributeDefinition, ...]
    optional: tuple[CategoryAttributeDefinition, ...]
```

单个属性定义只保留校验和 payload 编译真正需要的规范化字段：

```text
id、name、required、value_type、value_mode、allow_custom_values
constraints、dictionary_id、is_dictionary、is_collection、max_value_count
default_unit、default_unit_id、unit_options、unit_ids
platform_binding（例如 Ozon attribute_complex_id）
options（仅允许有界预览）
```

不得进入 `CategoryDefinition`：

- 完整平台 `raw` 报文
- 完整枚举 `values`
- `raw.values`
- 无界描述或无关平台元数据

Provider 必须把发布必需的 wire 字段归一化为明确的 `platform_binding`，发布代码不得
再从 `raw` 中猜字段。完整枚举统一通过 `attribute_values` 分页读取。

### 4.4 内部视图与公共视图

同一 Provider 对不同消费者提供不同的有界契约：

| 消费者 | 契约 | 是否包含 `platform_binding` | 是否包含完整枚举 |
|---|---|---:|---:|
| 属性填充、预检、payload 编译 | `CategoryDefinition` | 是 | 否 |
| 前端属性编辑器 | `CategoryAttributePage` | 否 | 否 |
| Agent `category_attributes_query` | `CategoryAttributePage` | 否 | 否 |
| 前端/Agent 枚举搜索 | `CategoryAttributeValuePage` | 否 | 仅当前页 |

公共属性页必须有 `limit`、`cursor`、`next_cursor` 和 `has_more`。Agent 与前端不得
获得内部 CategoryDefinition 后自行裁剪。

## 5. 缓存与可用性

### 5.1 当前缺口

现有 Yandex/Ozon 持久缓存主要保存类目树，不足以支撑删除草稿 Schema 后的发布
可用性。实施前必须为 `attribute_definitions` 增加 Provider 所有的定义缓存。

### 5.2 定义缓存契约

缓存键至少包含：

```text
platform + credential_scope_hash + site + category_id + definition_format_version
```

缓存记录包含：

```text
definition
fingerprint
retrieved_at
expires_at
stale_until
source
```

读取规则：

1. fresh cache：直接返回。
2. 无 fresh cache：请求 live。
3. live 成功：归一化、计算指纹并原子更新缓存。
4. live 遇到 timeout、连接失败、429 或平台 5xx：允许返回仍在 stale 窗口内的定义，
   并设置 `cache.stale=true`。
5. 401、403、凭据缺失、类目已禁用、响应结构错误：不得使用 stale 掩盖确定性错误。
6. 超过 `stale_until`：返回可重试的 `CATEGORY_ATTRIBUTES_UNAVAILABLE`，不得回退
   到商品或草稿里的旧规则，因为旧规则已经退役。

缓存属于 Provider/Catalog 基础设施，不进入商品、草稿、发布任务或 Agent history。

### 5.3 稳定指纹

不得使用 `fetched_at` 作为 Schema 版本。Yandex 当前每次读取都会生成新的
`fetched_at`，即使语义完全相同也会变化。

指纹算法：

```text
fingerprint = SHA-256(canonical_json(semantic_definition_projection))
```

指纹必须包含所有影响校验和 payload 的稳定字段，包括 `platform_binding`；必须排除：

```text
fetched_at、retrieved_at、expires_at、stale_until、cache source、排序无关噪声
```

属性和映射排序规则必须固定，并用跨进程测试证明相同定义产生相同指纹。

## 6. 商品与草稿模型

### 6.1 允许持久化的类目字段

草稿及 `target_sites[*]` 只保留：

```json
{
  "platform": "yandex",
  "site": "global",
  "category_id": "16088928",
  "description_category_id": "",
  "category_path": "...",
  "attributes": {
    "14871214": {
      "values": [
        {"dictionary_value_id": "30072093", "value": "蓝色"}
      ]
    }
  }
}
```

`category_path` 是展示快照，`category_id` 是规则读取的规范身份。属性值中的平台枚举
ID 属于用户选择结果，可以持久化；枚举候选全集不可以持久化。

### 6.2 退役字段

彻底删除：

```text
draft.category_attribute_schema
draft.target_sites[*].category_attribute_schema
product.local_platform_categories
```

新建草稿只保存对应平台的类目身份；不从旧商品记录投影，也不提供旧数据库转换。
完整 record 不再有持久化替代位置。

### 6.3 保存入口防回流

草稿写入的 HTTP `/api/save-draft`、Agent `draft_save` 和内部调用最终共用
`ProductStore.save_draft_detail`；商品写入最终经过 `ProductStore.save_product`。
两个 canonical 持久化边界分别拒绝对应的已退役输入：

```text
category_attribute_schema
categoryAttributeSchema
target_sites[*].category_attribute_schema
targetSites[*].categoryAttributeSchema
local_platform_categories
localPlatformCategories
```

不得静默丢弃后继续返回成功。错误码使用明确的
`RETIRED_CATEGORY_SCHEMA_FIELD`，提示调用方重新读取当前契约。

同步更新：

- `erp_web/schemas/product.py`
- `erp_web/product_model/defaults.py`
- `erp_web/product_model/merge_model.py`
- `erp_web/product_model/category_model.py`
- `erp_web/runtime_units/draft_publish_context.py`
- `erp_web/runtime_units/market_capability_support.py`
- `erp_web/stores/product_store.py`
- `erp_web/schemas/product_write_capabilities.py`

## 7. 消费方改造

### 7.1 类目匹配

`category_capabilities.match_category` 的流程调整为：

```text
focused category Agent 选择 category_id
    ↓
CategoryCatalog.attribute_definitions 做最终类目存在性/可用性核验
    ↓
apply_category_selection 只保存类目身份
    ↓
不保存 CategoryDefinition，不写 local_platform_categories
```

focused Agent 仍只返回候选选择、置信度和证据，不接收完整属性规则。

### 7.2 属性填充

`attribute_fill_capabilities`、`category_attribute_ai_fill` 和
`product_model/category_model` 接收当次注入的 `CategoryDefinition`：

```text
读取草稿 category_id
    ↓
Catalog.attribute_definitions
    ↓
规则填充 / focused Agent 填充
    ↓
字典候选按需调用 Catalog.attribute_values
    ↓
只持久化 attributes 与 validation_errors
```

删除属性填充对 `local_platform_categories` 和草稿 Schema 的写入。

### 7.3 类目预检

`category_precheck` 通过 Catalog 加载 `CategoryDefinition`，通用校验函数负责将草稿
值与定义比较：

```python
definition = catalog.attribute_definitions(...)
issues = validate_category_precheck(draft, definition)
```

Provider 不接收草稿，也不拥有预检结果、发布状态或工作流。Provider 负责平台事实；
预检领域负责业务判断。

### 7.4 发布预检与 payload 编译

引入类型化的临时上下文：

```python
@dataclass(frozen=True)
class PreparedPublishContext:
    product: dict[str, Any]
    draft: dict[str, Any]
    target: dict[str, Any]
    category_definition: CategoryDefinition
```

每次预检/编译运行只加载一次定义：

```text
加载草稿目标
    ↓
Catalog.attribute_definitions(platform, site, category_id)
    ↓
PreparedPublishContext
    ├── adapter.validate_draft(context)
    └── adapter.build_payload(context)
```

改造范围：

- `publish_helpers.py`：`_required_attribute_summary` 接收定义，不读商品/草稿规则副本。
- `publish_validation.py`：所有平台预检接收 PreparedPublishContext。
- `publish_ozon.py`：删除 `_category_record(draft)`；`attribute_complex_id` 从
  `platform_binding` 读取。
- `publish_yandex.py`：删除 `_draft_schema_definitions`；单位和枚举 ID 映射从定义
  读取。
- `publish_adapter.py`：三个平台注册适配器统一实现新上下文签名。
- `publish_capabilities.py`、`publish_workflows.py`、`runtime_api.py`：统一创建上下文，
  不各自重新加载定义。

发布预览/审批和真实提交是不同运行，各自最多加载一次定义。真实提交必须重新评估并
核对 `validation_digest`。发布队列持久化的是已批准 payload 和 digest，不持久化
CategoryDefinition；队列 worker 发布冻结 payload 时不得再次偷偷切换规则版本。

列表、索引和批量状态视图不得逐行调用 Provider。它们读取已持久化的轻量
`category_precheck` 摘要。

### 7.5 timeout 与 deadline

CategoryProvider 和发布领域不得依赖 `AiExecutionContext` 类型。统一业务接口接收
`timeout_seconds` 或通用 deadline：

```python
prepare_publish_context(..., timeout_seconds: float | None)
```

预算来源：

| 入口 | timeout 来源 |
|---|---|
| AI Capability | wrapper 从注入的 `AiExecutionContext` 计算后传入 |
| HTTP 预检/预览 | HTTP service/request budget 或配置默认值 |
| 审批快照/真实提交 | 发布 capability/job budget |
| 队列 worker | job 执行预算；冻结 payload 发布阶段不再拉 Schema |

不允许 Provider 反向导入 AI service、Agent 或 HTTP handler。

## 8. 前端改造

### 8.1 编辑态临时加载

属性编辑页流程：

```text
打开草稿 / 切换发布目标
    ↓
读取 platform + site + category_id
    ↓
调用类目属性分页接口
    ↓
CategoryDefinition 的公共视图只存在于 Pinia 编辑态
    ↓
保存只提交 category identity + attributes
```

现有 `loadCategoryAttributes` 和 `fetchCategoryAttributeValues` 可复用，但不得再调用
`persistActiveTargetListingFields` 保存 Schema。切换草稿、平台、站点或 category_id
时清空临时定义；进入属性页后重新加载。

### 8.2 类型与 normalizer

改造文件至少包括：

- `front/src/types/workflow.ts`
- `front/src/types/workflow.generated.ts`
- `front/src/api/workflow/normalizers/core.ts`
- `front/src/api/workflow/normalizers/product.ts`
- `front/src/stores/workflow/orchestration/runtime.ts`
- `front/src/stores/workflow/actions/publishing.ts`
- `front/src/components/domain/CategoryAttributesPanel.vue`

删除持久化模型中的 `categoryAttributeSchema`，保留独立的编辑态
`CategoryAttributePage` 类型。删除：

- `categoryAttributeSchemaFromSelection`
- `categorySelectionFromAttributeSchema`
- `normalizeCategoryAttributeSchema` 的草稿恢复路径
- `toBackendCategoryAttributeSchema`

枚举下拉继续通过 `/api/category-attribute-values` 按关键词和 limit 懒加载。

## 9. Agent 工具边界

### 9.1 category_attributes_query

当前工具会返回完整属性定义。改成分页轻量摘要：

```python
class CategoryAttributesQueryRequest(BaseModel):
    platform: str
    site: str = ""
    category_id: str
    cursor: str = ""
    limit: int = Field(default=50, ge=1, le=100)


class CategoryAttributesQueryResult(BaseModel):
    category_id: str
    category_path: str
    attributes: tuple[CategoryAttributeSummary, ...]
    next_cursor: str = ""
    has_more: bool = False
```

摘要禁止包含 `raw`、`platform_binding`、完整 `values` 和完整 options。

### 9.2 category_attribute_values_query

保留独立枚举查询，统一通过 Catalog/Provider：

```text
category_id + attribute_id + query + cursor + limit
```

返回当前页、`next_cursor` 和 `has_more`。不得把候选全集塞入上一个工具。

### 9.3 draft_read

Schema 分离是根因修复，但 Agent 读取边界仍必须有界。将
`DraftReadResult.draft: dict` 替换为类型化 `DraftReadView`，只返回排查和下一步决策
需要的字段：

```text
draft_id、product_id、platform/site、status
category identity、已填写 attributes
价格/库存摘要、图片计数、validation/precheck/publish 摘要
```

完整图片、发布日志、平台枚举值等通过 focused 分页工具读取。

本要求不是用投影替代模型修复，而是让“数据模型正确”和“Agent 输出有界”同时成立。

## 10. 缓存与数据库切换

### 10.1 当前数据库版本门禁

`erp_web/db.py` 只允许以下两种启动状态：

1. 数据库文件不存在，或 `user_version=0` 且不存在任何用户 schema object 的真正空库；
   此时在单事务内创建完整 v14。
2. `user_version=14` 且 table、column、constraint、index、view、trigger 与当前建库 SQL
   的完整结构签名一致；此时按当前库打开。

非空 v0、v1–v13、未来版本以及结构残缺或带额外用户 object 的 v14，均在任何写入前
原样失败。应用不自动升级、修复、删除或重建数据库，也不保留 runtime migration、
双轨 fallback 或旧 Task 处置分支。

### 10.2 显式全新数据库切换

旧业务数据已经确认不再使用。切换由用户或运维显式执行，而不是由应用启动逻辑删除：

1. 停止应用，并导出仍需保留的 AI Provider 配置和店铺授权。
2. 删除旧 SQLite 主库及对应 `-wal`、`-shm` sidecar。
3. 启动应用创建全新 v14，确认商品、草稿、任务和日志为空。
4. 导回配置与授权，并验证配置可读取、业务表仍为空。

配置导出/导回是运维数据转移，不是 schema migration；旧商品、草稿、消息历史、任务和
发布记录不转换、不恢复。

### 10.3 新写入防回流

新 canonical schema 启用后，旧字段出现在任何新写请求时返回明确错误；不保留旧字段
读取、双写、feature flag 或运行时 fallback。

## 11. 实施阶段

### Phase 0：冻结新契约和事实测试

1. 为 Provider ABC、CategoryDefinition、公共分页 View、fingerprint 定义 Pydantic
   schema。
2. 添加当前缓存能力事实测试，证明 Yandex/Ozon 属性定义尚无持久 stale 缓存。
3. 添加只用于负向输出边界回归的事故草稿 fixture；它不代表受支持的持久格式。

### Phase 1：统一 Provider 与定义缓存

1. 用显式 `CategoryProvider(ABC)` 替换混合的 `detail(include_attributes)` 契约。
2. 三个平台显式继承并实现属性定义、类目详情和枚举查询。
3. 建立 CategoryCatalog 和注入式 Loader。
4. 实现定义持久缓存、stale 规则和稳定 fingerprint。
5. 删除业务模块对平台类目 API 的直接依赖。

### Phase 2：切换所有业务消费者

1. 类目匹配、属性填充和类目预检改用 Catalog。
2. 建立 PreparedPublishContext。
3. Ozon/Yandex/Mercado Libre 预检和 payload 编译改用临时定义。
4. 发布预览、审批、真实提交和 direct publish 统一上下文创建入口。
5. 验证同一运行只加载一次定义。

### Phase 3：删除持久化规则副本

1. 删除草稿及 target site Schema 字段和全部写入点。
2. 删除 `local_platform_categories` 及全部读写回落。
3. 增加保存入口的退役字段拒绝。
4. 更新 canonical product/draft schema 与架构守卫。

### Phase 4：前端与 Agent 有界化

1. 前端改为编辑态分页查询，不再保存 Schema。
2. `category_attributes_query` 与枚举查询分页。
3. `draft_read` 改为类型化有界 View。
4. 重新生成前端类型并重建静态产物。

### Phase 5：执行全新数据库切换

1. 导出需保留的 AI Provider 配置和店铺授权，停止应用后显式删除旧 DB/sidecar。
2. 初始化全新 v14 并导回配置；不导回商品、草稿、消息、任务或日志。
3. 删除 runtime migration 代码与旧迁移 fixture，验证旧版、未来版和残缺库拒绝时
   数据库文件、journal mode 与 sidecar 均不变。

### Phase 6：删除旧实现与完成验收

1. 删除旧类型、helper、alias、测试、mock 和文档说明。
2. 搜索退役字段和直接平台 API 导入残留。
3. 运行后端、前端、架构和真实对话回归。

## 12. 完整消费矩阵

| 消费场景 | 新规则来源 | 持久化结果 |
|---|---|---|
| 类目搜索 | CategorySearchProvider | 候选摘要，不持久化规则 |
| 类目选择终检 | CategoryCatalog.attribute_definitions | category identity |
| 规则/AI 属性填充 | CategoryDefinition + attribute_values | attributes、validation_errors |
| 前端属性表单 | public_attribute_page | attributes |
| Agent 属性查询 | public_attribute_page | 不写业务状态 |
| 类目预检 | CategoryDefinition | precheck 摘要 + fingerprint |
| 发布 payload 编译 | PreparedPublishContext | approved payload + digest |
| 草稿列表/商品索引 | 已持久化 precheck 摘要 | 不调用 Provider |

所有场景不得从草稿、商品、历史 task result 或前端回传对象恢复平台规则。

## 13. 测试契约

### 13.1 Provider 与缓存

1. Registry 中每个平台实现都继承 CategoryProvider，且抽象方法完整。
2. 各平台输出相同的 CategoryDefinition 规范形状。
3. 定义不含 `raw`、完整 `values` 或 `raw.values`。
4. 相同语义定义跨进程产生相同 fingerprint；`fetched_at` 变化不影响指纹。
5. fresh/live/stale/expired 四条缓存路径分别覆盖。
6. 401/403/凭据缺失不允许 stale 掩盖。

### 13.2 业务消费者

1. 类目匹配只持久化类目身份。
2. 属性填充只持久化属性值和校验结果。
3. 预检和 payload 编译共享同一 CategoryDefinition 实例，loader 调用一次。
4. 三个平台均不从商品或草稿读取规则副本。
5. 列表和索引不触发 Provider I/O。
6. Ozon `attribute_complex_id`、Yandex unit/value ID 编译通过明确 binding 完成。

### 13.3 持久化与版本门禁

1. canonical product 不存在 `local_platform_categories`。
2. canonical draft/target 不存在 `category_attribute_schema`。
3. HTTP、Agent 和内部保存入口拒绝 snake_case/camelCase 退役字段。
4. 真正空库一次性创建完整 v14，完整当前 v14 可重复打开。
5. 任何非当前版本或不完整结构都在写入前拒绝，数据库及 sidecar 保持不变。
6. 生产代码不存在 v10–v13 upgrade、旧 Task 取消或类目规则逐行迁移分支。

### 13.4 前端与 Agent

1. 打开属性页会按 category identity 读取公共属性页。
2. 保存请求不含 Schema 或 category record。
3. 枚举值按关键词分页加载。
4. `category_attributes_query` 输出不含 raw/values，分页字段正确。
5. 构造 600 个枚举值时，`draft_read` 和属性查询工具结果仍远小于 256 KiB。
6. 失败工具不能导致已接收用户消息无解释地丢失；若触发 Runtime 大小守卫，应有
   独立的 Pydantic AI 原生错误处理设计与测试，不在领域代码实现第二套 Agent loop。

## 14. 架构守卫

在 `tests/test_ai_context_architecture.py` 或 focused architecture test 中增加：

1. Product、PlatformDraft、DraftTargetSite 不允许规则副本字段。
2. 只有平台 Provider 实现可以导入平台类目 API runtime unit。
3. category match、attribute fill、publish、Agent tool、HTTP facade 只能依赖
   CategoryCatalog、CategoryProvider 抽象或注入 Loader。
4. 发布代码不得读取 `raw` 或草稿 Schema；平台 wire 字段只能来自明确 binding。
5. Agent/Public schema 不允许 `raw`、完整 values 或无界 collection。
6. fingerprint 不允许依赖 `fetched_at` 等易变元数据。
7. 列表和索引路径不得调用 CategoryProvider。
8. 退役符号、字段、alias 和旧测试不存在。

## 15. 必跑命令

后端：

```bash
.venv/bin/python -m pytest tests/test_category_tools.py -q
.venv/bin/python -m pytest tests/test_category_attribute_tools.py -q
.venv/bin/python -m pytest tests/test_domain_read_capabilities.py -q
.venv/bin/python -m pytest tests/test_market_prepare_capabilities.py -q
.venv/bin/python -m pytest tests/test_draft_publish_context.py -q
.venv/bin/python -m pytest tests/test_publish_validation.py -q
.venv/bin/python -m pytest tests/test_yandex_publish.py -q
.venv/bin/python -m pytest tests/test_ozon_publish.py -q
.venv/bin/python -m pytest tests/test_domain_write_capabilities.py -q
.venv/bin/python -m pytest tests/test_erp_web_db_integration.py -q
.venv/bin/python -m pytest tests/test_global_agent_vertical_integration.py -q
.venv/bin/python -m pytest tests/test_ai_context_architecture.py -q
.venv/bin/python -m pytest tests -q
```

前端：

```bash
cd front
pnpm types:generate
pnpm types:check
pnpm test:run
pnpm typecheck
pnpm build
```

残留扫描：

```bash
rg -n "category_attribute_schema|categoryAttributeSchema|local_platform_categories" \
  erp_web front/src tests docs
```

仅明确验证退役输入或事故输出边界的负向测试可以保留字段字面量；生产模型、业务读写
路径和现行文档不得再描述它们为有效字段。

## 16. 验收标准

- [ ] 所有平台注册 Provider 显式实现统一属性抽象。
- [ ] 所有类目属性消费者通过 CategoryCatalog/Provider/注入 Loader 获取规则。
- [ ] 商品和草稿不再持久化完整 category record 或 category Schema。
- [ ] 类目匹配、属性填充、预检和 payload 编译不再存在第二规则来源。
- [ ] Ozon/Yandex/Mercado Libre 的属性定义缓存和失败语义有测试证明。
- [ ] 相同语义定义的 fingerprint 稳定，变化定义会使旧确认失效。
- [ ] 同一次发布评估只加载一次定义，队列持久化冻结 payload 而非定义。
- [ ] 前端属性编辑器按需查询，保存请求只包含类目身份与属性值。
- [ ] Agent 属性列表、枚举值和草稿读取全部有界。
- [x] 数据库只接受真正空库或完整当前 v14，且不存在 runtime migration/fallback。
- [x] 非当前版本或残缺库在写入前原样拒绝；旧库只通过显式导出、删除、重建处理。
- [ ] 后端全量测试、前端测试、类型生成检查、类型检查和构建全部通过。

## 17. 非目标与职责边界

- 不提高 256 KiB 工具输出上限掩盖数据模型问题。
- 不只在 `draft_read` 隐藏 Schema；但仍将 `draft_read` 改成有界契约。
- 不把草稿、发布状态、审批或工作流逻辑放进 CategoryProvider。
- 不让前端直接调用平台类目 API。
- 不在商品、草稿或发布任务中保存所谓“精简 Schema 快照”。
- 不保留旧字段读取、双写、feature flag、shadow 路径或平台 API 直连 fallback。
- 不为 Agent 生命周期、tool call 或错误恢复创建第二套自研 loop；相关行为继续由
  Pydantic AI 原生机制所有。

## 18. 风险与处理

| 风险 | 处理 |
|---|---|
| 删除持久 Schema 后平台属性接口不可用 | Provider 定义缓存 + 严格 stale 规则；确定性鉴权错误不掩盖 |
| 平台规则在预检与提交间变化 | 稳定 fingerprint 纳入 validation digest；提交时重新评估 |
| 统一 Provider 变成胖接口 | 强制属性核心 ABC，搜索/导航拆成小能力接口 |
| 业务模块反复加载定义 | PreparedPublishContext；同一运行 loader 调用次数测试 |
| 列表页产生 N+1 类目请求 | 列表只读持久化 precheck 摘要，架构测试禁止 Provider I/O |
| Ozon/Yandex wire 字段依赖 raw | Provider 归一化为明确 platform_binding，发布代码不读 raw |
| 误把非空旧库当作空库并覆盖 | 只读检查全部用户 schema object；任何非真正空库均 fail-fast，应用绝不自动删除或重建 |
| Agent 又因其他草稿字段超限 | draft_read 类型化有界 View + focused 分页读取工具 |
