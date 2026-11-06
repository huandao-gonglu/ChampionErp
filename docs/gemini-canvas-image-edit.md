# Gemini Canvas 修图

图片工作台继续使用原图选择和修改说明。`image.translate` 是当前图片翻译与 AI 修改共用的模型绑定；
选用浏览器模型后，经 `ai_gateway_providers.edit_images` 分派给独立的 `GeminiCanvasProvider`。
API 模型仍由 Pydantic Direct Model 执行，不增加 API 旁路，不自动切换连接。

## 最小接入

在 AI 模型设置中选择“添加网页模型 → Gemini Canvas”。项目自动填写公开分享页
`https://gemini.google.com/share/a3adc37e7008`、网页服务商 `gemini`、托管浏览器和 `canvas` Profile，
调试端口留空使用默认值，模型留空使用 `gemini-3.1-flash-image-preview`。
按需填写自己的代理，勾选“图像编辑”并确认测试，测试通过后自动保存当前模型与能力回执，再在图片处理功能绑定中选择该模型。
单独修改模型字段不会自动保存；其他模型的未保存修改不随本次测试提交。
预置不会提前伪造已通过的能力测试，也不包含用户账号、代理地址或登录资料。
删除模型后可通过同一入口重新添加；重复选择已添加项只定位现有配置，不覆盖个人设置。
首次点击图像编辑能力测试时直接显示专用 Google 登录窗口，完成登录后再加载 Canvas 并执行测试。
新增模型不自动打开窗口；删除模型条目不会清除独立浏览器目录中的登录资料。
普通使用者无需在各自账号里重新创建应用。
“网页服务商”使用预置目录生成的下拉选项，以固定服务商 ID 选择适配器；修改显示名称不改变连接。
手动切换到网页接入时需要明确选择服务商，不再在界面中默认填入 ChatGPT。
切换服务商会带入所选服务的默认网页设置，并清除原能力测试结果；个人代理保持原值。
项目自动查找本机 Chrome/Edge，使用 `browser_profile/ai/gemini/canvas` 保存登录状态；
Gemini 默认调试端口为 `9335`，与 1688 的目录和端口分开。不要求安装 BitBrowser。
托管模式下，Canvas 修图默认启动无窗口 Chrome。检测到需要 Google 登录时，
项目使用同一个 Profile 显示专用登录窗口；完成后自动恢复无窗口模式并继续原任务。
不会复制日常浏览器或 BitBrowser 的登录资料。
也保留显式连接已打开浏览器的能力，但不作为托管模式失败后的自动回退。
如果正常使用 Gemini 需要独立网络配置，在高级连接设置的“浏览器代理”中填写
`http://地址:端口`、`https://地址:端口` 或 `socks5://地址:端口`。
该设置仅在项目托管浏览器启动时生效，不影响 ERP 后端或 1688；修改后关闭 Gemini 专用窗口，
再测试连接。地址不能包含账号密码；HTTP 代理认证由浏览器的正常认证流程处理，
需要认证的 SOCKS5 应先由本机代理客户端转发为可用端口，Chrome 不支持 SOCKS5 认证。
实现直接使用 Chrome 原生 `--proxy-server`，不增加直连回退或指纹伪装；协议能力依据
[Chromium 官方代理文档](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)。
项目自动打开配置的 Canvas 应用预览；普通 Gemini 首页不能替代应用预览。
图像编辑能力测试会用随应用发布的红色测试图发起一次真实修图；通过后绑定到图片处理用例。

## 维护和分享承载应用

最小应用源码位于 `erp_web/assets/gemini_canvas_host.html`，不依赖轻舟或其他第三方工作台。
在自己的 Google 账号中打开 Gemini，新建对话并选择 Canvas，要求按该源码创建单文件 HTML 应用，
保留模型地址、空 API key 和原生 `fetch`。创建后打开应用预览，将该私有对话的
`https://gemini.google.com/app/<对话 ID>` 地址保留为维护入口。
在 Canvas 面板右上角点击“分享”，生成可分享公开链接。项目当前公开链接为
`https://share.gemini.google/LEAabCfJN89h`，打开后跳转到
`https://gemini.google.com/share/a3adc37e7008`；浏览器模型的网页 URL 填写后者。
执行器校验 Google 的完整页面地址，不直接接受短链接或将私有维护入口作为分发地址。

应用没有外部脚本、数据库或独立登录；只在用户手动提交时执行图片编辑，
不自动生成图片、不持久化图片或修改说明。ERP 仍通过 Google 注入的原生模型通道执行修图，
无需操作应用中的选图或提交按钮。源码随项目维护，由维护者创建并分享 Canvas 应用；
使用者在自己的专用浏览器登录 Google 后访问公开分享页，产品运行时不会自动新建 Gemini 对话或应用。

业务输入和输出仍由 ERP 负责，无需在承载页上传、输入或点击。执行器只使用当前标签关联的
Google Canvas 默认执行环境，调用 Google 注入的模型转发函数；不读取 Cookie 或登录凭据，
不调用第三方工作台的业务代码、账号服务或页面控件。

## 边界

