# 通用 S3 图片托管开发方案

状态：代码已实施，离线自动化验收通过；真实存储验收待提供隔离桶配置。本文不代表功能已经部署上线。

日期：2026-10-01。

范围：退役本地 Cloudflare 隧道图片交付，加入可配置的通用 S3 图片托管，并接入 Yandex 和 Ozon 的发布准备流程。Mercado Libre 保留平台图片上传流程。

## 1 决策与交付范围

开发一套 `s3_compatible` 适配器，通过 Endpoint、Region、Bucket、凭据及公开地址配置接入不同对象存储。R2、B2、Tigris、OCI 不分别实现上传器；服务商填写预设可以辅助配置，但不是独立业务分支。

第一版交付以下能力：

- 保存多个托管配置，并指定一个默认配置。
- 上传本地原图或处理后的图片，生成稳定、可匿名读取的 HTTPS 图片地址。
- 按内容哈希生成对象路径，在同一交付目标内复用已经上传的图片。
- 在界面测试实际上传和公开读取，分别报告结果。
- Yandex、Ozon 通过统一图片交付服务取得 URL，Mercado Libre 继续使用平台图片 ID。
- 删除旧隧道、公开目录复制实现、旧配置、旧分支及对应测试，不保留备用路径。

不包含 Cloudinary 适配器、旧图片数据迁移、多服务自动切换、后台批量搬迁、自动建桶、权限配置、CDN 配置、生产对象自动清理和收费额度管理。S3 兼容只保证本方案所需的基本操作，不宣称完整兼容所有 AWS S3 功能，也不把免费套餐视为永久在线保证。

本地原图、翻译结果、图片池和界面预览仍有独立产品价值，必须保留。用户不要求兼容旧托管数据，不等于授权删除业务图片、数据库、凭据或已发布商品。

## 2 实施前的实现与市场差异

发布适配器注册入口仍为 `erp_web/runtime_units/publish_adapter.py`；以下旧路径是本方案实施前的基线。

| 平台 | 实施前交付方式 | 本次处理 |
| --- | --- | --- |
| Yandex Market | 调用 `ImageDeliveryService.prepare_product`，最终提交 `pictures` URL 列表 | 本地图片交付改用 S3，平台 payload 结构不变 |
| Ozon | 调用同一交付服务，最终提交 `images` URL 列表 | 本地图片交付改用 S3，平台 payload 结构不变 |
| Mercado Libre | `ensure_mercadolibre_pictures_uploaded` 上传文件，取得平台图片 ID | 保持现有路径，不强制经过 S3 |

实施前，`erp_web/services/image_delivery_service.py` 包含 `existing_url` 和 `local_static` provider，通过环境变量取得配置。`local_static` 按内容哈希把图片复制到公开目录，`scripts/image_https_tunnel.sh` 将该目录经 Quick Tunnel 暴露；`scripts/dev.sh` 管理隧道生命周期。这些实现和配置现已删除。

实施前，Yandex、Ozon 设置了 `prepare_is_local_only=True`。`publish_workflows.py` 的预检以及 `publish_capabilities.py` 的显式准备据此在校验前执行本地物化。S3 上传属于外部写入，该标记及预检准备分支现已删除。

`product_publish_validate` 当前已经是只读入口；目标是所有普通预检均不上传图片，而不是新增另一套预检或发布工作流。

## 3 强制边界

1. 上传 Endpoint 和公开图片地址分别配置，不能从 S3 Endpoint 推断图片已经可以匿名读取。
2. 平台适配器决定图片交付方式。界面不提供“哪些市场使用托管”的用户开关。
3. 已有合规外部图片直链可以直接使用；本地图片及本项目管理的交付结果由当前默认配置准备。这是来源分类，不是 S3 失败后的 fallback。
4. 未配置托管服务时，本地图片明确返回“需要配置图片托管”。不得创建隧道、使用过期托管地址或假报成功；仅使用有效外部直链的草稿不必因此阻断。
5. 普通配置读取、保存、设为默认和只读校验不发起上传。显式配置测试、发布素材准备及受信 payload 预览可以按各自契约产生外部写入。
6. 任何上传失败不得降级使用本地 URL。公开访问成功只证明当前服务器视角，不证明 Yandex 或 Ozon 已抓取成功。
7. 网络等待不得发生在商品聚合写锁或数据库事务内。上传后的业务写回使用短锁，并检查源图片和目标配置是否仍与准备时一致。
8. 发布 worker 使用已确认的冻结 payload，不重新上传、切换托管配置或改写 URL。确认前及队列准入重新检查交付有效性和原有 digest；配置切换不改写已入队任务。
9. 不新增 Agent loop、工具审批协议或消息状态机；现有写工具、执行权限和 Pydantic AI 原生生命周期保持不变。

