# 桌面端与统一业务后端

桌面安装包包含 Electron、同一套 Vue 前端和独立 Python 本机执行器。业务后端单独部署，同一账号在网页与桌面读取相同的商品、草稿、订单、任务和消息历史；不同账号分别使用自己的业务空间。桌面退出关闭本次执行器并断开本机调用连接，业务后端及其 worker 继续运行。

浏览器采集、Browser AI、Gemini Canvas 和 CLI 在用户电脑执行；需要本机 Chrome / Edge 或已安装并登录的 Codex CLI。API 模型继续由后端执行，不自动切换连接方式。

## 启动与连接

开发时先按[账号初始化步骤](server-deployment.md#账号版本切换)创建管理员，再启动统一后端，在 `front/` 执行 `pnpm desktop:dev`。开发默认后端为 `http://127.0.0.1:5000`；`scripts/dev.sh` 设置端口时沿用 `ERP_PORT` / `VITE_DEV_PROXY_TARGET`。

安装版必须明确配置业务后端：

- 设置环境变量 `CHAMPION_ERP_BACKEND_URL=https://erp.example.com`；或
- 在 Electron 的 `Champion ERP/connection.json` 写入 `{"backend_url":"https://erp.example.com"}`。

地址只接受 HTTP/HTTPS 站点根地址。桌面用随机回环端口提供前端并代理普通 API、AI SSE、图片、文件及 OAuth 链接；代理目标固定为上述业务后端。原始 Host/Origin 在桌面代理处核验，当前桌面连接由主进程注入，页面不能指定其他桌面。

部署后端时，`ERP_BIND_HOST` 指定监听地址，默认 `127.0.0.1`。反向代理应配置实际 `ERP_PUBLIC_URL`，HTTPS 公网入口使用 Secure Cookie；确有额外站点时通过 `ERP_ALLOWED_HOSTS` / `ERP_ALLOWED_ORIGINS` 放行。来源校验与账号认证分别执行。

桌面先显示登录页，登录成功后才建立本机调用连接。`account-session.cjs` 在主进程内存保存远端 Cookie，剥离代理响应的 `Set-Cookie`，渲染页面只取得用户和 CSRF。后端核对空间及会话，退出、切换或失效时断开；关闭程序后重新登录，不保存远端密码或会话文件。

## 网页与桌面的操作范围

网页版隐藏专用采集浏览器、网页模型预置等本机入口，仍可使用 HTTP、API、手动采集和官方授权链接。
已保存的本机模型可以查看；在网页保存其他模型时，后端保留原本机连接和能力记录。
图片编辑、套图规划与生成、文本翻译等共用入口按各自的模型绑定判断。需要桌面的按钮显示原因，
已生成的素材、任务结果和草稿仍可查看和使用。登录、桌面断连、窗口重新聚焦和模型设置保存后刷新判断。
平台可用性不发起浏览器、模型或账号探测；连接测试仍由用户显式触发。

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

同时升级服务器与本机桌面时，在项目根目录执行 `.venv/bin/python scripts/release.py`。统一流程会执行回归检查、构建、服务器备份切换、公网验证以及桌面旧包备份和替换，详见[统一编译与发布](server-deployment.md#统一编译与发布)。

在目标平台和架构上安装 Python 与前端 pnpm 依赖，然后执行：

```sh
.venv/bin/python scripts/generate_third_party_notices.py
cd front
pnpm desktop:dist
```

`build:runtime` 在仓库外创建独立构建环境，仅安装 `requirements-desktop.txt` 和构建工具；`scripts/desktop_runtime.spec` 仅收集 `desktop_runtime`、`erp_shared` 和所需依赖，明确排除 `erp_web`、SQLite 与业务 SDK。输出为 `front/.desktop-runtime/champion-erp-runtime`，安装包资源目录为 `runtime`，不放入 ASAR。

前端输出到 `front/dist-desktop`，发行产物位于 `front/release`。第三方声明及 Electron 官方许可证继续通过 `extraResources` 放在 `licenses`，关于页通过固定 IPC 打开目录。macOS 签名、公证仍需发行环境配置。

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
