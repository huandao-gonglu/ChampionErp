"""查询外部请求审计与调用统计（只读本地数据库，不访问外部平台）。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp_web.context import get_context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    query = sub.add_parser("query", help="查询请求尝试与统计")
    for field in ("platform", "account-id", "interface", "operation-id", "decision", "outcome"):
        query.add_argument("--" + field)
    query.add_argument("--http-status", type=int)
    query.add_argument("--since", type=float, default=0, help="开始时间 Unix 秒")
    query.add_argument("--until", type=float)
    query.add_argument("--limit", type=int, default=100)
    args = vars(parser.parse_args())
    args.pop("action")
    store = get_context().external_requests.store
    result = store.query(**{k: v for k, v in args.items() if v is not None})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