## 4 模块划分

以下为目标模块，实施时可按项目已有职责合并，但不得形成第二套配置或发布 owner。

| 模块 | 职责 |
| --- | --- |
| `erp_web/schemas/image_hosting.py` | 配置、交付记录、配置测试结果和类型化错误契约 |
| `erp_web/app_config.py` 与 `erp_web/stores/config_store.py` | 默认值、归一化、配置持久化及秘密拆分，仍是配置唯一 owner |
| `erp_web/services/image_hosting_config.py` | 依赖轻量的字段校验、目标指纹和 URL 拼接，不做持久化或网络调用 |
| `erp_web/services/s3_image_storage.py` | S3 客户端装配、对象检查、上传和结果转换；签名及协议处理使用成熟 SDK |
| `erp_web/services/image_delivery_service.py` | 选择本次草稿图片、区分直链与本地素材、协调交付并返回交付字段 |
| `erp_web/services/image_public_access.py` | 匿名 HTTPS 访问检查，与 S3 身份认证请求隔离 |
| `erp_web/facades/image_hosting_facade.py` | 配置测试与应用级业务编排，不直接重复底层上传逻辑 |
| `erp_web/http_route_units/` 与 `erp_web/schemas/requests.py` | 薄 HTTP 入口、`HANDLED_PATHS` 和 `REQUEST_CONTRACTS`，每次读 body 均校验 |
| 前端设置页 | 配置列表、表单、默认配置和测试结果展示 |

保留并收敛现有 `ImageHttpsProvider` / `ImageDeliveryService` 交付边界，明确注册唯一的 S3 上传实现。已有外部直链在素材来源解析时处理，不继续作为可选的 `existing_url` 托管类型。

S3 客户端必须注入明确配置和凭据，不读取开发机的默认 AWS profile、环境凭据或实例角色作为隐式 fallback。只在这条边界装配客户端，平台模块不得直接导入 S3 SDK。

## 5 配置与凭据契约

在应用配置中新增 `image_hosting`，默认值为 `default_profile_id=""`、`profiles=[]`。以下 JSON 仅展示普通配置；示例域名不是可直接使用的服务。

```json
{
  "image_hosting": {
    "default_profile_id": "images-main",
    "profiles": [
      {
        "id": "images-main",
        "name": "商品图片存储",
        "type": "s3_compatible",
        "endpoint_url": "https://s3.example.com",
        "region": "服务商指定值",
        "bucket": "product-images",
        "key_prefix": "products",
        "public_base_url": "https://images.example.com",
        "addressing_style": "auto"
      }
    ]
  }
}
```

| 字段 | 校验与用途 |
| --- | --- |
| `id` | 后端生成的稳定配置身份，编辑和列表重排不能改变 |
| `name` | 用户可编辑的显示名称，不参与对象身份 |
| `type` | 第一版只接受 `s3_compatible`，未知类型明确拒绝 |
| `endpoint_url` | HTTPS S3 API 地址，不含凭据、查询或片段；桶独立填写 |
| `region` | 按服务商要求填写，允许其文档规定的 `auto` 等值，不猜测区域 |
| `bucket` | 必填，按照兼容服务允许的桶名称规则校验 |
| `key_prefix` | 可为空；规范化路径分隔符，禁止 `..` 等路径逃逸形式 |
| `public_base_url` | HTTPS 公开地址前缀，可带服务商所需固定路径，不含凭据、临时签名或片段 |
| `addressing_style` | `auto`、`path` 或 `virtual`；作为高级设置，不为每家服务写分支 |
| `access_key_id` 与 `secret_access_key` | 必需的秘密字段，只通过后端保存与读取 |

