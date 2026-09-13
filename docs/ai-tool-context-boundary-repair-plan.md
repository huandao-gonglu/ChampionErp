# 已被原生 Agent 方案替代

此文档原方案已退役，历史设计由版本控制保留。当前实现与职责边界见 [AI 上下文地图](ai-context-map.md)，重构范围见 [原生 Agent 重构计划](pydantic-ai-native-agent-refactor-plan.md)，实际验证见 [交付记录](pydantic-ai-native-agent-refactor-delivery.md)。

仍有效的要求已保留：部分补丁不展开未提供字段；写回执保留真实副作用状态；用户资料必须有可信来源；领域 Job 以平台真实终态为准；消息以完整 Pydantic 历史为准；权限、预算和幂等由各自唯一边界执行。
