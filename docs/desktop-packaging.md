# 桌面端与统一业务后端

桌面安装包包含 Electron、同一套 Vue 前端和独立 Python 本机执行器。业务后端单独部署，同一账号在网页与桌面读取相同的商品、草稿、订单、任务和消息历史；不同账号分别使用自己的业务空间。桌面退出关闭本次执行器并断开本机调用连接，业务后端及其 worker 继续运行。

浏览器采集、Browser AI、Gemini Canvas 和 CLI 在用户电脑执行；需要本机 Chrome / Edge 或已安装并登录的 Codex CLI。API 模型继续由后端执行，不自动切换连接方式。

## 启动与连接

开发时先按[账号初始化步骤](server-deployment.md#账号版本切换)创建管理员，再启动统一后端，在 `front/` 执行 `pnpm desktop:dev`。开发默认后端为 `http://127.0.0.1:5000`；`scripts/dev.sh` 设置端口时沿用 `ERP_PORT` / `VITE_DEV_PROXY_TARGET`。

正式版未指定连接时，默认使用 `front/package.json` 的 `desktopUpdateOrigin` 发行站点。连接独立部署时可以指定业务后端地址。

- 设置环境变量 `CHAMPION_ERP_BACKEND_URL=https://erp.example.com`；或
- 在 Electron 的 `Champion ERP/connection.json` 写入 `{"backend_url":"https://erp.example.com"}`。

地址只接受 HTTP/HTTPS 站点根地址。桌面用随机回环端口提供前端并代理普通 API、AI SSE、图片、文件及 OAuth 链接；代理目标固定为上述业务后端。原始 Host/Origin 在桌面代理处核验，当前桌面连接由主进程注入，页面不能指定其他桌面。

部署后端时，`ERP_BIND_HOST` 指定监听地址，默认 `127.0.0.1`。反向代理应配置实际 `ERP_PUBLIC_URL`，HTTPS 公网入口使用 Secure Cookie；确有额外站点时通过 `ERP_ALLOWED_HOSTS` / `ERP_ALLOWED_ORIGINS` 放行。来源校验与账号认证分别执行。

桌面登录页分别提供记住密码和自动登录。主进程通过 Electron 安全存储按后端地址加密保存选择的密码或会话，默认关闭。自动登录恢复后必须通过后端桌面连接校验；退出清除本机会话，改密同时清除本机已记密码。注册通过系统浏览器在后端公网域名完成人机验证和邮箱验证。

## 网页与桌面的操作范围

网页版保留专用采集浏览器、网页模型预置和 CLI 配置入口，点击直接通过 `champion-erp://continue` 打开客户端。
图片编辑、套图规划与生成、文本翻译、类目匹配和 AI 对话按实际模型绑定决定是否交接。
工作台交接携带商品、草稿编辑内容及采集链接，对话交接携带会话位置和待发送内容。
客户端登录同一账号后恢复目标页面，再回传打开确认。等待五秒尚无确认时，网页提供“重试”和“下载安装”。
等待期间收到确认仍会正常结束。安装弹窗读取当前站点已发布的 Windows、macOS 等平台安装包，安装后回网页重试。
模型配置错误和业务必填条件继续在原入口提示。已生成素材、任务结果和草稿仍可使用。
登录、桌面断连、窗口重新聚焦和模型设置保存后刷新操作可用性。
平台可用性不发起浏览器、模型或账号探测；连接测试仍由用户显式触发。

安装包通过 electron-builder 的 `protocols` 声明注册应用协议。Windows 从启动参数接收链接，
已运行实例由 `second-instance` 唤醒，macOS 在 `ready` 前监听 `open-url`，保存启动时提前到达的请求。
主进程核对链接站点与配置后，只向受信主页面提供交接编号。网页与客户端经过各自会话校验，
短期请求最多保留十分钟，来源会话失效后无法继续领取。桌面回传代表页面恢复完成，业务仍在原入口执行。

AI 在后端运行。桌面的用户输入可以对原设备发起本机调用，网页和微信输入只使用服务端能力。
后台处理、工具审批和重开页面均不会把任务转移到另一台在线电脑。旧收件记录没有来源时，
后续新操作只使用服务端能力；已保存的原调用按自身连接核对。图片请求已发出后失去回执，
在批次中标记“结果待核对”，不自动重做。

## 本机执行器与配置

`front/desktop/runtime.cjs` 管理执行器进程与 JSON 行管道；`desktop_runtime/__main__.py` 是 Python 启动入口。stdout 只传固定动作、素材与结果，日志写 stderr。开发使用 `.venv`，安装版使用自带可执行文件。

macOS 沿用 `~/Library/Application Support/Champion ERP/backend` 作为本机目录；Windows/Linux 使用系统应用数据目录下的同名目录。保留目录名是为了沿用已有浏览器登录资料，不代表继续启动本地业务后端。

- `config/desktop-connections.json` 保存 CLI 路径/Profile、浏览器模式、资料目录名、端口、网页 URL 和代理。
- 采集资料位于 `browser_profile/1688`；AI 资料位于 `browser_profile/ai/<provider>/<profile>`，两者分开。
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

前端输出到 `front/dist-desktop`，发行产物位于 `front/release`。第三方声明及 Electron 官方许可证继续通过 `extraResources` 放在 `licenses`，关于页通过固定 IPC 打开目录。macOS 签名、公证仍需发行环境配置。

## 客户端获取更新

自 1.2.8 起，正式包在启动本机执行器前检查发行站点，运行期间每 30 分钟检查一次，也可从“文件 → 检查桌面更新”立即检查。检查只按操作系统和 CPU 架构选包。版本策略与业务登录独立，发行站点不可达时沿用最后成功取得的策略。尚未取得过策略时仍可启动，接入已发布最低版本策略的业务后端后，新请求会再经服务端检查。

用户确认后才下载。Mac 客户端校验 DMG 的长度及 SHA-256，启动阶段打开安装包，使用期间只在 Finder 中定位下载文件。无 Apple 开发者证书时，统一发布脚本使用免费的本机临时签名，未经 Apple 公证，其他 Mac 上首次打开可能遇到系统安全提示。退出旧程序后用 DMG 内的应用替换原应用；原安装名称若为 `Champion ERP.app`，替换时请保留此名称，避免留下两个桌面入口。Windows 对应 NSIS 安装包，Linux 定位已校验的 AppImage。可选更新允许稍后使用原版本。低于最低版本时，新启动不能跳过更新进入业务；使用中的客户端仍保留当前会话和本机调用回执。

已有 Developer ID 和公证配置时，在统一发布命令追加 `--signed-desktop`，将验证签名和公证票据，发布 DMG 并额外提供原生更新 ZIP。已签名的 Mac 客户端在启动阶段可使用 Electron 的 `autoUpdater` 安装并重启，也可选择下载 DMG。首次从缺少签名的客户端切到已签名版本仍走安装包。Apple 会员与签名配置见 [Apple Developer ID](https://developer.apple.com/developer-id/)，原生更新要求见 [Electron autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater#macos)。

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
