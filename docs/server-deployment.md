# 服务器部署、账号与 New API 验收

## 当前部署

2026-11-06 已按用户选择，在既有 `107.174.48.241` 完成 ERP 个人账号版与修改版 New API 的内网部署。管理员邮箱为 `huandao-gonglu@qq.com`。两个服务独立运行、独立持久化，业务端口只监听回环地址；本次未配置公网 HTTPS 或官方支付商户。

| 内容 | 实际版本或位置 |
| --- | --- |
| ERP 运行代码 | `a00f45f9bdca0b408e75fee44cc655ae4c10e761` |
| ERP 发布目录 | `/opt/champion-erp/releases/20261106-account-newapi` |
| ERP 程序入口 | `/opt/champion-erp/current`，指向上述发布 |
| Python 环境 | `/opt/champion-erp/venv`，Python 3.12.3；账号版增加 `argon2-cffi` |
| ERP 数据 | `/var/lib/champion-erp`，账号库 `accounts.sqlite3`，原管理员业务数据保留原位，普通用户空间位于 `users/<用户ID>/` |
| ERP 服务 | `champion-erp.service`，开机自启，以专用账号 `champion-erp` 运行 |
| ERP 监听与本机入口 | `127.0.0.1:5050`；Mac 访问 <http://127.0.0.1:15050> |
| New API 源码 | 同级独立仓库 `/Users/hdgl/work/new-api`，分支 `codex/erp-integration` |
| New API 上游 | 官方 `v1.0.0-rc.48`，提交 `706144769e5bed5a47b78721e3bb11da3cf4c0b9` |
| New API 改造提交 | `515f0fa7ab8e5ec4bb50594432861f2870b17128` |
| New API 运行版本 | `v1.0.0-rc.48-erp.515f0fa7ab8e`，Linux/amd64 |
| New API 镜像 | `champion/new-api:515f0fa7ab8e5ec4bb50594432861f2870b17128` |
| 本地镜像 ID | `sha256:dba131776de0ac12c4d979382e77895ed69d6601165b4500563d9ac93a6c3d35`；这是本机构建镜像 ID，没有镜像仓库 RepoDigest |
| New API 部署与数据 | `/opt/new-api/deploy`；`/var/lib/new-api/new-api.db`，运行身份 UID/GID 10001，数据目录权限 0700 |
| New API 服务 | Docker 容器 `champion-new-api`，重启策略 `unless-stopped`，健康检查通过 |
| New API 监听与本机入口 | `127.0.0.1:3000`；Mac 访问 <http://localhost:13000> |
| ERP 桥接配置 | `/etc/champion-erp/newapi.env`，root 0600；systemd drop-in 显式加载 |
| New API 配置 | `/etc/new-api/runtime.env`，root 0600；会话密钥与 RPC 凭据分别生成 |

修改前已按要求提交 ERP 工作区，保存点为 `c929080`。New API 的源码、依赖和部署工具均在独立仓库，不进入 ERP 项目目录。服务器只发布修改版镜像，没有使用官方 `latest` 替代实际改造产物。

### 登录与桌面程序

初始管理员密码由服务器生成，存于 `/etc/champion-erp/initial-admin-password`，只有 root 可读；不写入代码、聊天或日志。通过既有 SSH 登录服务器读取，首次登录后可在「个人账号」修改密码。该文件只记录初始化密码，不随改密更新。

ERP 管理员 ID 为 `3552abddc46b46a2baad9bce26f4dc87`，已由服务器 CLI 明确绑定 New API 原生 root 运营账号。普通交接固定创建普通计费用户，ERP 管理员角色不会自动提权。运营账号重复绑定已实测幂等。

从 ERP 的「个人账号 → Token 与账单」打开 New API，在 New API 页面生成关联码，回到已登录的 ERP 核对并确认，再在 New API 完成交接。桌面主进程与系统浏览器不共享 Cookie，使用同一流程；新用户仍在 ERP 注册。

`/Applications/Champion ERP.app` 已更新为本次账号与 New API 版，版本号沿用 `1.2.5`。实际安装的 `app.asar` 与本次构建摘要一致：`de0cbf66bcc707ff0e52c0513c22c11772daa762788d7c0cd71edcc4a16e9531`。旧程序备份在 `/Users/hdgl/Library/Application Support/Champion ERP/install-backups/20261106-150452/Champion ERP.before-account-newapi.app`。本机构建尚未签名、公证。本机浏览器资料、CLI 和本机模型设置继续保存在原电脑。

### SSH 入口

Mac 的两个通道由 LaunchAgent 在用户登录后建立，断线后自动重连；使用现有密钥，不上传私钥或转发 SSH Agent：