公开地址由 `public_base_url` 加 URL 编码后的完整对象 key 生成。`public_base_url` 应包含公开入口所需的桶或命名空间路径，但不重复包含 `key_prefix`；桶不能在所有服务上采用相同的 URL 拼接规则，因此该前缀由用户明确配置。

沿用 SQLite `runtime_secrets` 保存凭据。当前秘密字段识别规则未明确覆盖 `access_key_id`、`secret_access_key`，必须显式补入，验证这两个字段均不会写入普通 JSON、日志、API 回读或工具结果。列表使用稳定 `id` 关联秘密，不能按数组序号关联。

界面显示“已配置 / 未配置”，支持替换及明确清空。未提交的秘密字段表示保留，掩码不是新凭据；清空使用明确动作，不用空字符串同时表达保留和删除。删除配置同步清理它自己的秘密，不删除远端对象；默认配置必须先解除或切换后才能删除。

`app_config` 默认值、顶层允许字段、归一化、脱敏响应、前端类型及示例配置必须同时更新。保存只做结构校验，不以网络测试失败阻止用户保存草稿配置；不完整配置不得设为可用默认项或用于上传。

## 6 S3 上传与交付记录

### 6.1 第一版协议范围

仅依赖 `PutObject` 和 `HeadObject` 进行生产图片交付。对象写入包含正确的 `Content-Type`、内容 SHA-256 元数据及适当的缓存控制；不依赖对象级 `public-read` ACL、自动建桶、复杂 Bucket Policy、版本管理、跨桶复制或多段上传。

