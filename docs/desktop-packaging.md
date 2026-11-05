# 独立桌面版

发行版包含 Electron、前端构建产物、Python 解释器及后端运行依赖。安装后无需项目目录、开发虚拟环境或单独启动后端。
浏览器 AI / 浏览器采集仍使用用户安装的 Chrome 或 Edge；CLI 类型模型仍需要对应 CLI 工具。

## 构建

在目标系统和架构上准备项目 `.venv`、`requirements.txt` 和前端 pnpm 依赖，然后执行：

```sh
.venv/bin/python scripts/generate_third_party_notices.py
cd front
pnpm desktop:dist
```

后端构建脚本在仓库外创建临时构建环境，只引用开发环境已安装的运行依赖，不向开发 `.venv` 安装构建工具。
后端中间产物位于忽略目录 `front/.desktop-backend`，安装包位于 `front/release`。
构建方案使用 [PyInstaller 的目录分发模式](https://www.pyinstaller.org/en/stable/usage.html)，
通过 Electron 的 `process.resourcesPath` 定位随包后端；不会将 Python 放进 ASAR。
macOS 正式分发时需另行提供开发者签名和公证；本机构建未配置证书时为未签名版本。

第三方许可证随前端构建写入 `front/dist-desktop/licenses`，打包时由 `extraResources` 复制到安装资源目录的 `licenses`，不放入 ASAR。目录内含应用依赖汇总 `THIRD-PARTY-NOTICES.txt` 和 Electron 官方 `Electron/LICENSE.txt`、`Electron/LICENSES.chromium.html`。“关于 → 开源许可证 → 查看”通过受限 IPC 打开该目录，不在页面展开全文。Web 版提供自己的声明 TXT 下载。收集范围和更新方式见 [第三方声明来源](../scripts/legal-notices/README.md)。

## 数据隔离

macOS 数据目录为 `~/Library/Application Support/Champion ERP/backend`。
Windows / Linux 使用 Electron 的系统应用数据目录下同名目录。程序内「文件 → 打开数据文件夹」可定位。

- 主数据库、辅助数据库、配置、图片、缓存、日志及浏览器资料都写入独立目录；升级程序不会覆盖这些数据。
- 发行版不接受开发端口和后端 URL 环境变量。后端绑定 `127.0.0.1:0`，操作系统分配空闲端口，并通过自有子进程管道报告地址与 PID。
- 桌面端在后端就绪后打开窗口；后端异常退出会报错，不能悄悄连接其他运行中的服务。退出时先关闭控制管道，超时后仅终止本次启动的子进程。
- 同一桌面数据目录只运行一个应用实例。浏览器采集默认使用 19222，浏览器 AI 默认从 19333 起分配，浏览器资料也使用桌面目录。
- 专用浏览器复用前核对资料目录记录的会话身份；端口被其他环境占用时直接报错，不接管其他浏览器。
- `.app` 里只有程序及公开默认配置，不含本机的 `app_config.json`、`store_config.json`、`.env`、数据库或浏览器登录信息。

## 配置与程序分开

开发配置的搬迁是一次性本机操作，在打包后复制到桌面数据目录，不放入安装包，也不增加应用内导入功能。
店铺授权在主库 `store_auth` 表，应用密钥、跨境巴士、订单回调和微信凭据统一由主库 `runtime_secrets` 保存；只复制 `config` 目录会漏掉凭据。完整存储清单和旧数据升级步骤见 [配置与授权存储](configuration-storage.md)。
独立配置还需将浏览器模型调试端口调整为桌面默认端口，避免复用开发浏览器；浏览器登录和微信绑定须分别建立。
程序启动只补充缺失的默认提示词、Agent 记忆和平台预设，已有设置不覆盖，两边后续配置不自动同步。

## 验证

```sh
.venv/bin/python -m pytest tests -q
node --test front/desktop/backend.test.cjs
```

发行前另用打包后的后端，在仓库外临时目录和不含开发路径的环境中启动，检查 `/api/state`，
关闭父进程管道并验证进程退出。测试不得携带真实店铺配置或触发平台业务操作。
