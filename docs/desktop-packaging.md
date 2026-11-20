# 桌面端与统一业务后端

桌面安装包包含 Electron、同一套 Vue 前端和独立 Python 本机执行器。业务后端单独部署，同一账号在网页与桌面读取相同的商品、草稿、订单、任务和消息历史；不同账号分别使用自己的业务空间。桌面退出关闭本次执行器并断开本机调用连接，业务后端及其 worker 继续运行。

1688 网页采集和 Gemini Canvas 使用桌面内置浏览器。其他网页采集和浏览器模型使用本机 Chrome / Edge，CLI 使用本机已安装并登录的工具。API 模型继续由后端执行，不自动切换连接方式。

## 启动与连接

开发时先按[账号初始化步骤](server-deployment.md#账号版本切换)创建管理员，在仓库根目录执行 `./scripts/dev.sh`，同时启动后端、网页和桌面。默认后端为 `http://127.0.0.1:5050`，网页为 `http://127.0.0.1:3000`，通过 `ERP_PORT`、`VITE_DEV_PORT` 调整端口，通过 `VITE_DEV_PROXY_TARGET` 指定已有后端。按 Ctrl+C 统一关闭。已有后端时，也可在 `front/` 执行 `pnpm desktop:dev` 启动前端与桌面。

开发桌面显示“开发版”，使用独立的 `champion-erp-front` 本机资料目录。Vue 页面在网页和桌面中同步热更新，修改 Electron 主进程或 Python 代码后重启脚本。Vite 的开发专用入口只接收同源 POST 的交接编号，通过 IPC 发给其管理的 Electron 进程，并使用配置中的实际后端地址验证交接。开发客户端只解析 `champion-erp-dev` 链接，正式客户端只解析 `champion-erp` 链接；系统的正式协议注册由安装版维护。关闭开发窗口后，再从开发网页操作可重新启动开发客户端。

开发版在任务打开 Gemini、Google 登录或 1688 网页时立即显示窗口。验证完成后继续显示原页。任务失败时可查看现场，手动关闭或再次打开同一会话会释放旧页面。

正式版未指定连接时，默认使用 `front/package.json` 的 `desktopUpdateOrigin` 发行站点。连接独立部署时可以指定业务后端地址。

- 设置环境变量 `CHAMPION_ERP_BACKEND_URL=https://erp.example.com`；或
- 在 Electron 的 `Champion ERP/connection.json` 写入 `{"backend_url":"https://erp.example.com"}`。

地址只接受 HTTP/HTTPS 站点根地址。桌面用随机回环端口提供前端并代理普通 API、AI SSE、图片、文件及 OAuth 链接；代理目标固定为上述业务后端。原始 Host/Origin 在桌面代理处核验，当前桌面连接由主进程注入，页面不能指定其他桌面。

部署后端时，`ERP_BIND_HOST` 指定监听地址，默认 `127.0.0.1`。反向代理应配置实际 `ERP_PUBLIC_URL`，HTTPS 公网入口使用 Secure Cookie；确有额外站点时通过 `ERP_ALLOWED_HOSTS` / `ERP_ALLOWED_ORIGINS` 放行。来源校验与账号认证分别执行。

桌面登录页分别提供记住密码和自动登录。主进程通过 Electron 安全存储按后端地址加密保存选择的密码或会话。开发版首次登录默认勾选自动登录，此后恢复用户保存的选择；正式版首次登录默认关闭。记住密码由用户单独选择，自动登录只保存会话即可使用。自动登录恢复后必须通过后端桌面连接校验；退出清除本机会话，改密同时清除本机已记密码。注册通过系统浏览器在后端公网域名完成人机验证和邮箱验证。

## 网页与桌面的操作范围

采集页的 URL 清单保存在当前账号的后台空间。1688 API 链接可直接在网页执行。选中的链接包含浏览器采集项时，网页经 `champion-erp://continue` 向桌面传递链接 ID 和版本。客户端自动打开目标商品页，遇到验证码时保留原页，验证完成后继续采集。
API 采集接收单个 1688 商品详情链接，后端从链接解析商品编号并使用当前采购账号授权调用商品详情接口。网页和桌面均可直接提交。

图片编辑、翻译等共用功能按实际模型绑定决定是否交接。客户端登录同一账号后恢复目标页面，再回传打开确认。
等待五秒尚无确认时，网页提供“重试”和“下载安装”。安装弹窗使用当前站点已发布的各平台安装包，安装后回网页重试。
模型配置错误及业务必填条件在原入口提示，登录、桌面断连、窗口聚焦和模型设置保存后刷新可用性。

安装包通过 electron-builder 的 `protocols` 声明注册应用协议。Windows 从启动参数接收链接，
已运行实例由 `second-instance` 唤醒，macOS 在 `ready` 前监听 `open-url` 并保存提前到达的请求。
主进程核对链接所属站点后，仅向受信主页面提供交接编号。网页与桌面经过各自会话校验，
短期请求最多保留十分钟，来源会话失效后无法继续领取。页面恢复完成后回传确认，业务由原入口执行。

AI 在后端运行。桌面的用户输入可以对原设备发起本机调用，网页和微信输入只使用服务端能力。
后台处理、工具审批和重开页面均不会把任务转移到另一台在线电脑。旧收件记录没有来源时，
后续新操作只使用服务端能力；已保存的原调用按自身连接核对。图片请求已发出后失去回执，
在批次中标记“结果待核对”，不自动重做。

## 本机执行器与配置

`front/desktop/runtime.cjs` 管理执行器进程与 JSON 行管道；`desktop_runtime/__main__.py` 是 Python 启动入口。stdout 只传固定动作、素材与结果，日志写 stderr。开发使用 `.venv`，安装版使用自带可执行文件。

macOS 沿用 `~/Library/Application Support/Champion ERP/backend` 作为本机目录；Windows/Linux 使用系统应用数据目录下的同名目录。保留目录名是为了沿用已有浏览器登录资料，不代表继续启动本地业务后端。

- `config/desktop-connections.json` 保存 CLI 路径/Profile、浏览器模式、资料目录名、端口、网页 URL 和代理。
- 1688 和 Gemini Canvas 的登录数据保存在桌面应用数据目录的 `Partitions` 下。分区按后端、ERP 账号、服务和会话名称隔离。升级到内置浏览器后首次使用可能需要登录；外部浏览器资料保留在原目录。
- 本机设置通过受限 preload IPC 保存。主进程校验主窗口、主 frame、来源和字段；业务后端不能通过调用通道改写命令或本机路径。
- 后端模型配置只保存业务模型、能力、用例绑定和 `local_connection_id` / 配置指纹。执行时核验实际本机配置，变更后重新测试，旧能力证明不能沿用。
- 不携带店铺凭据、业务数据库、开发 `.env`、业务 worker、API 模型 SDK 或开发浏览器资料。

`front/desktop/connection.cjs` 在主进程持续等待后端本机调用，不依赖 Vue 页面挂载。普通操作采用请求/响应。断连、取消和超时结束受影响调用；已发送操作不会自动重放，结果未知仍由原业务入口解释。连接仅存在于当前后端进程内，不跨重启恢复或改派其他桌面。网络断开后重新打开桌面连接。

核对项目安装的 Pydantic AI 2.44.0 后，任务归属使用原生 `CallDeferred` / `ApprovalRequired` 的 metadata，
不修改 Agent run、审批、恢复和消息编码。依据见 [官方 Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)。
项目适配仅补充原生 Agent 不负责的 Electron/CDP 进程传输；若将来采用可直接管理本机执行器的原生连接，移除这层进程通道。

采集图片逐张通过后端素材入口交接，商品仍由 `ProductStore` 保存；Canvas 源图由后端读取，返回图片经原图片服务校验和保存。本机绝对路径不能成为商品素材持久引用。

## 构建

同时发布服务器、网页和可选桌面更新时，在项目根目录执行 `.venv/bin/python scripts/release.py`。构建用 Mac 也通过客户端检查更新。需要强制升级时加 `--desktop-update required`；仅发布服务器和网页时加 `--server-only`。详见[统一编译与发布](server-deployment.md#统一编译与发布)。

在目标平台和架构上安装 Python 与前端 pnpm 依赖，然后执行：

```sh
.venv/bin/python scripts/generate_third_party_notices.py
cd front
pnpm desktop:dist
```

`build:runtime` 在仓库外创建独立构建环境。`requirements-desktop-build.txt` 引用执行器运行依赖并锁定打包工具，构建优先从本机 wheel 缓存安装，缺项时自动补齐。`scripts/desktop_runtime.spec` 仅收集 `desktop_runtime`、`erp_shared` 和所需依赖，明确排除 `erp_web`、SQLite 与业务 SDK。输出为 `front/.desktop-runtime/champion-erp-runtime`，安装包资源目录为 ASAR 外的 `runtime`。

前端输出到 `front/dist-desktop`，发行产物位于 `front/release`。第三方声明及 Electron 官方许可证继续通过 `extraResources` 放在 `licenses`，关于页通过固定 IPC 打开目录。

macOS 普通打包和统一发布共用 `front/electron-builder.config.mjs`，默认使用固定自签代码签名证书 `gongluERP Local Signing`。配置按证书 SHA-1 指纹选择原证书，不按同名新证书替换；构建机须持有原证书及私钥并允许代码签名。缺失时直接报错，不生成临时签名包。原证书及私钥应加密导出为 `.p12`，备份在仓库外；换构建机时导入同一身份，不重新创建。证书过期或更换身份需要单独安排切换及重新授权。

签名由 electron-builder 处理应用、Helper 和内嵌本机执行器；签名后复核主应用标识、固定证书及执行器签名，并读取主应用与执行器的实际 designated requirement。自签规则必须只绑定各自标识及原证书，拒绝绑定 CDHash、版本号或其他条件的规则，避免“证书没变但授权仍绑定单个构建”。原生回归使用同一证书签名两次内容不同的构建，并用各自的规则互相验证；也覆盖证书正确但附加版本条件的错误构建。自签包不执行 Apple 公证或网络时间戳，沿用关闭强化运行时的本机测试配置。签名身份依据见 [Apple TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)。

### 从临时签名迁移系统授权

固定证书让新构建具备稳定身份，但不会自动改写 macOS 已保存的旧授权。旧版若用临时签名取得权限，系统仍可能按旧版 CDHash 核验新版，导致更新后开关关闭或首次访问被拒绝；这不能仅凭“签名有效”判定已经解决。

如升级后提示被阻止修改 App，进入「系统设置 → 隐私与安全性 → App 管理」，开启当前安装的 `gongluErp`，完全退出 ERP 后重新打开并重试。此处是允许修改应用的「App 管理」，不是「自动化」。重新允许后，系统才会按当前固定证书保存授权。不要为了迁移清除全部隐私授权，也不要由应用写入 TCC 数据库。若同一证书下仍反复失效，应核对系统实际保存及使用的规则，而不是继续重新创建证书。

2026-11-16 的本机排查确认：安装的 1.2.82 与 1.2.74、1.2.78、1.2.79 使用同一证书；当次旧授权实际绑定 1.2.69 的 CDHash，开启后改为「应用标识 + 固定证书」，后续访问已放行。新授权规则也通过上述四个版本的原生签名核验。该记录验证了这次迁移，不代表所有权限或未来系统版本都不会再次提示。

## 客户端获取更新

自 1.2.8 起，正式包在启动本机执行器前检查发行站点，运行期间每 30 分钟检查一次，也可从“文件 → 检查桌面更新”立即检查。检查只按操作系统和 CPU 架构选包。版本策略与业务登录独立，发行站点不可达时沿用最后成功取得的策略。尚未取得过策略时仍可启动，接入已发布最低版本策略的业务后端后，新请求会再经服务端检查。

用户确认后才下载。自 1.2.89 起，固定自签证书和 Developer ID 签名的 Mac 客户端均支持在启动阶段自动替换。`desktop/update-signature.cjs` 对当前应用执行完整签名检查，接受原自签证书或 Developer ID，拒绝临时签名、其他自签证书和损坏的应用。发布默认同时构建 DMG 与 ZIP，并将 `macZip` 写入更新清单。

自 1.2.133 起，符合自动更新条件的 Mac 在业务运行中可选择「下载更新」，`updates.cjs` 校验长度和 SHA-256 后将 ZIP 缓存在本机。下次启动按发行清单复核对应版本 ZIP 的大小和摘要，有效时显示「安装并重启」。ZIP 缺失或无效时显示「下载并安装」。手动安装包也按清单校验并复用有效缓存。

确认安装后，`native-update-feed.cjs` 通过仅绑定 `127.0.0.1`、带随机地址的临时来源向 Electron 原生更新器提供已校验的 ZIP。成功或失败后均关闭来源。原生更新器继续核验新版是否满足旧版的签名身份，之后由 Squirrel 在退出后替换并重启。校验失败保留旧版并报告错误。

没有原生更新入口的旧客户端须先手动安装一次该功能版，之后才能使用自动替换。默认自签包未经 Apple 公证，其他 Mac 上首次打开或替换应用仍可能需要系统授权；自动替换也不会消除自签版的钥匙串提示。用户可主动选择 DMG 手工安装：退出旧程序后用安装包中的应用替换原应用，并保持原应用文件名，避免两个桌面入口。Windows 对应 NSIS 安装包，Linux 定位已校验的 AppImage。可选更新允许稍后使用原版本；低于最低版本时，新启动不能跳过更新进入业务，使用中的客户端仍保留原有会话和本机回执。

已有 Developer ID 和公证配置时，在统一发布命令追加 `--signed-desktop`；脚本设置 `ERP_DESKTOP_SIGNING_MODE=developer-id`，启用强化运行时及公证，额外验证 Developer ID 签名和公证票据。单独使用 `pnpm desktop:dist` 构建正式签名包时也需显式设置该环境变量。首次从临时或自签身份切到 Developer ID 仍走安装包。Apple 会员与签名配置见 [Apple Developer ID](https://developer.apple.com/developer-id/)，原生更新要求见 [Electron autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater#macos)。

### 自动替换的原生验收

在配置了原自签证书的 Mac 上执行 `.venv/bin/python scripts/test_desktop_auto_update.py`。脚本构建两个独立 Electron 应用，使用随机 Bundle ID、仓库外临时目录、`~/Applications` 下独立测试目录和本机发行服务。成功场景先下载 ZIP，再启动旧版并确认安装，验证发行服务总共只收到一次 ZIP 请求。无浏览器窗口、不读取 ERP 账号或钥匙串，不替换正式应用；结束后回收测试更新服务、缓存及应用。

2026-11-17 在 macOS 27 / Apple 芯片、Electron 43.0.0 上使用真实 `autoUpdater` / Squirrel 完成四项验收：摘要错误、损坏 ZIP、临时签名替换包均被拒绝且保留 1.0.0；正确自签包从 1.0.0 自动替换为 1.0.1，并验证新版进程在同一安装路径重新启动。发行网络使用本机服务，未向生产更新站点发布，也不代表所有 Mac 的安装授权都可跳过。

## 旧桌面数据迁移

切换前停止旧桌面和来源业务进程。不要把新后端指向一份空库后继续维护两套业务数据。迁移和程序切换分别执行：

```sh
.venv/bin/python scripts/migrate_desktop_business.py \
  --source '/旧桌面/Champion ERP/backend' \
  --target '/统一后端/erp-data' \
  --backup '/独立备份/desktop-before-split' \
  --local '/旧桌面/Champion ERP/backend'
```

来源须含当前格式 `erp.sqlite3`；旧数据库结构或旧凭据布局先按原迁移文档处理。目标和备份目录必须不存在，来源与目标不得互相包含。迁移器先备份主库、辅助库、配置和素材，再生成业务目标；SQLite 使用一致性备份，不直接复制 WAL 中的数据库文件。

跨电脑迁移时，`--source` 指向上传后的快照目录，另用 `--source-prefix` 指定快照中持久化路径使用的原电脑绝对目录。`--local` 可先指向独立输出目录，再通过本机配置 owner 将本机参数保存回原电脑。

转换仅重写来源目录下的资产路径及 `/file` 引用，保留业务 ID、记录数量和关联。随后通过 `ConfigStore` 读写业务配置和凭据，通过本机配置 owner 分离 Browser/CLI 参数；浏览器登录资料留在原电脑。失败保留备份及 `migration-incomplete` 门禁，不能直接启动未完成的目标。完成后生成 `desktop-migration-report.json`，核对记录、素材访问和原历史回执，再配置后端 `ERP_APP_DIR` 与桌面连接地址。

程序启动不会自动迁移或覆盖业务数据。2026-11-06 已按用户选择迁移真实桌面数据并完成服务器切换；实际地址、验收证据、备份及恢复方式见 [服务器部署记录](server-deployment.md)。项目开发数据库未参与本次迁移。

## 验证

```sh
.venv/bin/python -m compileall -q desktop_runtime erp_shared erp_web
.venv/bin/python -m pytest tests -q
node --test front/desktop/*.test.cjs
pnpm --dir front test:run
pnpm --dir front types:check
pnpm --dir front build:desktop
```

发行时另用打包后的执行器，在仓库外临时目录验证就绪管道、固定动作、父进程 EOF 和无业务数据库初始化。安装版连真实服务器、真实浏览器登录及实际数据迁移需在目标环境验收，不用测试结果代替这些上线步骤。
