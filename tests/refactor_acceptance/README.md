# 重构验收测试

本目录把 `docs/champion-Erp-设计审查报告.md` 中的 48 个问题转换为可重复执行的验收门禁。`coverage_manifest.py` 维护“报告问题 → 测试”的可追溯清单；元测试会核对报告问题数、pytest 真实收集结果、漏项、失效引用、重复定义、未登记用例及 `skip/xfail`。映射只负责可追溯性，具体断言仍是验收语义的证据。

| 报告章节 | 问题数 | 主要验收方式 |
|---|---:|---|
| 一、应该设计成类但没有 | 12 | 实例隔离、生命周期、连接策略、缓存过期与终态释放 |
| 二、应该抽象但没有 | 10 | registry/Protocol 行为、AST 依赖与分支检查、真实失败关闭 |
| 三、设计错误 | 12 | 真实路由响应、HTTP 400、契约生成、依赖方向和前端边界 |
| 四 A、该进库没进 | 9 | 并发、重启恢复、迁移、脱敏和正式业务入口 |
| 四 B、存了但设计不当 | 5 | 数据库 schema 与实际落盘形状 |

## 运行

首次运行先安装测试依赖：

```bash
python3 -m pip install -r requirements-dev.txt
```

完整门禁会依次执行后端全量测试、前端类型检查、前端测试和前端构建：

```bash
python3 tests/refactor_acceptance/run_acceptance.py
```

只快速检查本目录：

```bash
python3 tests/refactor_acceptance/run_acceptance.py --backend-only --acceptance-only
```

也可以直接调用当前 Python 解释器中的 pytest：

```bash
python3 -m pytest tests/refactor_acceptance -q
```

HTTP 用例默认启动隔离临时目录中的本地后端，不会读取或改写仓库里的真实凭据和业务数据。设置 `ERP_TEST_BASE_URL` 后，验收会拒绝执行写用例；只有对专用测试实例同时显式设置 `ERP_ACCEPTANCE_ALLOW_EXTERNAL_WRITES=1` 才会放行。

## 判定规则

- 不使用 `skip` 或 `xfail` 隐藏未完成项。
- 断言尽量经过真实 facade、service、SQLite 或 HTTP 入口；AST 检查只用于依赖方向、禁用分支和模块边界等架构性质。
- 并发风险必须经过生产业务入口施压并核对数据库结果；声明可恢复的持久化用例必须重新构造 `AppContext`/service 后读取，不能只检查内存对象。
- `/api/state`、写请求校验和设置白名单必须经过真实 HTTP 边界验证，不能由测试内的同名 helper 自证。
- 前端 workflow 类型必须由后端 schema 的生成命令校验，手工维护两套类型视为失败。
- 测试失败表示对应重构目标尚未满足；测试本身异常、资源泄漏或环境硬编码不属于可接受的“红灯”。