| 通道 | LaunchAgent | 转发 |
| --- | --- | --- |
| ERP | `/Users/hdgl/Library/LaunchAgents/com.champion.erp.cloud-tunnel.plist` | `127.0.0.1:15050 → 服务器 127.0.0.1:5050` |
| New API | `/Users/hdgl/Library/LaunchAgents/com.champion.newapi.cloud-tunnel.plist` | `127.0.0.1:13000 → 服务器 127.0.0.1:3000` |

日志位于 `/Users/hdgl/Library/Application Support/Champion ERP/logs/` 下的 `cloud-tunnel.log`、`cloud-tunnel.error.log`、`newapi-tunnel.log` 和 `newapi-tunnel.error.log`。桌面 `connection.json` 使用 `http://127.0.0.1:15050`。

ERP 使用主机名 `127.0.0.1`，New API 使用 `localhost`，必须按上表地址访问；不同端口不能隔离 Cookie。SSH 登录此服务器需显式使用 `-p 22`。其他电脑需要自己的 SSH 授权及相同转发端口，不能使用这台 Mac 的回环地址。

正式公开部署仍需两个独立 HTTPS 主机名、有效证书、Secure Cookie、精确 Origin 和可信代理；完成后再切换双方公开地址。当前 HTTP 只在回环地址及 SSH 加密通道内使用，ERP/New API 端口均未直接对公网开放。

## 本次实际验收

本次通过实际服务、API、CLI 和安装包内主进程模块验证；未操作图形界面。

- ERP 管理员网页登录、New API 运营交接成功；两个普通验收账号分别登录、建立原生计费用户，同一 ERP 账号的网页与实际 Electron 安装包内主进程代理映射到同一个 New API 用户。
- 独立浏览器 Cookie jar 无法消费另一浏览器的交接，授权前保持等待、完成后拒绝重放；交接响应只返回固定站内目标，未返回长期凭据。
- 两个账号能管理自己的模型 Key，跨账号列举及读取被拒绝。仅用监听服务器回环地址的模拟上游测试 `gpt-4o-mini`，没有产生真实模型费用。
- 第一次模拟调用的 100 输入 / 20 输出 token，使 A 的余额与消费日志一致扣减 14 quota，B 的余额和消费日志不变。生命周期验收共成功调用三次，留下三条消费记录、合计 42 quota。
- 退出桌面 ERP 撤销该会话关联的 New API 浏览器会话及刷新能力，另一 ERP 设备仍有效；ERP 改密撤销全部关联浏览器会话，模型 Key 保留。禁用后模型请求被拒绝且未到达模拟上游，重新启用可用已有 Key，旧浏览器会话不会复活。
- 敏感渠道 Key 查看必须重新验证 ERP 密码；可在同一账号的另一 ERP 设备确认，随后使用原生用途绑定 Proof，使用一次后拒绝重放。已有 MFA 不降级由专项回归覆盖。
- 实际请求本地密码登录、注册、找回密码、OAuth 和角色提升入口均返回拒绝。在线支付下单保持关闭；原生支付回调及验签没有被此策略整体封禁。
- 实际停止 New API 后，ERP 仍可登录、禁用用户，返回同步待完成；恢复后顺序重试完成，待禁用用户的原生会话已撤销。退出 New API 不影响 ERP 登录。
- 两套备份均在独立目录解包并验证；正式镜像连续启动两次，用户、绑定、Key、会话、余额和消费记录与备份一致，运营绑定重复执行没有重建用户。
- 原管理员业务库的 20 张表与账号初始化前备份逐一比对主键，全部保留，包括 2 个商品、2 个草稿、2 个发布任务和 15 条对话历史。没有再次运行桌面迁移器或清库。
- 临时模拟渠道已删除，两个验收账号已禁用，历史账单保留。真实模型渠道需由运营者在 New API 配置后使用；当前未接入官方支付宝/微信商户，未执行实际收款。

### 开发回归与数据库矩阵

| 范围 | 最终结果 |
| --- | --- |
| ERP 全后端 | `4141 passed, 53 subtests passed`，包含编译和架构边界验证 |
| ERP 前端 | 128 个文件，1126 个测试通过；类型、API 类型检查和网页/桌面构建通过 |
| ERP 桌面 Node | 14 个测试通过；实际安装包内账号代理完成真实服务联调 |
| New API 相关 Go 包 | `controller`、`model`、`middleware`、`service`、`router` 全部通过 |
| New API 前端 | 203 个文件、2465 个测试通过；类型检查、相关文件 lint、完整生产构建通过 |
| SQLite 3.50.4 | 新库及官方发布库升级，各连续启动两次；原数据及旧、新索引保留 |
| PostgreSQL 18.6 | 真实测试实例交接/撤销/Proof 回归通过；新库及官方发布库升级，各连续启动两次 |
| MySQL 8.4.13 | 真实测试实例交接/撤销/Proof 回归通过；新库及官方发布库升级，各连续启动两次 |

