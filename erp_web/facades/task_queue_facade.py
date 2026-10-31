"""任务页面的只读投影与显式确认入口。"""
from erp_web.context import get_context


def read(query):
    queue = get_context().task_queue
    return queue.detail(query["id"]) if query.get("id") else queue.list(query)


def check(body):
    return get_context().task_queue.check(body["task_id"])


__all__ = ["read", "check"]