使用成熟 S3 SDK 负责 AWS Signature Version 4、请求序列化和响应解析。SDK 选型与版本锁定前，先验证公开扩展点能接入现有统一请求管理器，并验证寻址方式、校验和请求头及常用服务兼容性。不自研签名协议，不以“兼容 S3”为由启用服务不支持的可选功能。Boto3 的寻址、超时及重试配置可参照其[官方配置文档](https://docs.aws.amazon.com/boto3/latest/guide/configuration.html)。

桶、公开读取权限与域名由用户在服务商侧准备。生产上传权限最小化为目标桶或前缀的写入和对象检查，不要求全账户管理权限。

### 6.2 对象身份与复用

对象 key 采用 `<key_prefix>/assets/<sha256前两位>/<sha256>.<规范扩展名>`。按实际图片字节计算哈希和识别格式，不能只信任扩展名。同一内容和同一交付目标重复准备时，复用已确认对象；不同目标之间不能仅凭相同 key 推断已上传。

在现有图片池持久化结构中扩展交付信息：`hosting_profile_id`、`delivery_fingerprint`、`delivery_provider`、`storage_key`、`content_sha256` 和 `url`。由共享 schema 定义，并同步调整图片池归一化与商品持久化，确保字段不会在回读时被丢弃；不把临时字典形状扩散到各平台。第一版不新增多服务交付历史表。

目标指纹包含类型、Endpoint、Region、Bucket、路径前缀、公开地址前缀和寻址方式。名称变化不影响交付；密钥轮换不改变对象身份，但应让配置测试结果失效并使用新凭据装配客户端。

同一目标检查已知 key 时，`HeadObject` 返回不存在才允许上传；403、超时或其他失败不能当作不存在。检查内容元数据和长度；不能将 ETag 当作通用 SHA-256。对象存在但元数据不匹配时报告冲突，不静默覆盖。并发写入同一内容 key 时不能产生不同业务对象或误合并其他图片。

### 6.3 配置切换与写回

切换默认配置或修改交付目标后，旧交付记录在新准备中判为失效。有本地源文件时上传到新目标；源文件不存在时明确报错，不能只替换 URL 前缀而假定新桶已有对象。

准备过程先取得源图片和配置快照，锁外执行网络操作，最后短锁内合并交付字段。源内容、图片选择或当前目标发生并发变化时，拒绝把旧结果标记为当前已准备；保留其他用户编辑，不覆盖完整商品快照。

本次不迁移旧隧道记录、不扫描重传历史图片、不自动删除旧桶对象，也不更改已经发布到市场的商品。

### 6.4 网络审计与失败处理

S3 请求和匿名图片访问必须接入现有外部请求管理，关联托管配置、目标及业务操作；不得误归属为 Yandex/Ozon 卖家接口，也不得直接由 SDK 的默认传输绕过审计。实现一层专用 SDK 传输适配即可，不建立第二套请求管理器。

默认关闭 SDK 自动重试及业务层重试。只读检查的有限重试使用统一管理器现有预算。上传超时或断连按结果未知处理，本次准备不宣称成功、不盲目重发；下一次用户显式准备先检查对象，内容匹配才复用。后续若允许写入重试，必须先为内容寻址写入证明并声明幂等语义，不作为第一版默认能力。

共享遵守超时、并发限制、取消、限流和既有阻断策略。按项目安全边界限制自定义地址及重定向，防止服务端探测本机、内网和云元数据地址；匿名访问不携带 S3 密钥、店铺授权或 Cookie。错误日志不得输出签名 URL、Authorization 或原始凭据。

建议错误契约至少覆盖 `IMAGE_HOSTING_NOT_CONFIGURED`、`IMAGE_HOSTING_CONFIG_INVALID`、`IMAGE_SOURCE_MISSING`、`IMAGE_UPLOAD_FAILED`、`IMAGE_UPLOAD_OUTCOME_UNKNOWN`、`IMAGE_DELIVERY_STALE` 和 `IMAGE_PUBLIC_ACCESS_FAILED`；错误包含中文原因与下一步动作。

## 7 发布准备与只读校验

移除 Yandex/Ozon 的 `prepare_is_local_only=True` 和相关“本地物化可以在普通预检内执行”的分支、注释及测试。不要仅把值改成 false：当前 URL 校验可能因此在上传前阻断本地图片，必须同步分离校验阶段。

目标顺序如下：

1. 上传前基础校验：核对草稿、店铺、类目、属性和图片源。本地文件是合法的待准备素材，不因为还没有公网 URL 就阻止显式准备；配置或源文件缺失仍阻断。
2. 显式准备素材：Yandex/Ozon 调用统一 S3 交付；Mercado Libre 调用现有平台上传。上传或源检查失败不得进入最终 payload 确认。
3. 短锁写回图片交付字段，回读当前草稿，并检查并发变化。
4. 最终确定性校验：要求实际提交的图片已经具备对应平台交付结果，生成 payload 与 digest。
5. 用户确认后按现有边界重新校验并入队，worker 发送冻结 payload，不再准备素材。

复用现有校验实现的共同规则，仅把“源素材是否可准备”与“最终图片是否可提交”的条件拆开，不复制两套类目或属性规则。

`product_publish_validate` 以及普通 HTTP 预检只读取当前交付状态，可以返回“尚未准备”及调用准备操作的提示，不能上传或复制图片。`product_publish_prepare` 和受信 `publish-payload-preview` 是显式准备入口，应在界面文案和工具契约中说明可能上传素材。已有 `check_public_access` 仍是显式网络探测，不作为确定性 digest 或平台抓取成功的证据。

S3 只负责提供图片交付地址，不改变 Yandex 的 `pictures`、Ozon 的 `images`、Mercado Libre 的图片 ID 契约。原有平台尺寸、格式、数量等规则继续由平台校验负责。

## 8 配置界面与测试

设置中增加“图片托管”，列表展示名称、S3 Endpoint、Bucket、默认状态、凭据配置状态以及最近一次测试结果。提供新增、编辑、删除、测试与设为默认；用户不需要选择市场。

表单以“S3 兼容存储”为唯一类型，包含第 5 节字段，寻址方式放在高级设置。展示说明：“上传接口地址用于写入，公开图片地址用于平台读取，两者可能不同；公开读取和域名需在存储商侧配置。”

凭据输入框内显示“已配置，留空保持原值”，与 AI API Key 输入一致；未配置时提示输入对应凭据，直接输入新值即可替换。低频的删除操作放在高级设置，使用“移除已保存凭据”按钮，一次移除两项凭据，不在普通表单中放清空复选框。点击按钮只标记待移除，可取消；点击“保存配置”后才生效，并说明移除后配置将无法上传。默认配置须先解除默认才能移除凭据。

测试由用户显式触发，对本次表单配置执行，不能静默改写已保存配置或默认项。保留已配置秘密时，后端按配置 id 取回秘密，不发给前端。测试结果关联配置版本或指纹，修改地址、桶或凭据后不能继续显示旧结果为当前成功。

测试步骤为：

1. 校验配置和权限边界。
2. 上传一张项目提供的有效小图片到独立测试前缀。
3. 检查已上传对象，生成公开 HTTPS 地址。
4. 不携带授权实际 GET 图片，限制下载大小和时间，核对状态、图片类型及内容；HEAD 成功不能单独代表可下载。
5. 分别返回“上传成功”“匿名读取成功”和测试对象清理结果，不以 S3 上传成功替代全部成功。

测试对象与业务对象分离。具备删除权限时仅删除本次精确测试 key；没有删除权限时提示残留 key，由用户处理，不因此要求扩大生产凭据权限，也不把清理失败冒充交付失败。配置删除、改名和切换默认均不触发远端对象删除。

如使用弹窗，复用 `WorkspaceDialog.vue`；自定义遮罩按 `useBackdropDismiss` 完整手势契约接入。保存和测试中防止重复提交，不让遮罩误关闭造成用户误判操作结果。

## 9 旧实现退役清单

| 位置 | 必须移除的内容 |
| --- | --- |
| `scripts/image_https_tunnel.sh` | 删除脚本及本地静态服务器、cloudflared 启动路径 |
| `scripts/dev.sh` | 图片隧道启停、地址探测、日志专用逻辑、环境注入和 required/off/auto 分支 |
| `config/.env.example` | 删除 `ERP_IMAGE_HTTPS_PROVIDER`、`ERP_IMAGE_HTTPS_BASE_URL`、`ERP_IMAGE_HTTPS_ROOT`、`ERP_IMAGE_HTTPS_TUNNEL`、`ERP_IMAGE_HTTPS_PORT` |
| `image_delivery_service.py` | 删除环境配置读取、`LocalStaticHttpsProvider`、公开目录复制以及把 `existing_url` 作为托管类型的注册 |
| 发布准备与预检 | 删除 `prepare_is_local_only` 旧判断及只为本地复制存在的推进路径 |
| 旧测试与 mock | 改写本地复制、隧道 URL 重算和环境变量驱动测试，删除仅验证废弃行为的用例 |
| 文档与示例 | 更新 `docs/ai-context-map.md` 和相关使用说明，只描述实施后唯一有效路径 |

不卸载机器上全局安装的 `cloudflared`，不删除用户自己的 Cloudflare 服务，不删除图片池或本地业务图片。旧隧道调试产物只对本次确认为无用的精确目标清理，不能把整个 `data/` 当作临时目录删除。

保留本开发文档的退役说明作为历史设计依据；残留检索针对有效代码、有效配置和当前使用说明，不能把本文提到的旧名称误判为运行时残留。

## 10 实施顺序

1. 定义共享 schema、配置字段、凭据规则及错误契约；验证 SDK 与统一外部请求管理的集成方式。
2. 实现 S3 基础模块、目标指纹、内容寻址上传及匿名访问测试，先通过离线测试。
3. 接入 `ImageDeliveryService`，改造 Yandex/Ozon 的两阶段校验和显式准备；覆盖原有 Mercado 上传路径不受影响。
4. 增加薄 HTTP 入口和图片托管设置界面，补齐脱敏、清空、切换、并发及重复提交行为。
5. 完整删除隧道和旧交付实现，更新示例及当前架构说明；不保留运行时开关或双路径。
6. 运行完整回归，用用户提供的隔离存储配置做上传与匿名读取验收，记录实际测试过的服务及配置差异。

以上是开发顺序，不是要求各中间状态独立发布。最终交付必须同时完成新路径和旧实现退役；回滚依靠版本控制。

## 11 验收标准

### 11.1 离线自动化验证

- 配置归一化拒绝未知字段、无效默认 id、不合法 URL 和路径；稳定 id 在重排后正确关联秘密。
- 两项凭据仅进入 `runtime_secrets`，API、日志、配置文件、测试结果和工具结果均不泄露。
- S3 签名请求、寻址方式、Content-Type、对象 key、元数据、超时及网络审计可在离线受控响应中验证。
- 同目标重复准备不重复上传；换 Bucket、Endpoint 或公开入口后不误用旧交付；只有 HeadObject 明确不存在才执行上传。
- 上传失败、公开访问失败、403、429 和结果未知分别报告；SDK 与业务层没有隐式重试。
- 只读工具和普通 HTTP 预检的上传调用次数为零；本地素材不会因未上传而阻止显式准备，最终发布仍不能提交本地地址。
- Yandex/Ozon 的最终 URL 与图片选择一致；Mercado 仍使用原生图片 ID，没有新增 S3 调用。
- 上传期间并发修改商品或配置时不会覆盖用户编辑、误标已准备或生成可确认的过期 payload；网络请求不跨商品写锁。
- 冻结 payload 不被后续配置切换改写，worker 不调用 S3；队列准入保留既有审批及 digest 校验。
- 测试 GET 不携带秘密，重定向受限，HTML 或损坏内容不能记为图片访问成功；清理仅涉及本次测试 key。
- 弹窗覆盖内部按下外部松开不关闭、正常遮罩点击关闭，以及提交中不能关闭的场景。
- 架构守卫验证唯一交付入口、配置 owner、统一网络审计和旧脚本、provider、环境变量、旧准备标记不再用于运行时。

实施后执行后端完整测试 `.venv/bin/python -m pytest tests -q`，以及项目现有前端测试、类型检查、Python 编译检查和 `git diff --check`。至少更新 `tests/test_ai_context_architecture.py` 或当前负责对应边界的架构测试。文档本身的交付只需检查内容和差异，不声称已经通过功能验收。

### 11.2 真实存储验证

使用专用测试桶或前缀，验证一张真实图片能够上传、匿名 HTTPS 下载及重复准备复用。要声明某服务已兼容，必须记录该服务实际验证结果；只有离线测试或官方宣称兼容，不能写成“四家均已实测”。

真实市场发布不属于默认测试动作。需要验证 Yandex/Ozon 实际抓取时，应由用户另行指定测试草稿及发布范围，平台回执与公开 URL 测试结果分别记录。

## 12 服务商资料与配置责任

服务商配置以实施时官方文档为准，不在项目中硬编码免费额度或永久可用承诺。

- R2 的 S3 操作范围见[兼容列表](https://developers.cloudflare.com/r2/api/s3/api/)。公开读取需单独配置；`r2.dev` 是有限流的开发入口，正式交付使用自定义域名或其他正式入口，见[公开桶文档](https://developers.cloudflare.com/r2/buckets/public-buckets/)。
- B2 的 Endpoint、签名与寻址支持见[S3 兼容 API 文档](https://www.backblaze.com/docs/en/cloud-storage-call-the-s3-compatible-api)。
- Tigris 支持 S3 客户端及自定义公开域名，见[官方能力说明](https://www.tigrisdata.com/features/)。
- OCI 的 S3 兼容范围并非全部 AWS S3 能力，凭据使用其 S3 兼容 Access Key / Secret Key，见[官方常见问题](https://www.oracle.com/cn/cloud/storage/object-storage/faq/)。

用户负责创建存储资源、授权及公开访问入口；项目负责验证配置、可靠执行已声明操作和提供明确结果。不把图片 URL 无短期签名等同于无限期保证：账户停用、对象删除、域名失效或额度限制仍可能导致图片不可访问。

## 13 实施与验证记录

实施日期：2026-10-02。

- 已加入多配置管理、后端稳定 id、默认项和 SQLite 秘密拆分；前端入口为“设置 → 图片托管”。保存不产生网络请求，表单测试不静默保存配置。
- SDK 使用 `botocore==1.43.106`，通过公开 `before-send.s3` 扩展点将签名请求交给现有统一请求管理器。离线测试覆盖三种寻址方式、显式凭据、元数据、禁用可选校验和、403/429、重定向和结果未知后的显式复用。
- `image_content.py` 负责纯图片校验；`image_hosting_transport.py` 负责 HTTPS 地址、DNS 绑定与下载限制；`image_delivery_persistence.py` 在锁外上传后短锁合并交付字段。配置测试与生产上传复用同一 S3 基础边界。
- Yandex/Ozon 共用源校验、显式准备、最终校验和 digest 流程，覆盖草稿及所选 SKU 的图片。普通预检与只读工具不上传；队列准入复核交付和草稿输入，保留并发编辑；worker 使用冻结 payload。Mercado Libre 继续走平台图片 ID。
- 旧 provider、隧道脚本、开发启动逻辑、环境变量和废弃准备标记已退役；当前入口与职责见 `docs/ai-context-map.md`。

本地验证结果：

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests -q --tb=short` | 2137 项及 47 个子用例通过 |
| `pnpm test:run`（`front/`） | 62 个测试文件、513 项测试通过 |
| `pnpm typecheck`（`front/`） | 通过 |
| `pnpm lint:check`（`front/`） | 0 错误；9 条既有警告来自未修改文件 |
| `.venv/bin/python scripts/generate_frontend_types.py --check` | 后端 schema 与前端生成类型一致 |
| Python 编译、`bash -n scripts/dev.sh`、`git diff --check` | 通过 |

首次实施回归使用离线存储响应，未执行真实市场发布，也没有声称 R2、B2、Tigris 或 OCI 已实测兼容。上线使用前应在“图片托管”中填写存储商要求的字段，显式测试上传和匿名读取，再将配置设为默认；真实验收仍按第 11.2 节记录。

同日 Backblaze B2 联调中，用户报告配置测试失败。审计显示 PUT 与 DELETE 均未收到 HTTP 响应，匿名 GET 尚未执行；本机将 `s3.us-east-005.backblazeb2.com` 解析为 `198.18.0.6`。复现确认该保留地址被安全传输在 HTTP 发送前阻止，可能来自代理的 Fake-IP 模式，尚不能据此判断 S3 凭据或公开权限是否正确。

已修正发送前拦截的错误分类和测试状态：统一请求审计记录 `not_sent`，不误报写入结果未知；DNS/连接/TLS 原因使用固定脱敏说明。未发送 PUT 时跳过匿名 GET 和 DELETE，并显示无需清理；PUT 后 HEAD 失败仍清理本次精确 key。离线回归覆盖 Fake-IP、连接/TLS 拦截、审计状态、跳过未执行步骤及真正的写入结果未知。保留公网 IP 校验与 TLS 证书验证，不绕过安全边界。

DNS 恢复后，只读诊断取得 B2 的 `InvalidAccessKeyId` 拒绝码。用户更新应用密钥后的真实配置测试取得 PUT 200、HEAD 200 和 DELETE 204，上传、对象元数据检查及测试对象清理通过；匿名 GET 返回 401，公开读取验收尚未通过，需在存储商侧核对桶公开权限。

已修正匿名 401/403 的请求策略：只拒绝本次请求，不将没有凭据的匿名读取判为“凭据失效”，允许公开权限修正后的下一次显式测试。使用现有恢复入口精确解除本次误分类产生的一条匿名凭据阻断，保留审计和其他真实阻断。离线回归覆盖同一地址在 401 后再次显式读取成功，未宣称匿名下载或真实市场抓取已经通过。

Supabase 联调已通过真实上传、匿名图片读取及本次独立测试对象清理。此前匿名 GET 的 HTTP 400 来自公开地址前缀重复填写 `products`，仅在配置中去掉多余路径即可解决：公开前缀到 `/storage/v1/object/public/<bucket>`，对象路径前缀由项目统一拼接。配置测试的成功界面只展示上传、匿名读取和清理结果；失败时才展示具体原因。