服务器验收摘要保存在 `/opt/new-api/deployment-records/20261106/acceptance.json`，六项迁移结果位于同目录 `migrations/`。仅保存场景、计数和摘要，不包含可用登录或模型凭据。独立测试数据库及模拟上游已清理。

迁移矩阵使用固定官方镜像 `calciumion/new-api:v1.0.0-rc.48@sha256:51401a46510bf110ffafdf2cd7177e0b80bbcf02439e69b665c696f330e87ae5` 创建旧库，并核对其 revision 为上述上游 SHA。升级保留旧用户、模型 Key、余额、日志、会话、配置及索引。测试库与实际运行库隔离。

全量 `go test ./...` 有一个未通过项：`relay/channel/advancedcustom/TestAdaptorMasksDialedHostsInDialFailure/channel_proxy_host`。当前环境返回 `unexpected EOF`，用例期待代理主机名；已在未修改的官方提交独立复现。账号相关五个 Go 包全部通过，但不能把此次结果表述为全仓测试全部通过。ERP lint 的 16 项及 New API 原有页脚的一项提示是现有警告，没有新增 lint 错误。

上述验证覆盖本次账号和部署范围，不代表正式 HTTPS、商户支付或完整 ASVS 认证已经通过。开发依赖与安全参考以两仓库实施说明为准。

## 镜像与对应源码

- 镜像归档：`/opt/new-api/artifacts/new-api-515f0fa7-linux-amd64.tar.gz`，Linux/amd64，84,313,000 字节。
- 归档 SHA256：`2b73ea148ac1e678447f02de7c9bbcacbc4c5cf0373dfadd861002f24308ea10`，同目录另有 `.sha256` 文件。该摘要对应镜像归档，区别于上表镜像 ID。
- 网络使用者可从 <http://localhost:13000/source/new-api.tar.gz> 或登录页、页脚获取修改版源码。实际下载与构建归档 SHA256 都为 `ac0ce160b57cd7602a11c0fcc573c9c5954615091fe648a961ecfe2ded4897c2`，7,140,150 字节，内含 `SOURCE_REVISION=515f0fa7ab8e5ec4bb50594432861f2870b17128`。
- 原 AGPL-3.0、NOTICE、第三方声明及 New API / QuantumNous 标识保留；源码归档不含 `.git`、数据库、运行配置或真实凭据。
- 独立源码仓库的 `ERP-INTEGRATION.md` 与 `deploy/erp/` 提供入口边界、构建、运行配置示例、运营绑定、迁移及备份工具。

## 原桌面数据迁移记录

数据来源为 `/Users/hdgl/Library/Application Support/Champion ERP/backend`。切换前已正常停止旧桌面及其业务后端，并使用 SQLite 一致性备份生成快照。

- 保留 2 个商品、2 个平台草稿、2 个发布任务和 15 条对话历史，以及原有授权与业务凭据。
- 启动服务器业务进程前，逐一核对 10 个 SQLite 数据库的表记录数量、主键摘要和完整性；均通过。
- 113 个素材文件逐一核对 SHA256；原桌面目录下的持久化路径已转换到服务器业务目录。
- 已通过本机 SSH 通道验证网页、商品接口及 3 张真实商品图片；重启服务器服务后仍能读取 2 个商品。
- 已通过实际安装包中的执行器验证服务器请求/响应及本机配置读取，关联 1 个本机模型；本次验收未调用模型推理、浏览器界面或店铺写入。

迁移报告位于 `/var/lib/champion-erp/desktop-migration-report.json`，服务器另存 `/srv/champion-erp/migration-20261106-093745.json`。

跨电脑迁移时，上传目录与数据库中记录的原电脑目录不同，应使用迁移器的 `--source-prefix`：

```sh
python scripts/migrate_desktop_business.py \
  --source '/服务器/来源快照' \
  --source-prefix '/Users/hdgl/Library/Application Support/Champion ERP/backend' \
  --target '/新的业务目录' \
  --backup '/新的备份目录' \
  --local '/迁移输出/本机配置'
```

`--source-prefix` 只指定待转换的原始路径前缀；备份仍读取 `--source`。本机配置输出应通过 `desktop_runtime.config.LocalConfig` 保存回原电脑，浏览器登录资料继续留在原电脑。已部署的数据不要再次执行迁移。

