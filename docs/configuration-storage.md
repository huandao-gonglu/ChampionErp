# 配置与授权存储

`ConfigStore` 是 ERP 自管授权与配置的统一读写入口。授权集中于应用数据目录的 `erp.sqlite3`，不随安装包分发；开发环境和桌面版各有自己的数据目录，互不同步。

## 当前归属

| 内容 | 持久化位置与负责模块 |
| --- | --- |
| 店铺密钥、OAuth 令牌和动态授权状态 | 主库 `store_auth`，由 `ConfigStore` 管理 |
| 1688 应用与买家授权 | 主库 `runtime_secrets` 的 `credentials:alibaba` 命名空间，采购用 `state`，跨境查询用 `crossborder`。环境应用密钥不复制入库；见 [1688 授权](alibaba-authorization.md) |
| AI、图床等应用密钥 | 主库 `runtime_secrets` 的 `app_config` 命名空间，由 `ConfigStore` 拆分、合并和脱敏 |
| 跨境巴士企业密钥 | 开发者注入后端环境变量 `ERP_CROSSBORDERBUS_CLIENT_SECRET`（本机可写 `config/.env`），不经用户界面或授权接口设置；保留旧主库密钥读取，下一次保存时以指纹替换 |
| 跨境巴士访问及刷新令牌、用户授权和企业密钥指纹 | 主库 `runtime_secrets` 的 `credentials:crossborderbus` 命名空间，由 `ConfigStore.crossborderbus_credentials/save_crossborderbus_credentials` 绑定当前企业配置 |
| 微信绑定凭据 | 主库 `runtime_secrets` 的 `credentials:wechat` 命名空间；渠道库仅保存凭据引用 |
| 订单回调令牌及公网回调地址 | 主库 `runtime_secrets` 的 `order_notifications` 命名空间；已有令牌原值保留 |
| 非敏感应用、店铺设置 | `config/app_config.json`、`config/store_config.json`，由 `ConfigStore` 管理 |
| Agent 提示词、记忆、平台预设、运费资料 | 原有配置文件，由对应配置模块管理，不属于授权 |
| 物流地址、履约规则、订单收货仓快照 | 订单库，由物流和履约 Store 管理；地址版本校验与订单创建占位需要同一事务，不能为集中配置而拆开 |
| 跨境巴士合作目录、订单同步进度、微信游标与收发记录 | 各领域库中的缓存或运行状态，不作为授权来源 |
| 浏览器登录状态、外部 CLI 登录 | 浏览器资料目录或外部工具自管；不解析、复制或纳入 ERP 密钥表 |
| `.env`、进程环境变量 | 显式部署输入；不作为应用内保存授权的另一个写入位置 |

旧 `fulfillment_warehouse_links` 和 `yandex_pricing_warehouses` 记录继续保留，不因不再使用而删除。图床测试回执属于验证状态，既有主库存储不变。

集中管理不等于数据库加密：数据库和备份含敏感信息，应按私有数据保管，不加入安装包或版本控制。

## 微信绑定一致性

新绑定先写入主库中的独立凭据记录，再在渠道库的同一事务中切换凭据引用、绑定代次和收发队列；提交后才删除旧凭据。中断最多留下主库中未被引用的记录，不会删除仍在使用的旧凭据。读取到缺失的凭据引用直接报错，不静默解绑或使用另一个账号。渠道服务拿到的内部状态仍包含解析后的凭据，数据库中的渠道状态不再包含凭据本体。

## 已有数据升级

这是持久化格式迁移，不是配置导入功能。主库继续使用版本 17 的现有表结构。升级旧安装前，停止使用目标数据目录的服务，再运行：

```sh
.venv/bin/python scripts/migrate_credentials.py /绝对路径/应用数据目录
```

开发环境的数据目录是仓库根目录；macOS 桌面版是 `~/Library/Application Support/Champion ERP/backend`，带空格时需加引号。两者分别迁移，不互相复制。

脚本锁定现存主库、订单库和微信库，验证主库结构及凭据冲突，在 `data/backups/credentials-<时间>/` 保存完整数据库备份；主库凭据提交成功后才清理业务库旧位置。所有业务记录、会话绑定、游标和令牌原值保留。重复执行不会轮换令牌；遇到不同凭据拒绝覆盖。若中途退出，可重新运行核对并继续。启动检查在后台任务之前拒绝未迁移的旧布局，运行时不双写或回退到旧库。

更新程序时同时完成该环境的数据迁移。需要退回旧程序时，先停止所有进程，再从同一次迁移备份恢复所有相关数据库；不能只恢复主库或只换回旧程序。迁移后产生的新业务数据不会出现在旧备份中，不应直接用旧备份覆盖继续使用中的数据。

回归覆盖迁移前后凭据与业务数据相等、冲突不覆盖、中断后重跑、微信绑定事务失败及并发回调令牌初始化，见 `tests/test_credential_migration.py`。
