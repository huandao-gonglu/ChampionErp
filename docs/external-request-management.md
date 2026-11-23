# 外部请求管理与限流边界

当前采用两层极简架构：**源头约束调用边界，统一出口仅保留纯内存并发/退避保险丝 + 纯观测审计日志**。不再维护 SQLite 持久阻断状态机、策略配置表、探测租约或独立「系统设置 → 中断与恢复」审批面板。

## 入口与职责

- `schemas/external_requests.py`：定义 `RequestContext`、`RequestFailure`、`ExternalRequestBlocked`、`ExternalRequestNotSent`、`ExternalRequestOutcomeUnknown`；平台错误公共类型在 `schemas/platform_errors.py`。
- `services/external_request_context.py`：关联 HTTP、后台 Job、AI Tool 的来源、取消、期限、单轮请求上限与授权穿透标记。仅读取本地身份，不做远端预检查；凭据只存 SHA-256 指纹。
- `services/external_request_limiter.py`：进程内纯内存并发闸门、速率令牌桶、共享背压退避、短熔断、单轮 AI 预算与鉴权熔断。使用 `threading.Condition` 精确等待，零 SQLite 轮询写锁。
- `services/platform_request_policy.py`：纯函数，优先按 HTTP 状态码和平台结构化业务 `code` 识别拒绝、限流与响应配额头（Ozon `Ratelimit-Remaining`、Yandex `X-RateLimit-Resource-Remaining/Until`）。
- `services/external_request_manager.py`：业务 urllib 外发唯一入口。本机 Chrome CDP（`services/browser_debug_http.py`）、New API 内部桥接、Turnstile 与原生 AI Provider/SDK 推理保持独立边界。AI 发起的业务工具经本管理器约束，单轮默认最多 **40 次**真实外发。
- `stores/external_request_store.py`：独立观测审计库 `external-requests.sqlite3`（schema v3、WAL、`0600`），仅保留脱敏后的 `external_attempts` 单表，供事后查账、24 小时写入 `outcome_unknown` 防重放及同凭据只读历史核验。保留期清理（30 天 / 100,000 条）节流执行，不拖慢每次请求。
- `scripts/external_requests.py`：本地只读审计查询（`query`），不访问外部平台。

## 默认限速与故障处理

| 机制 | 默认行为 |
| --- | --- |
| **Ozon 主动限速** | 每 `Client-Id` 进程内默认并发 **3**、**15 RPS**（保守低于公开资料 50 RPS 上限，为多系统共用留余量）；见 `Ratelimit-Remaining: 0` 暂停内存令牌桶至下一秒（或 Reset 头），不落库 |
| **Yandex 主动限速** | 按官方规则对同 `campaign/business` 最多放行 **4 并发**；见 `X-RateLimit-Resource-Remaining: 0` 按 `X-RateLimit-Resource-Until` 暂停内存接口窗口 |
| **Mercado Libre** | 每账号默认并发 **4**，不猜测官方 RPS |
| **万邦 1688** | 识别 HTTP 200 的 `error_code`：4013 冷却至北京时间次日 00:00 并持久化到 `onebound-quota.sqlite3`；4016/4007/4005 内存熔断并持久停用，管理员在设置中点「恢复使用」（`POST /api/alibaba/onebound/key/restore`，不发探测）解除；4008 走纯内存退避；4006/4012 单接口 60s 防重撞；4009 维护中对写入保留 `outcome_unknown`；发送前经 `before_send` 校验两层额度与分级放行 |
| **429 / 420 / Ozon `code 8`** | 纯内存共享冷却：有有效 `Retry-After` 按其等待；无恢复时间按**轮次**退避 `2s → 5s → 15s → 60s`（并发在途的晚到 429 不重复升级）；到期单飞放行，超任务 deadline 快速失败停止，**不落库、不要求人工点恢复** |
| **401 / 凭据失效** | 内存按 `credential_id` 熔断（不阻断刷新后的新凭据）；Ozon/Yandex 同步将匹配店铺的 `auth_status` 标为异常，引导到「授权配置」更换并测试 |
| **Ozon 403 / `code 7`** | 单接口 403 仅报错缺权限并设 60s 内存防重撞，不锁整店；命中 `/v1/seller/info`、明确停用文案、**同 `credential_id` 此前成功过的只读接口突报 403**、或 10 分钟内同凭据无成功且 **≥ 3 个不同接口 403** 时，升级为整号熔断并持久化到店铺 `auth_status` |
| **恢复方式** | 401/账号受限统一在「授权配置」修正权限或换 Key 后点击「测试连接」（允许穿透熔断验证），测试通过后自动恢复 |
| **5xx / 网络错误（只读）** | 默认单次请求 `max_attempts=1`（显式只读最多 3 次，如在线商品同步 `read_max_attempts=3` 与 Yandex 费用试算，每次尝试独立按单次超时上限并受外层 `deadline_at` 约束）；10 分钟内连续 5 次故障进入 60s 内存短熔断，单飞只读探测，失败退避至 600s，成功或重启自动恢复 |
| **写入超时 / 断连 / 5xx** | 永不自动重试；记录 `outcome_unknown` 并抛 `ExternalRequestOutcomeUnknown`，24 小时内同操作同参数指纹写入跨重启本地阻止重放 |

## 审计库迁移（v1/v2 → v3）

启动打开既有 v1/v2 `external-requests.sqlite3` 时在单事务内无损迁移：
- 保留全部 `external_attempts` 历史记录（补齐 `fingerprint` 列）；
- 若旧 `external_blocks` 存在尚未解除的账号级封禁（`*_ACCOUNT_DISABLED`），自动承接到对应店铺的 `auth_status = failed`，确保升级不会放开曾被封的账号；
- 删除已退役的 `external_blocks`、`external_policies`、`external_recoveries` 表并升级至 `user_version = 3`。

## 运维查询

```bash
.venv/bin/python scripts/external_requests.py query --platform ozon --limit 100
.venv/bin/python scripts/external_requests.py query --operation-id '<任务编号>' --since 1790524800
```
