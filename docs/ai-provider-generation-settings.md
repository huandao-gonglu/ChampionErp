# AI 模型生成设置

## 模型 Thinking 开关

“配置 AI 模型”的“启用 Thinking（深度思考）”复选框控制单条模型配置的默认行为：

- 勾选：保存 `thinking_enabled: true`。
- 取消勾选：保存 `thinking_enabled: false`。
- 恢复默认：删除该字段，复选框显示半选并注明“未设置”，沿用高级请求配置或服务商默认。

未包含该字段的已有配置保持原有行为。CLI / 浏览器连接不保存此字段；未接入推理参数映射的服务商禁用开关并显示说明，不显示半选状态。

开关用于文本、JSON 和 Function Call 的能力探测及正式请求。更改开关会清除前端能力证明；
后端将其纳入 `configuration_fingerprint`，使旧指纹证明失效，需要重新探测。

## 覆盖顺序

推理设置由高到低依次为：功能绑定的 `generation.reasoning`、模型的 `thinking_enabled`、
高级配置的 `extra.request_body`、服务商默认。功能绑定留空时继承模型默认；保存绑定时不展开继承值。

功能绑定还可以设置 `temperature`、`max_output_tokens`，以及服务商支持的推理强度或预算。
配置校验和映射统一由 `erp_web/services/ai_generation_settings.py` 持有。

## 服务商映射

| 服务商 | 协议 | Thinking 开关映射 |
| --- | --- | --- |
| OpenAI | Chat Completions / Responses | 原生 `ModelSettings.thinking`，由 Pydantic AI 按模型 profile 转换 |
| oMLX（本地模型） | Chat Completions / Responses | `extra_body.chat_template_kwargs.enable_thinking` |
| 阿里云百炼 / Qwen | Chat Completions | `extra_body.enable_thinking` |
| 阿里云百炼 / Qwen | Responses | `extra_body.reasoning.effort`，关闭为 `none`，开启未指定强度时为 `medium` |

oMLX 当前只提供开启 / 关闭控制，不提供推理强度或预算。DeepSeek 尚未接入此统一开关。
具体模型能否切换思考仍由服务商决定。使用原生 `ModelSettings.thinking` 时，Factory 依据
原生 Model profile 拒绝无有效映射或无法关闭思考的设置，避免 Pydantic AI 静默忽略该开关。

本地 oMLX 模型应显式选择“oMLX（本地模型）”服务商，保留实际 Base URL，选择模型后再设置开关并重新探测。
项目不根据 URL 或模型名猜测服务商。

### oMLX 的 Responses 流标记问题

2026-09-24 在本机 oMLX 的 `Qwen3.8-27B-oQ4` 上复现：发送
`chat_template_kwargs.enable_thinking=false` 后，Responses 流仍把完整 JSON 答案同时放进
`reasoning` 和 `message`。本机服务源码的 `stream_responses_api` 用模型级 `native_reasoning`
初始化思考解析器，没有在这个初始状态中考虑请求级关闭值；未遇到结束标记后，解析器再将内容复制为正文。

同一模型切到 Chat Completions 并发送相同关闭参数，返回仅有正文，且是合法 JSON object。
因此 oMLX 预设默认使用 Chat Completions；已有显式协议选择保持不变，Responses 选项显示说明。
不通过过滤原生 ThinkingPart 隐藏返回内容，也不在调用失败后自动切换协议。

### 本地接口验证记录

2026-09-24 通过 ERP 的 `/api/test-ai-model` 验证 `ai_model_local`（`Qwen3.8-27B-oQ4`）：

- OpenAI / Responses 配置传入 `thinking_enabled=false` 时，原实现仍返回非 JSON object；原生 OpenAI profile 不支持该模型的 Thinking 映射。
- oMLX / Chat Completions / `thinking_enabled=false` 下，聊天、JSON、Function Call 探测全部通过。
- 保存上述配置并重启 ERP 后端后，仅按模型 ID 发起 JSON 探测，仍然通过。
- 带 `X-AI-Presentation-ID` 的页面同款流式请求保存为 `conversation_7e03f0ff5bc04f7ba594b5762682f7de`，原生响应仅包含 `text`，正文可解析为 JSON object。
- 新 Factory 对无有效 Thinking 映射的 OpenAI / Qwen 配置明确报错，避免开关被静默忽略。

## Pydantic AI 职责边界

实现时核对了已安装的 Pydantic AI 2.44.0 和官方
[ModelSettings 文档](https://pydantic.dev/docs/ai/api/pydantic-ai/settings/)、
[OpenAI 兼容服务文档](https://pydantic.dev/docs/ai/models/openai/)。

直接使用原生 `ModelSettings.thinking` 和 `extra_body` 承载配置。
oMLX 的聊天模板字段通过原生 `extra_body` 传入，因为 OpenAI 的模型 profile 不负责 oMLX 的模板开关。
`ai_model_factory` 仍是模型创建入口，Agent 与 Direct Model 请求继续由 Pydantic AI 执行；没有增加推理请求旁路。
