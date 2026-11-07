"""单机发布期间的任务准入与停机判断。锁随持有进程退出自动释放。"""
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import copy_context
from pathlib import Path
from threading import Thread

# 部署脚本创建固定文件。整个发布（含新版验收和回滚）持有入口锁。
ACTIVITIES = (
    "业务请求", "AI 对话", "后台工具", "商品提交", "图片批次", "选品调研",
    "在线商品", "订单处理", "跨境履约", "微信渠道", "账号同步",
)


def locked_file(directory, name, *, exclusive=False, blocking=True):
    # 只有启用 systemd 发布协调的服务器需要 POSIX 文件锁。
    import fcntl

    file = (directory / name).open("rb")
    try:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(file, mode if blocking else mode | fcntl.LOCK_NB)
    except BaseException:
        file.close()
        raise
    return file


class ReleaseGuard:
    def __init__(self):
        self.directory = None

    @contextmanager
    def serving(self, path):
        if not path:
            yield
            return
        directory = Path(path)
        with locked_file(directory, "server", exclusive=True, blocking=False):
            self.directory = directory
            try:
                yield
            finally:
                self.directory = None

    def accepting(self):
        """可中断的只读轮询只检查入口；不将等待平台变为发布阻塞。"""
        with self.admit(None) as allowed:
            return allowed

    def _activity(self, name):
        stack = ExitStack()
        if self.directory is not None:
            try:
                stack.enter_context(locked_file(self.directory, "active"))
                stack.enter_context(locked_file(self.directory, name))
            except BaseException:
                stack.close()
                raise
        return stack

    @contextmanager
    def admit(self, name):
        """互斥地检查入口并登记活动；业务执行期间不持有入口锁。"""
        if self.directory is None:
            yield True
            return
        try:
            admission = locked_file(self.directory, "admission", blocking=False)
        except BlockingIOError:
            yield False
            return
        with admission:
            activity = self._activity(name) if name else nullcontext()
        with activity:
            yield True

    def submit(self, name, submit, *args):
        """已接收工作的子任务在提交前占位，排队和执行都计入等待。"""
        activity = self._activity(name)
        try:
            future = submit(*args)
        except BaseException:
            activity.close()
            raise
        future.add_done_callback(lambda _: activity.close())
        return future

    def start_thread(self, name, thread_name, target, *args):
        activity = self._activity(name)
        context = copy_context()

        def execute():
            with activity:
                context.run(target, *args)

        try:
            Thread(target=execute, name=thread_name, daemon=True).start()
        except BaseException:
            activity.close()
            raise


release_guard = ReleaseGuard()
