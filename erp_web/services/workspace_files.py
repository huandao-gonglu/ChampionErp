"""个人空间文件访问校验；路径不能充当跨账号授权。"""
from pathlib import Path
from erp_web.schemas.requests import RequestValidationError


def owned_path(path: Path | str, *, app_dir: Path | str | None = None) -> Path:
    from erp_web.context import peek_context
    context = peek_context()
    root = Path(app_dir) if app_dir is not None else context.paths.app_dir if context else None
    value = Path(path).expanduser()
    if root is not None and not value.is_absolute():
        value = root / value
    value = value.resolve()
    if context is not None and context.user_id:
        root = context.paths.app_dir.resolve()
        # 管理员的根目录包含所有 users，但其文件权限仍只覆盖原个人目录。
        allowed = any(value.is_relative_to((root / name).resolve()) for name in ("data", "config", "dist"))
        if not allowed:
            raise RequestValidationError("文件不属于当前账号的业务目录。", status_code=403, error_code="WORKSPACE_FILE_FORBIDDEN")
    return value
