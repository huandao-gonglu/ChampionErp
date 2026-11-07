# 服务器部署、账号与 New API 验收

## 统一编译与发布

在构建用 Mac 的项目根目录执行以下命令。默认发布服务器、网页和该 Mac 架构的可选桌面更新。发布前先准备好新版号、提交代码，脚本从干净的 Git 版本构建。首次分发需先完成下方的[更新站点配置](#桌面更新发布服务)。

```sh
.venv/bin/python scripts/release.py
```

默认执行后端、前端和桌面全量测试。需要跳过这三套测试时，显式传入 `--skip-tests`。此参数也可与 `--server-only` 合用。第三方声明、生成契约、构建和发布验收仍会执行，测试选项会保存在 `release.json` 中。

```sh
.venv/bin/python scripts/release.py --skip-tests
```

服务器包上传完成后，若等待任务超时、首次接入需安排维护，或公网验证、桌面包分发中断，可以用同一发布目录继续。脚本校验版本与包摘要，复用已经准备好的环境。

```sh
.venv/bin/python scripts/release.py --resume '/发布记录所在目录'
```

默认目标为当前 SSH 服务器及 `https://hdgl.cc.cd`。SSH 使用现有密钥并校验已知主机，不转发 Agent。`--help` 可查看服务器、密钥、发布记录目录与任务等待时间参数。

脚本依次检查第三方声明，运行后端全量、前端、桌面及生成契约回归，编译网页和独立本机执行器，再生成 macOS 应用包。只上传 Git 跟踪的源码与本次网页构建，不上传开发配置、数据库、浏览器资料或本机依赖缓存。

服务器为每个发布目录创建独立 `.venv`，安装依赖并完成编译检查。准备完成后，统一发布脚本按下列顺序切换。

1. 暂停接收新的业务提交和后台任务领取。网页仍可查阅任务，桌面执行器可继续上传素材和回传结果。此时提交新任务返回 HTTP 503，用户可在发布结束后重新提交。
2. 等候已接收的业务请求、AI 对话和工具、图片批次、调研及正在执行的领域任务收尾。商品发布按平台提交线程登记，直到平台受理回执和本地提交结果保存。已提交且只等平台确认的商品、已持久化的待审批对话、可以重新领取的队列记录均可跨版本继续等待。
3. 达到安全停机点后，停止 ERP，使用 `deploy/backup_accounts.py` 备份并恢复校验各业务空间，再切换 `current` 并启动新版。验收通过后开放任务入口。验收失败时，在入口仍暂停的情况下恢复上一个程序目录和启动配置。不可逆数据库迁移仍需独立的恢复步骤。

默认最多等待一小时。可用 `--wait-tasks-seconds` 调整，设为 `0` 时只允许当前已空闲的服务立即切换。等待超时会打印剩余任务类型，原服务自动恢复接收任务。使用上面的 `--resume` 命令可再次等待同一发布包。

**首次从旧版接入**时，旧进程尚无活动任务登记。脚本先准备新版，检测到旧服务仍在运行就退出。安排一次维护窗口，暂停用户和外部任务来源，确认已有任务收尾后，在服务器执行 `systemctl stop champion-erp.service`，再从 Mac 用 `--resume` 继续。之后统一发布会通过 systemd 的 `release-python.conf` 自动配置 `ERP_RELEASE_GUARD_DIR`。直接执行 `systemctl restart` 会跳过发布等待。

健康检查包含真实临时管理员会话的共享设置、运费表、禁售名单及匿名拒绝检查，结束后撤销该会话。公网页面和 JS 入口须匹配本次构建摘要。公网验收成功后，脚本分发经过签名完整性和执行器检查的桌面包，校验公网更新清单后结束。构建用 Mac 与其他客户端一样，通过“文件 → 检查桌面更新”取得新版。

构建日志、服务器包及 `release.json` 保存在 `~/Library/Application Support/Champion ERP/releases/<发布标识>/`，服务器数据备份保存在 `/srv/champion-erp/backups/<发布标识>/`。安装新版后沿用原有 `Champion ERP` 本机配置和浏览器资料目录。

## 桌面更新发布服务

安装包和版本清单由现有 Nginx 通过 HTTPS 提供，在 ERP 切换后端进程期间也能下载。首次启用时，将本仓库 Nginx 模板中 `location ^~ /desktop-updates/` 合入 ERP 的 443 站点，创建 `/var/www/champion-erp/desktop-updates`（管理员可写，Nginx 和 ERP 可读），通过 `nginx -t` 后重载。发行站点固定在构建输入 `front/package.json` 的 `desktopUpdateOrigin`；自托管发行时须同时配置这个地址和 `--public-url`。

每次分发新安装包先提高 `front/package.json` 的版本号，重新生成第三方声明，并按统一发布流程提交代码。在目标 Mac 执行以下命令之一。

```sh
.venv/bin/python scripts/release.py --update-notes '本次更新内容'
.venv/bin/python scripts/release.py --desktop-update required --update-notes '本次升级原因'
```

只更新后端或网页版时，执行 `.venv/bin/python scripts/release.py --server-only`，跳过桌面构建和分发，现有桌面版本策略继续生效。脚本从实际应用可执行文件读取架构，生成 DMG，在公网网页验收成功后上传到服务器。发布程序按 SHA-256 复核内容，将安装包放入按平台、架构和版本区分的固定目录，检查公网分段下载，再原子更新该目标的 `latest.json`。脚本在安装依赖前检查目标架构的线上版本，并在打包后用实际架构复核。遇到版本冲突时，提高版本号后重新发布。

- **可选**保留原先的最低可用版本。比如 1.3.0 曾设为强制，1.3.1 设为可选，1.3.0 可以稍后升级，1.2.9 仍需升级。
- **强制**把该平台和架构的最低版本提高到本次版本。支持更新协议的客户端在启动时执行策略，已打开的客户端定时检查；业务后端在接收带版本的新提交时也检查同一清单。已发送的桌面调用、素材和回执继续处理。
- **撤回强制**可在服务器修改当前清单，保留原安装包。以下示例要求当前线上目标恰为 1.3.1，版本已变会拒绝覆盖。客户端下次检查后取得新策略。

```sh
python3 /opt/champion-erp/current/deploy/publish_desktop.py --policy-only \
  --platform darwin --arch arm64 --version 1.3.1 --policy optional --minimum 0.0.0
```

首次安装支持版本检查的客户端需手动下载。Mac Apple 芯片的安装地址形如 `/desktop-updates/darwin-arm64/1.2.8/installer.dmg`。旧客户端未上报桌面版本，网页也不参与桌面最低版本检查。Windows 的 NSIS 安装包和 Linux 的 AppImage 需在对应构建环境生成，再在服务器用同一 `publish_desktop.py` 提供 `--platform win32|linux`、`--arch`、`--version`、`--installer`、`--sha256`、`--public-url` 和 `--policy` 发布。每个已发布架构有独立的清单。

## 当前公网部署（2026-11-07）

网页版、业务后端及 Apple 芯片 Mac 可选更新已发布 **1.2.13**（`20261107t122406z-3325c4004ae7`）。管理员可在 1688 授权页粘贴完整回调地址，由服务器完成换码。已验证公网资源、桌面更新清单和服务器端管理员请求校验，真实 1688 换码需管理员在浏览器完成登录。[下载 Mac 安装包](https://hdgl.cc.cd/desktop-updates/darwin-arm64/1.2.13/installer.dmg)

## 1.2.12 发布记录（2026-11-07）

网页版、业务后端和 Apple 芯片 Mac 可选更新为 **1.2.12**，发布记录为 `20261107t095635z-ce769a5f4610`。1688 商品、采购、订单和物流统一使用跨境 ERP 服务商应用 4848173。企业自用应用已从 ERP 退役。备份恢复、登录设置接口、公网资源和桌面更新清单均通过验证。[下载 Mac 安装包](https://hdgl.cc.cd/desktop-updates/darwin-arm64/1.2.12/installer.dmg)

发布时使用生产回调 `https://hdgl.cc.cd/auth/alibaba/callback` 的授权链接仍被 1688 拒绝。客户真实授权需在开放平台确认已审核的回调和订购入口。

## 1.2.11 发布记录（2026-11-07）

网页版、业务后端和 Apple 芯片 Mac 桌面更新为 **1.2.11**，发布记录为 `20261107t084447z-0ad6cec2929a`。此版修复 macOS 默认浏览器为 Chrome 时，1688 授权只出现 Dock 图标的问题。已在有无头 Chrome 实例运行的 Mac 上验证新窗口可见且获得焦点。[下载 Mac 安装包](https://hdgl.cc.cd/desktop-updates/darwin-arm64/1.2.11/installer.dmg)

## 1.2.10 发布记录（2026-11-07）

公网 ERP 网页、业务后端和 Apple 芯片 Mac 桌面更新已发布 **1.2.10**，发布记录为 `20261107t082738z-89449751fd6c`。公网首页加载 `/assets/index-DP16RMPL.js`，已核对页面版本与 1688 浏览器授权界面。桌面采用可选升级。[下载 Mac 安装包](https://hdgl.cc.cd/desktop-updates/darwin-arm64/1.2.10/installer.dmg)

采购和跨境应用的 ERP 生产回调地址均已更新为 `https://hdgl.cc.cd/auth/alibaba/callback`，服务切换后已回读确认。使用原授权的账号需重新登录 1688。

## 1.2.8 发布记录（2026-11-07）

后端、网页版和 Apple 芯片 Mac 桌面版 **1.2.8** 已发布，代码提交为 `d70e926b7f75`。首次切换在维护窗口完成，当前服务已启用任务准入和安全停机协调。

- 发布记录为 `20261107t074511z-d70e926b7f75`，状态为 `complete`。对应业务备份在 `/srv/champion-erp/backups/20261107t074511z-d70e926b7f75/accounts.tar.gz`，恢复检查通过。公网账号入口、网页资源摘要、管理员设置和匿名访问检查通过。
- 桌面策略为可选升级。已从公网完整下载并校验 [Apple 芯片 Mac 安装包](https://hdgl.cc.cd/desktop-updates/darwin-arm64/1.2.8/installer.dmg)，大小为 151757340 字节，SHA-256 为 `ee82f69818c036b1a12a5a5e5dff75bb292df98d31d5541d515f5467f8d0b366`。1.2.7 用户需手动安装一次。

## 1.2.7 发布记录（2026-11-07）

ERP 与本机桌面当时发布为 **1.2.7**，应用代码提交 `34bcbc5a2c0a3b88eba951a2002a4ee096f0ea1d`。统一发布脚本已完成编译、服务器备份与切换、公网资源核验以及本机应用替换，状态为 `complete`。

- 服务器目录为 `/opt/champion-erp/releases/20261107t040708z-34bcbc5a2c0a`，备份为 `/srv/champion-erp/backups/20261107t040708z-34bcbc5a2c0a/accounts.tar.gz`，恢复摘要和数据库完整性验证通过。
- 本机 `/Applications/Champion ERP.app` 显示版本为 `1.2.7`。旧包位于 `/Applications/.Champion ERP-20261107t040708z-34bcbc5a2c0a.backup.app`。构建与发布记录保存在 `/Users/hdgl/Library/Application Support/Champion ERP/releases/20261107t040708z-34bcbc5a2c0a/`。
- 已通过真实管理员会话读取新增运费规则接口，Yandex 22 条、Ozon 6 条，版本及数量与当前生效表一致。公网网页引用 `/assets/index-B6MPGpey.js`，内容摘要匹配本次构建。已安装执行器完成就绪、配置读取和 EOF 退出校验。
- 独立构建完成后端 `4176 passed, 53 subtests passed`、前端 131 个文件共 1143 个测试、桌面 Node 回归、生成契约检查与 Web/macOS 构建。

系统设置的「国际物流运费表」现提供当前表及管理员默认表的查看入口，直接读取已有导入数据，展示来源、版本、费用、适用限制并支持搜索与分页。网页刷新或桌面重开后可用。

## 1.2.6 发布记录（2026-11-07）

ERP 与本机桌面已通过统一脚本发布为 **1.2.6**，应用代码提交为 `f9cc6bda6e030f2e99ac793dae3af59bd6110893`。发布脚本随后在 `708b8a4` 补充明确的 HTTP 客户端标识和断点继续，通过已构建包完成公网验收及桌面安装。

| 项目 | 当前位置或结果 |
| --- | --- |
| ERP 公网入口 | <https://hdgl.cc.cd/> |
| ERP 运行目录 | `/opt/champion-erp/releases/20261107t024300z-f9cc6bda6e03`，由 `current` 指向 |
| ERP Python 环境 | 当前发布目录内 `.venv`，通过 `release-python.conf` 加载，Python 3.12.3 |
| 服务器备份 | `/srv/champion-erp/backups/20261107t024300z-f9cc6bda6e03/accounts.tar.gz`，75,123,327 字节，恢复摘要及 SQLite 完整性验证通过 |
| 本机应用 | `/Applications/Champion ERP.app`，显示名称 `gongluErp`，版本 `1.2.6` |
| 本机旧包 | `/Applications/.Champion ERP-20261107t024300z-f9cc6bda6e03.backup.app` |
| 发布记录和构建产物 | `/Users/hdgl/Library/Application Support/Champion ERP/releases/20261107t024300z-f9cc6bda6e03/` |

固定提交的独立构建完成后端 `4173 passed, 53 subtests passed`、前端 130 个文件共 1138 个测试、桌面 Node 回归、生成契约检查及 Web/macOS 构建。发布脚本的公网请求、恢复入口、安装失败回退与并行保护共 7 项专项回归通过。

服务器切换后已用实际管理员临时会话读取共享设置、两平台运费表和禁售名单，验证会话已撤销。当前共享 Yandex 运费表为 `yandex-d1749fe4d569da571a33`，22 条规则；Ozon 为 `ozon-2abffa086b075eb2e375`，6 档规则；禁售名单为 555 个类目。匿名管理员入口被拒绝。公网账户入口就绪，网页引用 `/assets/index-CPxEk4Dp.js`，响应内容 SHA256 与构建结果一致。

本机新应用 ASAR SHA256 为 `d3d077aa3c729b755b2370596a88e6f21dbff0a3719d8f624193b3792dcd959c`，已与构建包核对，并从实际安装目录启动独立执行器验证就绪、空临时配置读取与 EOF 正常退出。发布状态记录为 `complete`。新桌面在用户重新打开后进入登录及业务流程。

## 域名与网关部署记录（2026-11-06）

| 服务 | 用户入口 | Nginx 上游 |
| --- | --- | --- |
| ERP | <https://hdgl.cc.cd/> | `127.0.0.1:5050` |
| New API | <https://hdgl.cc.cd:8443/> | `127.0.0.1:3000` |

DNSHE 将 `hdgl.cc.cd` 委派给 `damien.ns.cloudflare.com` 和 `emerie.ns.cloudflare.com`。Cloudflare Free 的根域 A 记录代理到 `107.174.48.241`，回源模式为 Full (strict)。域名证书由 Let's Encrypt 签发，位于 `/etc/letsencrypt/live/champion-erp-domain/`，模拟续期通过。用户经旧 IP 打开网页会转到对应端口的域名。

ERP 运行提交为 `af05c4fdb6b6d584eab28e31dfde2203ffde1208`，New API 镜像为 `champion/new-api:89bda6caa5664f4cc0fc2abf0f634dc0ad4518a8`。两端数据备份及变更前的运行配置位于 `/srv/champion-erp/backups/20261106T122451Z-domain/`。对应 New API 镜像归档位于 `/opt/new-api/artifacts/new-api-89bda6ca-linux-amd64.tar.gz`，SHA256 为 `874bc8177c828774c437651d34a28d32b6ca886793a3eceb32003b014fecbb2b`。

公开的 ERP 构建资源由 Nginx 直接提供并由边缘节点缓存。业务响应使用 `private, no-store`。同一台 Mac 测得模型配置接口从旧入口约 6–8 秒降到 0.62–0.67 秒，三次均为 gzip 和 `CF-Cache-Status: DYNAMIC`。同一桌面连接流程包含本机配置回传，单次对照从 9.99 秒降到 1.57 秒；本机文件读取均在 3 毫秒以内。静态资源第二次读取命中香港边缘节点。模型面板在一分钟内复用当前账号已读的完整配置，并将并发读取合并。

ERP 原模型目录新增两条系统模型，即文本模型 `dsflash`（`deepseek-flash`）与图片模型 `qwenflash`（`qwen-image-3.0`）。管理员原有 7 条模型和 5 项功能绑定与服务器持久化数据逐项匹配，现共 9 条模型。普通账号的个人模型支持添加、使用独立凭据测试、绑定和删除。两条系统模型支持原设置中的改名、启停和能力测试，服务端保护其 ID、连接信息及删除操作。运营账号和普通账号均已验证自动登录和各自的 default 分组托管密钥。查询有效绑定账号时缺失托管密钥数与重复数都为零。生产文本、出图及编辑请求均通过网关鉴权，零余额验收账号得到 `insufficient_user_quota`，验收后已停用。本地用两个账号和模拟上游分别跑通三种调用与结果解析。真实上游输出和成功扣费需使用有余额的账号验收。

域名站点配置为 `deploy/nginx/champion-cloudflare.conf`，在服务器上以 `sites-enabled/champion-zone-hdgl` 加载，排在提供公共日志格式的 `champion-ip-https` 后。真实客户端 IP 仅信任从 Cloudflare 官方 `ips-v4` 和 `ips-v6` 清单生成的 `/etc/nginx/snippets/champion-cloudflare-realip.conf`。反代按端口分别筛选 Cookie，两端均封住 `/api/internal/`。IP 和域名的证书均由 `champion-certbot-renew.timer` 维护。

已在域名上验证 ERP 登录、自动 POST 交接、进入 New API 的 `/keys`、运营身份和退出联动，以及匿名模型接口和错误 Origin 的拒绝。本机桌面 `connection.json` 已切换到域名，退出应用后安装待更新包。

## 公网切换前的内网部署记录

以下入口、镜像与快照对应此前的 SSH 部署。

2026-11-06 已按用户选择，在既有 `107.174.48.241` 完成 ERP 个人账号版与修改版 New API 的内网部署。管理员邮箱为 `huandao-gonglu@qq.com`。两个服务独立运行、独立持久化，业务端口只监听回环地址；本次未配置公网 HTTPS 或官方支付商户。

| 内容 | 实际版本或位置 |
| --- | --- |
| ERP 运行代码 | `cb4364b5ba118fd407e23e73d6e602268733aacc` |
| ERP 发布目录 | `/opt/champion-erp/releases/20261106-auto-newapi` |
| ERP 程序入口 | `/opt/champion-erp/current`，指向上述发布 |
| Python 环境 | `/opt/champion-erp/venv`，Python 3.12.3；账号版增加 `argon2-cffi` |
| ERP 数据 | `/var/lib/champion-erp`，账号库 `accounts.sqlite3`，原管理员业务数据保留原位，普通用户空间位于 `users/<用户ID>/` |
| ERP 服务 | `champion-erp.service`，开机自启，以专用账号 `champion-erp` 运行 |
| ERP 监听与本机入口 | `127.0.0.1:5050`；Mac 访问 <http://127.0.0.1:15050> |
| New API 源码 | 同级独立仓库 `/Users/hdgl/work/new-api`，分支 `codex/erp-integration` |
| New API 上游 | 官方 `v1.0.0-rc.48`，提交 `706144769e5bed5a47b78721e3bb11da3cf4c0b9` |
| New API 改造提交 | `fe8edf388783655de8e569d891c9dd2a571f100f` |
| New API 运行版本 | `v1.0.0-rc.48-erp.fe8edf388783`，Linux/amd64 |
| New API 镜像 | `champion/new-api:fe8edf388783655de8e569d891c9dd2a571f100f` |
| 本地镜像 ID | `sha256:ad44f94f7f6f6d78bc14c87c520f9bce5876db4704c454aa069cec81a49f461d`；这是本机构建镜像 ID，没有镜像仓库 RepoDigest |
| New API 部署与数据 | `/opt/new-api/deploy`；`/var/lib/new-api/new-api.db`，运行身份 UID/GID 10001，数据目录权限 0700 |
| New API 服务 | Docker 容器 `champion-new-api`，重启策略 `unless-stopped`，健康检查通过 |
| New API 监听与本机入口 | `127.0.0.1:3000`；Mac 访问 <http://localhost:13000> |
| ERP 桥接配置 | `/etc/champion-erp/newapi.env`，root 0600；systemd drop-in 显式加载 |
| New API 配置 | `/etc/new-api/runtime.env`，root 0600；会话密钥与 RPC 凭据分别生成 |

修改前已按要求提交 ERP 工作区，保存点为 `c929080`。New API 的源码、依赖和部署工具均在独立仓库，不进入 ERP 项目目录。服务器只发布修改版镜像，没有使用官方 `latest` 替代实际改造产物。

### 登录与桌面程序

初始管理员密码由服务器生成，存于 `/etc/champion-erp/initial-admin-password`，只有 root 可读；不写入代码、聊天或日志。通过既有 SSH 登录服务器读取，首次登录后可在「个人账号」修改密码。该文件只记录初始化密码，不随改密更新。

ERP 管理员 ID 为 `3552abddc46b46a2baad9bce26f4dc87`，已由服务器 CLI 明确绑定 New API 原生 root 运营账号。普通交接固定创建普通计费用户，ERP 管理员角色不会自动提权。运营账号重复绑定已实测幂等。

从 ERP 的「个人账号 → Token 与账单」点击打开后，直接自动登录 New API，不再输入关联码、点击继续或确认账号。网页用来源绑定的一次性票据 POST 交接；桌面通过主进程和本机临时页面自动提交，不与系统浏览器共享 Cookie。新用户仍在 ERP 注册；敏感操作保留二次验证。

本次新桌面包位于 `/Users/hdgl/Library/Application Support/Champion ERP/updates/newapi-auto-login-20261106/Champion ERP.app`，版本号沿用 `1.2.5`，`app.asar` SHA256 为 `236ee96e69cd1d2134fbfd66164e061012784a76e56d970bd680932782f6fb7e`。当前 `/Applications/Champion ERP.app` 正在运行，已准备退出后安装：`install-when-closed.py` 等待正常退出后备份旧程序并原子替换；如安装目录已被其他更新改动则跳过，避免覆盖并行更新。状态记录在同目录 `install.json`，旧程序备份位置为 `Champion ERP.before-auto-login.app`。截至本次交付，状态为 `waiting_for_exit`；退出后重新打开生效。

本机构建尚未签名、公证；本机浏览器资料、CLI、模型配置和业务数据不因安装包更新迁移。本次从独立检出构建，只发布自动登录修复，保留原工作区的并行品牌修改。

### SSH 入口

Mac 的两个通道由 LaunchAgent 在用户登录后建立，断线后自动重连；使用现有密钥，不上传私钥或转发 SSH Agent：

| 通道 | LaunchAgent | 转发 |
| --- | --- | --- |
| ERP | `/Users/hdgl/Library/LaunchAgents/com.champion.erp.cloud-tunnel.plist` | `127.0.0.1:15050 → 服务器 127.0.0.1:5050` |
| New API | `/Users/hdgl/Library/LaunchAgents/com.champion.newapi.cloud-tunnel.plist` | `127.0.0.1:13000 → 服务器 127.0.0.1:3000` |

日志位于 `/Users/hdgl/Library/Application Support/Champion ERP/logs/` 下的 `cloud-tunnel.log`、`cloud-tunnel.error.log`、`newapi-tunnel.log` 和 `newapi-tunnel.error.log`。桌面 `connection.json` 使用 `http://127.0.0.1:15050`。

ERP 使用主机名 `127.0.0.1`，New API 使用 `localhost`，必须按上表地址访问；不同端口不能隔离 Cookie。SSH 登录此服务器需显式使用 `-p 22`。其他电脑需要自己的 SSH 授权及相同转发端口，不能使用这台 Mac 的回环地址。

正式公开部署仍需两个独立 HTTPS 主机名、有效证书、Secure Cookie、精确 Origin 和可信代理；完成后再切换双方公开地址。当前 HTTP 只在回环地址及 SSH 加密通道内使用，ERP/New API 端口均未直接对公网开放。

## 自动登录修复验收（2026-11-06）

- 真实 ERP 网页账号接口直接签发 60 秒票据，经 `POST /api/erp/login` 返回 303 到钱包、Key 或消费页，未经过「Sign in with ERP」或手动关联码步骤。
- 运营账号保留既有 root 绑定；两个新普通用户各自进入原生普通计费账号，同一用户的网页与实际新安装包 IPC 进入相同用户，跨账号读取模型 Key 被拒绝。
- 实际新包的主进程申请票据、生成本机一次性页面、系统浏览器 POST、原生刷新完成联调；未操作图形界面。重放本机入口或票据被拒绝，错来源无法消费票据。
- 桌面退出撤销对应原生会话，另一 ERP 设备的原生会话仍有效。原计费用户、绑定、余额和三条消费记录均保留；本次没有模型调用费用。
- SQLite 3.50.4、PostgreSQL 18.6、MySQL 8.4.13 的八项 ERP 相关 Go 回归均通过；数据库结构未改变。相关五个 Go 包通过，前端 2465 项通过；共享加载/错误组件继续复用。
- ERP 独立检出完成全后端 4143 项及 53 个 subtest，前端 1126 项、桌面 17 项、类型检查、生成契约检查和构建；lint 无新增错误，保留 16 项原有警告。
- 切换前快照分别为 `/srv/champion-erp/backups/20261106-before-auto-login.tar.gz` 和 `/srv/new-api/backups/20261106-before-auto-login.tar.gz`；验收后最新快照见下文。两次快照均独立恢复校验通过。
- 账号验收摘要位于 `/opt/new-api/deployment-records/20261106-auto-login/acceptance.json`；测试账号已禁用、临时 Key 已撤销、独立数据库和凭据已清理。

## 原账号版本验收记录

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
| ERP 全后端 | `4143 passed, 53 subtests passed`，包含编译和架构边界验证 |
| ERP 前端 | 128 个文件，1126 个测试通过；类型、API 类型检查和网页/桌面构建通过 |
| ERP 桌面 Node | 17 个测试通过；实际安装包内账号代理完成真实服务联调 |
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

- 镜像归档：`/opt/new-api/artifacts/new-api-fe8edf388-linux-amd64.tar.gz`，Linux/amd64，84,316,090 字节。
- 归档 SHA256：`2045b05b51e67dfc43a4ed0b7fb095089d7404c522decac69bf06a19082850ce`，同目录另有 `.sha256` 文件。该摘要对应镜像归档，区别于上表镜像 ID。
- 网络使用者可从 <http://localhost:13000/source/new-api.tar.gz> 或登录页、页脚获取修改版源码。实际下载与构建归档 SHA256 都为 `ac5e752ea2177f78d573e4eb09e2e374ed5fa6a67750b40a28e350154039703f`，7,140,491 字节，内含 `SOURCE_REVISION=fe8edf388783655de8e569d891c9dd2a571f100f`。
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
| ERP | `/srv/champion-erp/backups/20261106-auto-login.tar.gz` | 75,071,452 字节；账号库、原管理员与全部普通用户业务库、配置、素材、桥接配置；逐文件 SHA256、各 SQLite 完整性 |
| New API | `/srv/new-api/backups/20261106-auto-login.tar.gz` | 29,237 字节；用户、身份绑定、会话、模型 Key、余额、日志及全部其他表、运行配置；逐表行数及摘要 |

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
