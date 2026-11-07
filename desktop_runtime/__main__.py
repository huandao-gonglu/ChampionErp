"""Electron 管道入口；stdout 仅传输 JSON，运行日志写入 stderr。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import json
import logging
import os
from pathlib import Path
import signal
import sys
from threading import Event, Lock

from .browser_debug_service import close_runtime_browsers
from .config import LocalConfig
from .dispatcher import dispatch
from .operation import bind_operation


def main():
    parser = argparse.ArgumentParser(description='Champion ERP 本机执行器')
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = LocalConfig(args.data_dir.expanduser().resolve())
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    output = sys.stdout
    lock = Lock()
    running = {}
    stopped = Event()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='desktop-operation')

    def emit(value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with lock:
            output.write(raw + '\n')
            output.flush()

    def stop(*_):
        stopped.set()
        with lock:
            for cancel in running.values():
                cancel.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def execute(message, cancel):
        call_id = message['id']
        try:
            with bind_operation(cancel, message['timeout']):
                result = dispatch(config, message['operation'], message['params'])
                # 大量图片逐张交接，不把整件商品的图片塞进一个 HTTP 请求。
                for asset in result.pop('assets', []):
                    if asset.get('data'):
                        emit({'id': call_id, 'asset': asset})
                emit({'id': call_id, 'ok': True, 'result': result})
        except Exception as exc:
            logging.exception('本机操作失败：%s', message['operation'])
            emit({'id': call_id, 'ok': False, 'error': str(exc) or '本机操作失败。',
                  'error_code': getattr(exc, 'code', 'CHROME_NOT_FOUND' if isinstance(exc, FileNotFoundError) else 'DESKTOP_EXECUTION_FAILED'),
                  'outcome_unknown': isinstance(exc, (TimeoutError, OSError)) or '结果未知' in str(exc) or cancel.is_set()})
        finally:
            with lock:
                running.pop(call_id, None)

    emit({'ready': True, 'pid': os.getpid()})
    try:
        with redirect_stdout(sys.stderr):
            for line in sys.stdin:
                if stopped.is_set():
                    break
                message = {}
                try:
                    message = json.loads(line)
                    if message.get('cancel'):
                        with lock:
                            if message.get('id') in running:
                                running[message['id']].set()
                        continue
                    if (not isinstance(message.get('id'), str) or not message['id'] or
                            not isinstance(message.get('timeout'), (int, float)) or
                            not 0 < message['timeout'] <= 7200):
                        raise ValueError('本机调用标识或超时无效。')
                    with lock:
                        if message['id'] in running or len(running) >= 16:
                            raise ValueError('本机调用重复或等待队列已满。')
                        cancel = running[message['id']] = Event()
                    pool.submit(execute, message, cancel)
                except Exception as exc:
                    emit({'id': message.get('id', '') if isinstance(message, dict) else '',
                          'ok': False, 'error': str(exc), 'error_code': 'INVALID_DESKTOP_CALL'})
    finally:
        stop()
        # 取消后即可释放专用浏览器，避免被仍在收尾的网络读取拖住。
        close_runtime_browsers()
        pool.shutdown(wait=True, cancel_futures=True)
        close_runtime_browsers()


if __name__ == '__main__':
    main()
