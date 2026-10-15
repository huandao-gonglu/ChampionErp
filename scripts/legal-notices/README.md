# 第三方声明来源

`supplements.json` 与 `python-supplements.json` 保存已安装包缺失的上游许可证原文和来源 URL。按生态、包名和精确安装版本关联，不用其他项目的版权声明补齐。少数包未发布带许可证的版本标签，来源为上游仓库分支快照；发行前仍需按实际分发版本核对，不把快照当作法律审计结论。

重新生成：

```sh
# 在仓库根目录，先安装 requirements-dev.txt、根目录 npm 锁文件和 front/pnpm-lock.yaml 对应依赖。
.venv/bin/python scripts/generate_third_party_notices.py
.venv/bin/python scripts/generate_third_party_notices.py --check
# 若分发开发工具或需要所有条目均包含原文，执行严格完整性审核。
.venv/bin/python scripts/generate_third_party_notices.py --check --require-complete
```

生成文件为 `front/src/legal/third-party-notices.json`，随源码保存，查看声明不访问外部网站。内容包括当前平台安装的前端及开发工具、根目录测试工具、requirements 声明的 Python 依赖闭包，以及 Electron 分发包自带的 Chromium credits。依赖包内的嵌套 LICENSE / NOTICE 一并提取；相同文本去重存储。没有上游版本信息的 Chromium 子组件不推测其版本。

部分开发依赖的已发布包和可获取上游资料没有完整许可文本，保留其声明的许可证和项目链接，以 `declaration-only` 明确标记；不能虚构版权人或以别的项目的许可证冒充。完整列表见生成文件的 `incompleteNotices`，生成和核验命令也会打印。目前这些缺项均不在 `pnpm licenses list --prod --json` 的应用运行依赖中。新增补充原文时仍须保留真实上游来源。

Web 和桌面构建校验依赖清单、锁文件、生成脚本及补充声明的指纹；发生变化必须重新生成。发行时在目标平台、实际发行依赖环境运行生成和 `--check`，不要直接复用另一平台的依赖快照。额外捆绑的 Python 解释器、浏览器、二进制工具、字体、图片、未公开其嵌入依赖的原生扩展等，还须以最终安装包内容核查许可证、源码提供及再分发义务。本清单不是对所有许可证义务均已履行的法律保证。