## 账号切换记录

账号版已实际部署，原匿名发布 `/opt/champion-erp/releases/20261106-093745` 留存为历史版本。不要在当前数据上重复初始化管理员或重新执行桌面迁移。

管理员初始化时，命令先对原业务数据做 SQLite 一致性备份，位置为 `/var/lib/champion-erp/backups/before-accounts-20261106T064507875999Z.tar.gz`；初始化元数据位于 `/etc/champion-erp/admin-bootstrap.json`。密码经受控标准输入传递，没有放入进程参数。原库、素材、业务 ID 和凭据未重新创建。

后续发布使用独立目录，安装依赖并确认服务账号可读后切换 `current`；先验证 `/api/account/session` 返回已初始化，再执行依赖 ERP 内部接口的运营绑定命令。`systemctl start` 返回不代表应用 HTTP 已就绪。运营绑定是显式运维操作，不是每次用户登录时运行的步骤。

## 运维

登录服务器后执行：

```sh
systemctl status champion-erp.service
journalctl -u champion-erp.service -n 100 --no-pager
systemctl restart champion-erp.service
cd /opt/new-api/deploy
docker compose ps
docker compose logs --tail 100 new-api
docker compose restart new-api
```

ERP 应用日志另存 `/var/lib/champion-erp/data/logs/backend.log`。代码和环境由管理员维护，业务数据归相应运行身份。New API 容器只读根文件系统、丢弃 capabilities，日志轮转，资源限制见独立仓库 `deploy/erp/compose.yaml`。

本机通道状态可用 `launchctl print gui/$(id -u)/com.champion.erp.cloud-tunnel` 和 `launchctl print gui/$(id -u)/com.champion.newapi.cloud-tunnel` 查看。需要重建时对相应服务执行 `launchctl kickstart -k`。服务器重启会结束当时的桌面连接，重新打开桌面程序后建立新连接；进行中的本机操作不自动重放。

## 备份与恢复

本次验收后的两套快照均为 root 0600，包含配置与凭据，不公开分发：

| 快照 | 位置 | 已验证内容 |
| --- | --- | --- |
| ERP | `/srv/champion-erp/backups/20261106-account-newapi.tar.gz` | 75,048,903 字节；账号库、原管理员与全部普通用户业务库、配置、素材、桥接配置；逐文件 SHA256、各 SQLite 完整性 |
| New API | `/srv/new-api/backups/20261106-account-newapi.tar.gz` | 26,651 字节；用户、身份绑定、会话、模型 Key、余额、日志及全部其他表、运行配置；逐表行数及摘要 |

已在停止写入后生成快照，并在独立临时目录恢复校验，未覆盖运行库。SQLite 使用 backup API，未直接复制活动主文件或忽略 WAL。当前运行库不因恢复演练被回写。

后续手工备份及验证：

```sh
# ERP 的多个业务库需要停止服务，输出必须位于业务目录外。
systemctl stop champion-erp.service
/opt/champion-erp/venv/bin/python /opt/champion-erp/current/deploy/backup_accounts.py backup \
  --archive /srv/champion-erp/backups/<新的时间>.tar.gz
systemctl start champion-erp.service
/opt/champion-erp/venv/bin/python /opt/champion-erp/current/deploy/backup_accounts.py verify \
  --archive /srv/champion-erp/backups/<新的时间>.tar.gz

python3 /opt/new-api/deploy/backup.py backup --archive /srv/new-api/backups/<新的时间>.tar.gz
python3 /opt/new-api/deploy/backup.py verify --archive /srv/new-api/backups/<新的时间>.tar.gz
```

验收记录是已完成的快照与恢复演练，当前没有配置周期备份任务。正式恢复前先停止写入、另存当前数据，再解包到新目录，按脚本清单校验、恢复运行身份和私有文件权限后切换数据路径；不要覆盖正在使用的目录。ERP 和 New API 分别备份，不能只恢复账号库、只恢复业务库或用源码镜像代替账务数据。涉及数据库迁移时，切回旧镜像并不自动回退数据库。

原桌面迁移快照继续保留：

- Mac：`/Users/hdgl/Library/Application Support/Champion ERP/migration-backups/20261106-093745`，含来源、摘要及更早的安装程序。
- 服务器：`/srv/champion-erp/backups/20261106-093745`；原上传来源另存 `/srv/champion-erp/staging/20261106-093745/source`。

这些历史快照均不包含之后的新记录。原桌面库不得重新启用为另一套同时写入的业务库。
