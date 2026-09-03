# AI 探针测试能力消息历史记录修复

## 问题描述

在进行 AI 模型配置页的能力探测（probe）时，用户发送的探测消息没有被保存到消息历史记录（`message_store`），导致前端无法在对话展示中查看探测过程的消息。

## 根本原因

`request_for_probe` 和相关的探测函数原本没有 `conversation_id`和`message_store` 参数，因此无法将探测过程中产生的消息保存到 Pydantic 官方历史存储中。

## 解决方案

### 1. 修改 `ai_direct_request_service.py`

#### `request_for_probe` 函数
- 添加可选参数 `conversation_id: str | None = None`
- 添加可选参数 `message_store: Any | None = None`
- 成功后保存 messages 到历史，带异常日志记录

#### `request_json_for_probe` 函数
- 传递相同的 `conversation_id`和`message_store` 参数给`request_for_probe`

### 2. 修改 `ai_gateway_probe.py`

#### `CapabilityProbeContext` 数据类
- 添加字段 `conversation_id: str | None = None`
- 添加字段 `message_store: Any | None = None`
- 这些字段会被 `replace()`自动保留

### 3. 修改 `ai_model_probe_service.py`

#### `_probe_capability` 函数
- 从 context 获取 `conversation_id`和`message_store`
- 调用 `request_for_probe` 和`request_json_for_probe` 时传递这些参数

#### `probe_model_capabilities` 函数
- 添加参数并传递给 `CapabilityProbeContext`

#### `test_api_model` 函数
- 添加参数并传递给`probe_model_capabilities`

### 4. 修改 `store_credentials.py`

#### `test_ai_model_config` 函数
- 添加参数 `conversation_id` 和`message_store`
- 当提供 `conversation_id` 但无`message_store` 时，从上下文获取
- 传递参数给 `ai_gateway.test_ai_model`

### 5. 修改 `ai_gateway_providers.py`

#### `test_ai_model` 函数
- 添加参数并传递给相应的 API 实现

### 6. 修改 `copy_facade.py`

#### `test_ai_model_payload` 函数
- 从请求体提取 `conversation_id`
- 传递给 `test_ai_model_config`

## 使用方式

### 前端调用示例

```json
{
  "model": {
    "id": "gpt-4o",
    "provider": "openai"
  },
  "conversation_id": "conv_abc123" 
}
```

当提供了 `conversation_id` 时，探测消息会自动保存到对应 conversation 的历史中。

### Python 调用示例

```python
from erp_web.services.ai_gateway import test_ai_model

result = test_ai_model(
    app_dir=".",
    model=model_config,
    conversation_id="conversation-123",
    message_store=message_store_instance
)
```

## 注意事项

1. **向后兼容**: 新增的参数都是可选的，默认为 `None`，不影响现有代码
2. **安全**: 当 `conversation_id`或`message_store` 为`None` 时，不会尝试保存历史
3. **异常处理**: 如果保存历史失败，会记录警告日志但不影响探测本身
4. **性能**: 只在需要提供 `conversation_id`时才进行历史保存，不会增加额外开销

## 测试

已在 `test_ai_direct_request_service.py` 中添加测试用例验证消息保存功能：

```python
def test_probe_direct_request_saves_messages_to_history_when_conversation_id_provided(...)
```

该测试模拟了 `message_store`并验证在提供`conversation_id` 时消息被正确保存。
