"""字段来源校验：只接受服务端已保存值或可定位、范围匹配的用户消息。"""

import json
import re
from collections.abc import Mapping

from .capability_errors import BusinessCapabilityError


def require_category_user_selection(category_id, execution, *, source_message_id, draft_id):
    """自动匹配必须经过实物对照；只有用户明确指定的类目才能直接保存。"""
    if category_id and not user_supplied_input(
        execution.business_scope, "category_id", value=category_id,
        source_message_id=source_message_id, entity_id=draft_id,
    ):
        raise BusinessCapabilityError(
            "CATEGORY_USER_SELECTION_REQUIRED",
            "直接指定 category_id 需要对应草稿的真实用户选择。自动匹配请留空，"
            "由类目 Agent 检索并核对完整路径和商品实物，不能只凭搜索结果 ID 保存。",
        )




def user_supplied_input(
    business_scope: Mapping[str, str],
    key: str,
    *,
    value=None,
    source_message_id="",
    entity_id="",
) -> bool:
    if value in (None, [], ""):
        return False
    saved = json.loads(business_scope.get("saved_user_facts", "{}"))
    if saved.get(key) == value:
        return True
    if not source_message_id or not entity_id:
        return False
    for fact in json.loads(business_scope.get("user_facts", "[]")):
        if fact.get("message_id") != source_message_id:
            continue
        text = str(fact.get("text", ""))
        if entity_id not in fact.get("draft_ids", []) and not re.search(
            r"(?<![\w-])" + re.escape(entity_id) + r"(?![\w-])", text
        ):
            return False
        values = value if isinstance(value, list) else [value]
        clauses = re.split(r"[，,。；;\n]", text)

        def explicitly_selected(item):
            token = str(item)
            mentions = [clause for clause in clauses if token in clause]
            return bool(mentions) and all(
                not any(
                    word in clause.split(token, 1)[0]
                    for word in ("不要", "不是", "不选", "禁止", "取消")
                )
                for clause in mentions
            )

        return all(explicitly_selected(item) for item in values)
    return False
