"""兼容 PinCC Grok Responses 正文中的推理标签，不接管 Agent 或事件协议。

Pydantic AI 2.44.0 的 Responses 文本路径不解析标签。此适配只拆分文本，
继续用原生 PartsManager、StreamedResponse 和 Provider 处理事件、用量与取消。
待该接口返回独立 reasoning 事件或上游原生支持后移除此适配。
"""

from dataclasses import dataclass, field, fields, replace
from functools import cached_property
from typing import Any

from pydantic_ai._parts_manager import ModelResponsePartsManager
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ModelResponsePart, TextPart, ThinkingPart
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesStreamedResponse


@dataclass
class TaggedThinkingParser:
    """只识别片段开头的协议标签；正文、代码示例中的同名标签保持原文。"""

    state: str = "prefix"
    pending: str = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        self.pending += text
        result = []
        while self.pending:
            if self.state == "text":
                result.append(("text", self.pending))
                self.pending = ""
            elif self.state == "prefix":
                stripped = self.pending.lstrip()
                if stripped.startswith("<think>"):
                    self.pending = stripped[len("<think>"):]
                    self.state = "thinking"
                elif "<think>".startswith(stripped):
                    # 限制前导空白缓存，标签本身最多保留 6 个字符。
                    if len(self.pending) > 256:
                        whitespace = len(self.pending) - len(stripped)
                        result.append(("text", self.pending[:whitespace]))
                        self.pending = stripped
                    break
                else:
                    self.state = "text"
            else:
                end = self.pending.find("</think>")
                if end >= 0:
                    if end:
                        result.append(("thinking", self.pending[:end]))
                    self.pending = self.pending[end + len("</think>"):]
                    self.state = "prefix"
                else:
                    keep = next((size for size in range(min(7, len(self.pending)), 0, -1)
                                 if "</think>".startswith(self.pending[-size:])), 0)
                    ready = self.pending[:-keep] if keep else self.pending
                    if ready:
                        result.append(("thinking", ready))
                    self.pending = self.pending[-keep:] if keep else ""
                    break
        return result

    def finish(self) -> list[tuple[str, str]]:
        if not self.pending:
            return []
        result = [("thinking" if self.state == "thinking" else "text", self.pending)]
        self.pending = ""
        return result


def _check_unclosed_answer(parts: list[ModelResponsePart], *, unclosed: bool, complete: bool) -> None:
    if (unclosed and complete
            and all(isinstance(part, ThinkingPart) or isinstance(part, TextPart) and not part.content.strip()
                    for part in parts)):
        raise UnexpectedModelBehavior(
            "模型接口返回的推理标签未闭合，无法识别最终答复。请检查中转接口的 Thinking 返回格式。"
        )


def normalize_tagged_response(response: ModelResponse) -> ModelResponse:
    """非流式结果采用同一分类规则；不改写原生 reasoning、工具或历史数据。"""
    parts = []
    unclosed = False
    for part in response.parts:
        if not isinstance(part, TextPart) or not part.content:
            parts.append(part)
            continue
        parser = TaggedThinkingParser()
        for kind, content in [*parser.feed(part.content), *parser.finish()]:
            if kind == "thinking":
                # 不是服务商原生 reasoning item，不能伪造可回传的 reasoning ID。
                parts.append(ThinkingPart(content, provider_name=part.provider_name))
            else:
                parts.append(replace(part, content=content))
        unclosed = unclosed or parser.state == "thinking"
    normalized = replace(response, parts=parts)
    _check_unclosed_answer(normalized.parts, unclosed=unclosed, complete=normalized.state == "complete")
    return normalized


@dataclass(repr=False)
class _TaggedPartsManager(ModelResponsePartsManager):
    parsers: dict[Any, TaggedThinkingParser] = field(default_factory=dict)
    metadata: dict[Any, dict] = field(default_factory=dict)

    def _emit_segments(self, key, segments):
        metadata = self.metadata[key]
        for kind, content in segments:
            if kind == "thinking":
                yield from super().handle_thinking_delta(
                    vendor_part_id=(key, "tagged-thinking"), content=content,
                    provider_name=metadata.get("provider_name"),
                )
            else:
                yield from super().handle_text_delta(vendor_part_id=key, content=content, **metadata)

    def handle_text_delta(self, *, vendor_part_id, content, **kwargs):
        parser = self.parsers.setdefault(vendor_part_id, TaggedThinkingParser())
        metadata = self.metadata.setdefault(vendor_part_id, {})
        if details := kwargs.get("provider_details"):
            kwargs["provider_details"] = {**metadata.get("provider_details", {}), **details}
        metadata.update({key: value for key, value in kwargs.items() if value is not None})
        # 标签已由增量解析器消费，不让原生单 token 标签逻辑再次解释普通正文。
        metadata.pop("thinking_tags", None)
        yield from self._emit_segments(vendor_part_id, parser.feed(content))
        if not content and parser.state == "text":
            # output_text.done 可能只补充引用或 logprobs；仍交回原生元数据合并。
            yield from self._emit_segments(vendor_part_id, [("text", "")])

    def finish_text(self):
        for key, parser in self.parsers.items():
            yield from self._emit_segments(key, parser.finish())


@dataclass
class _TaggedResponsesStream(OpenAIResponsesStreamedResponse):
    @cached_property
    def _parts_manager(self):
        return _TaggedPartsManager(model_request_parameters=self.model_request_parameters)

    async def _get_event_iterator(self):
        try:
            async for event in super()._get_event_iterator():
                yield event
        finally:
            # 包括正常结束和连接中断：保留最后几个字符，不泄露未闭合的推理。
            # 中断时只更新历史，不在异常/取消期间继续向已关闭的消费者发送事件。
            tail = list(self._parts_manager.finish_text())
        for event in tail:
            yield event
        _check_unclosed_answer(self._parts_manager.get_parts(), complete=self.state == "complete",
                              unclosed=any(parser.state == "thinking"
                                           for parser in self._parts_manager.parsers.values()))


class PinccResponsesModel(OpenAIResponsesModel):
    """保留原生 Responses 实现，仅替换文本分类扩展点。"""

    def _process_response(self, response, model_settings, model_request_parameters):
        return normalize_tagged_response(super()._process_response(
            response, model_settings, model_request_parameters,
        ))

    async def _process_streamed_response(self, *args, **kwargs):
        original = await super()._process_streamed_response(*args, **kwargs)
        # 使用原生构造结果，保留续传、后台状态、用量和服务商元数据。
        native_fields = fields(original)
        adapted = _TaggedResponsesStream(**{
            item.name: getattr(original, item.name) for item in native_fields if item.init
        })
        for item in native_fields:
            if not item.init:
                setattr(adapted, item.name, getattr(original, item.name))
        return adapted


__all__ = ["PinccResponsesModel", "TaggedThinkingParser", "normalize_tagged_response"]