- 1688 与 Gemini 共用 `desktop_runtime.browser_debug_service.ensure_browser_debug_session` 的浏览器发现、启动和连接等待。
  `desktop_runtime/browser_ai_runtime` 负责 AI 独立资料目录与页面连接；创建页面使用 `PUT /json/new`，
  Canvas 只复用配置地址对应的页面，不借用其他对话或空白标签。
  普通 Chrome/Edge 已提供独立资料目录和 CDP，无需额外浏览器框架或指纹内核；
  [Chrome 136 起的远程调试要求](https://developer.chrome.com/blog/remote-debugging-port)也要求非默认资料目录。
  `desktop_runtime/gemini_browser_session` 负责登录检查、最长五分钟的交互登录等待及恢复原任务；
  1688 和其他浏览器调用保持其显式显示方式，“连接已打开浏览器”模式由用户管理窗口。
  Profile 不改变网络出口或地区资格，产品运行时不自动创建 Canvas。
- 隐藏执行使用 Chrome 原生 `--headless`，登录显示与隐藏之间顺序重启同一个专用 Profile，
  不复制 Cookie。项目通过本机资料目录中的浏览器 WebSocket 实例身份记录核对归属，
  不自动关闭未登记的浏览器实例；首次从旧版本升级时需先关闭旧专用浏览器。
  这是对原生浏览器进程生命周期的适配，不创建 Agent、消息或领域任务协议。
  [Chrome 无窗口模式](https://developer.chrome.com/docs/automation-and-testing/headless)与有窗口模式共用内核，
  不需要额外安装浏览器内核；只有项目需要安排登录显示与请求继续。
- 输入支持本地 PNG/JPEG/WebP，每张不超过 20 MB；首版每张原图返回一份图片结果。
  Canvas 原生配置决定生成规格，当前不映射 API 的质量、尺寸参数。
- 同一浏览器端口的图片任务顺序执行；不持有商品锁跨模型等待。
- 仅在真实二进制图片成功解码后交给原有图片服务生成独立资产，保留来源 ID；
  文本描述、空回执和不匹配的 MIME 均不能作为成功。
- 请求前和登录等待期间检查取消；登录超时、关闭登录窗口或取消时，不发送图片请求，
  并尝试恢复隐藏模式。登录等待属于浏览器连接准备时间，不消耗图片生成的请求超时。
  登录完成后才发送原图和要求；明确收到未认证拒绝时，最多重新登录并恢复一次原请求。
  已提交的 Canvas 调用不保证能被远端取消；连接超时或丢失报告结果未知，不自动重发。
  地区、网络、限额或缺少 Canvas 通道的错误不会被当作登录问题反复打开窗口。
- 浏览器连接没有 Pydantic 原生模型消息；不伪造消息历史。任务输入、状态和结果资产仍由现有图片批次持久化。
- 不声明与 Gemini 聊天生图共用额度，也不声明可免于平台限制；Canvas 不是已验证的通用后端 API。

## 验证

真实验证已完成：由项目图片处理入口调用自建 Canvas 公开分享页的原生通道，
将红色测试 PNG 修改为蓝色，取回并解码 1024×1024 JPEG（146,254 字节）。
Chrome 使用 Gemini 独立 Profile，接入本机已有的 SSH SOCKS5 代理；
重启专用浏览器后登录状态保持有效，不需要 BitBrowser 进程或其登录资料。
关闭私有维护页和当前分享页后，项目按配置自动重新打开公开 `/share/<分享 ID>`，
HTTP 连接测试仍通过；无需保持私有维护页或第三方工作台页面打开。

无窗口模式已完成真实修图，返回 1024×1024 JPEG，生成完成后仍保持 Headless Chrome。
当前有效登录环境中，显示 Google 登录窗口、检测登录状态并恢复隐藏连接的流程已实际验证；
全新匿名 Profile 能正确识别为需要登录，未强制退出用户的真实账号。

本机首次直连出现地区不可用提示，配置已有代理后真实修图成功。
这验证了当前账号与网络环境的组合，不保证其他账号或网络均可用；
额度池归属仍未验证。

`tests/test_gemini_canvas_runtime.py` 覆盖现有图片服务入库、源图保持、来源关联、
默认执行环境选择、错误/空结果/格式拒绝、超时不重发及 API/浏览器分流。
`tests/test_browser_managed_profile.py` 覆盖进程复用、专用资料目录、页面身份、
代理校验与配置保存、代理启动参数和代理变化后的能力验证标识。
网页预置由 `ai_model_config.browser_model_presets` 独立提供，响应字段为 `browser_presets`；
不随用户模型删除而移除，也不重新插入用户已删除的模型。当前目录只发布 Gemini Canvas，
网页入口按目录数据生成选项，后续只加入已完成适配的网页服务。
`tests/test_gemini_browser_session.py` 覆盖已登录时保持隐藏、登录后恢复、等待超时、取消、
登录导航完成前拒绝旧文档状态，以及用户自行管理的浏览器不被重启。


桌面与统一后端拆分后，后端 `gemini_canvas_runtime.GeminiCanvasProvider` 负责传递源图和校验返回图片，
本机页面执行在 `desktop_runtime/gemini_canvas_runtime.py`。结果仍由原图片服务保存和关联来源；网页不能执行本机模型，
桌面关闭或连接中断时明确失败/结果待核对，不切换到 API。
连接配置和数据迁移见 [桌面与统一后端](desktop-packaging.md)。
