# 第三方声明来源

`supplements.json` 与 `python-supplements.json` 保存已安装包缺失的上游许可证原文和来源 URL。按生态、包名和精确安装版本关联，不用其他项目的版权声明补齐。少数包未发布带许可证的版本标签，来源为上游仓库分支快照；发行前仍需按实际分发版本核对，不把快照当作法律审计结论。

重新生成：

```sh
# 在仓库根目录，先安装 requirements.txt 和 front/pnpm-lock.yaml 对应依赖。
.venv/bin/python scripts/generate_third_party_notices.py
.venv/bin/python scripts/generate_third_party_notices.py --check
# 发行时检查所有交付依赖均有许可证原文。
.venv/bin/python scripts/generate_third_party_notices.py --check --require-complete
```

生成文件为 `front/src/legal/third-party-notices.json`，仅作为构建输入随源码保存，不发送给页面。来源限定为前端 `pnpm licenses list --prod` 的生产依赖、`requirements.txt` 的 Python 运行依赖闭包。不收集根目录 Playwright、Python 测试依赖、前端构建与测试工具。Electron 虽列在开发依赖中，其运行时仍随桌面版交付，因此在桌面构建时原样复制其官方许可证和 Chromium 声明，不再解析为页面数据。

`front/build/third-party-notices.ts` 生成 `licenses/THIRD-PARTY-NOTICES.txt`：Web 按 Rollup 产物中有实际代码的 npm 模块筛选，保留配套后端运行依赖；桌面包还包含生产 `node_modules`，因此保留生产依赖闭包。桌面构建同时复制 `electron/dist/LICENSE` 和 `LICENSES.chromium.html` 到 `licenses/Electron/`。构建发现未登记的 npm 模块时失败，不能静默漏掉声明。开发服务没有最终产物，下载文件包含 Web 生产依赖候选清单；发行清单以构建输出为准。新增单独复制的第三方静态资产时须把它们纳入收集范围。

依赖包内的嵌套 LICENSE / NOTICE 一并提取。对有明确起止标记的 MIT、Apache 2.0、BSD、ISC 正文分段，仅忽略排版空白进行去重，不按许可证名称猜测等价；版权、附录、特殊声明和未识别文本保持原文。文本文件按“名称、版本、项目地址、版权与声明”组织，共同正文用编号引用并在末尾完整保留，同名但措辞不同的条款分别保留。完全相同的长篇复合声明也集中保存并保留组件引用，简短版权逐项保留。

“关于”页不再加载或展开声明全文。Web 入口下载上述 TXT，后端只为 `/licenses/THIRD-PARTY-NOTICES.txt` 注册静态下载路径，不开放任意目录文件；桌面入口通过专用 IPC 打开固定的 `process.resourcesPath/licenses` 目录，不接受页面指定路径。`electron-builder.extraResources` 将该目录复制到安装包资源目录，放在 ASAR 外便于阅读；桌面开发模式打开 `front/dist-desktop/licenses`。没有声明文件时应修复构建产物，不能改回全文弹窗或悄悄跳过随包声明。

缺少原文的包保留声明的许可证和项目链接，以 `declaration-only` 标记，并列入 `incompleteNotices`；不能虚构版权人或以别的项目的许可证冒充。新增补充原文时须保留真实上游来源。

Web 和桌面构建校验依赖清单、锁文件、生成脚本及补充声明的指纹；发生变化必须重新生成。发行时在目标平台、实际发行依赖环境运行生成和 `--check`，不要直接复用另一平台的依赖快照。额外捆绑的 Python 解释器、浏览器、二进制工具、字体、图片、未公开其嵌入依赖的原生扩展等，还须以最终安装包内容核查许可证、源码提供及再分发义务。本清单不是对所有许可证义务均已履行的法律保证。
