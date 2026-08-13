# 项目文档索引

`docs/` 保存需要随代码一起评审、提交和维护的正式文档。本地一次性分析、临时草稿和工具输出
应放入仓库根目录的 `doc/`，该目录不会进入版本控制。

## 当前设计与运行边界

- [AI Context Map](./ai-context-map.md)：当前代码的 AI 入口、分层、状态所有者和架构守卫，
  是定位实现边界的首要索引。
- [API 接口清单](./api-endpoints.md)：按实际 HTTP 路由维护的公开接口及顶层参数说明。
- [类目业务链路](./类目业务链路.md)：类目匹配从前端到 focused Agent、平台搜索和 AI Work
  投影的时序图。

## 已实施方案与设计基线

- [全局 Agent 已实施方案](./global-agent-next-stage.md)：全局 Agent 顺序任务、暂停恢复、发布确认、
  发布终态和主从 AI Work 对话的设计基线。实现偏差与加固原因见
  [实施变更记录](../nextStateDoingChange.md)。
- [AI Tool 注解化升级方案](./ai-tool-annotation-upgrade.md)：类型化能力、`@ai_tool`、Compiler、
  Catalog、可信 Scope 和写工具门控方案。
- [ERP AI Task、工具调用与工作流架构](./ai-task-tool-workflow-architecture.md)：Pydantic Agent、
  Direct Model、AI Work、Tool Runtime 和领域 Capability 的总体架构边界。

## 历史决策记录

- [设计审查与重构记录](./champion-Erp-设计审查报告.md)：2026 年 7 月架构审查及后续接管执行记录。
  该文档用于解释重构缘由；当前代码边界以 `ai-context-map.md` 和仓库根目录 `AGENTS.md` 为准。

## 维护约定

- 新增或变更公开 HTTP 行为时，同步更新 `api-endpoints.md`。
- AI 模块移动、拆分或增加公开入口时，同步更新 `ai-context-map.md` 和对应架构测试。
- 已实施方案保留目标设计基线；实现中采用的替代方案、风险加固和理由写入对应变更记录，
  不通过改写历史方案掩盖差异。
- 文档中的状态、日期、路径和验收结果必须与当前代码一致；失效文档应明确标记为历史记录或删除。
