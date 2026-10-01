"""查询外部请求审计、配置平台上限或明确恢复已解决的阻断。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from erp_web.context import get_context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action",required=True)
    query = sub.add_parser("query",help="查询请求尝试与统计")
    for field in ("platform","account-id","interface","operation-id","decision","outcome"):
        query.add_argument("--"+field)
    query.add_argument("--http-status",type=int)
    query.add_argument("--since",type=float,default=0,help="开始时间 Unix 秒")
    query.add_argument("--until",type=float)
    query.add_argument("--limit",type=int,default=100)
    sub.add_parser("blocks",help="查看阻断、恢复条件和累计拦截数")
    sub.add_parser("recoveries",help="查看最近的明确恢复记录")
    recover = sub.add_parser("recover",help="平台条件已经满足后明确恢复；不发送平台探测")
    for field in ("platform","account-id","scope","scope-key","reason"):
        recover.add_argument("--"+field,required=True)
    configure = sub.add_parser("configure",help="依据平台契约设置共享上限；不指定表示无额外上限")
    configure.add_argument("--platform",required=True)
    configure.add_argument("--interface",default="*")
    configure.add_argument("--concurrency",type=int)
    configure.add_argument("--requests-per-minute",type=int)
    configure.add_argument("--consecutive-failure-limit",type=int)
    args = vars(parser.parse_args())
    action = args.pop("action")
    store = get_context().external_requests.store
    if action == "query":
        result = store.query(**{k:v for k,v in args.items() if v is not None})
    elif action == "blocks":
        result = store.blocks()
    elif action == "recoveries":
        result = store.recoveries()
    elif action == "recover":
        store.recover(**args)
        result = {"ok":True,"message":"恢复已记录，未发送任何平台探测"}
    else:
        store.configure(**args)
        result = {"ok":True,"message":"已保存平台请求上限"}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
