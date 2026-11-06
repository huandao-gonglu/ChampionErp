"""停止 ERP 服务后，一致性备份账号库、全部业务空间和桥接配置。"""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import sqlite3
import tarfile
import tempfile


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(app_dir, service_env, output):
    app_dir, output = app_dir.resolve(), output.resolve()
    if output.is_relative_to(app_dir):
        raise ValueError("备份输出必须位于业务目录外")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError("备份文件已存在，拒绝覆盖")
    with tempfile.TemporaryDirectory(prefix="erp-account-backup-") as temporary:
        snapshots = Path(temporary)
        manifest = {}
        with output.open("xb") as raw:
            output.chmod(0o600)
            with tarfile.open(fileobj=raw, mode="w:gz", dereference=False) as archive:
                for source in sorted(app_dir.rglob("*")):
                    relative = source.relative_to(app_dir)
                    parts = relative.parts
                    if parts[0] == "backups" or (len(parts) > 2 and parts[0] == "users" and parts[2] == "backups"):
                        continue
                    if source.name.endswith(("-wal", "-shm")):
                        continue
                    name = "app/" + relative.as_posix()
                    if source.is_symlink():
                        target = source.resolve()
                        if not target.is_relative_to(app_dir):
                            raise ValueError("业务目录包含外部符号链接，须先核对其备份归属")
                        info = archive.gettarinfo(str(source), arcname=name)
                        info.linkname = posixpath.relpath(target.relative_to(app_dir).as_posix(), relative.parent.as_posix())
                        archive.addfile(info)
                        continue
                    if not source.is_file():
                        continue
                    stored = source
                    if source.suffix in {".sqlite3", ".sqlite", ".db"}:
                        stored = snapshots / relative
                        stored.parent.mkdir(parents=True, exist_ok=True)
                        with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as original, sqlite3.connect(stored) as target:
                            original.backup(target)
                            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                                raise RuntimeError("业务数据库完整性检查失败")
                    archive.add(stored, arcname=name, recursive=False)
                    manifest[name] = digest_file(stored)
                if service_env.exists():
                    archive.add(service_env, arcname="service/newapi.env", recursive=False)
                    manifest["service/newapi.env"] = digest_file(service_env)
                manifest_path = snapshots / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.add(manifest_path, arcname="manifest.json")


def verify(path):
    with tempfile.TemporaryDirectory(prefix="erp-account-restore-") as temporary, tarfile.open(path, "r:gz") as archive:
        root = Path(temporary)
        archive.extractall(root, filter="data")
        for name, digest in json.loads((root / "manifest.json").read_text()).items():
            restored = root / name
            if digest_file(restored) != digest:
                raise RuntimeError("恢复文件与备份摘要不一致")
            if restored.suffix in {".sqlite3", ".sqlite", ".db"}:
                with sqlite3.connect(restored) as db:
                    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("恢复数据库完整性检查失败")
        print("ERP 恢复验证通过：账号、各业务库及文件摘要一致。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "verify"])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path, default=Path("/var/lib/champion-erp"))
    parser.add_argument("--service-env", type=Path, default=Path("/etc/champion-erp/newapi.env"))
    args = parser.parse_args()
    if args.action == "backup":
        backup(args.app_dir, args.service_env, args.archive)
        print("ERP 账号与业务空间备份完成。")
    else:
        verify(args.archive)


if __name__ == "__main__":
    main()
