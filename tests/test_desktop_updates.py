"""桌面分发清单、最低版本与安装包激活边界。"""
import hashlib
import json

import pytest

from deploy.publish_desktop import change_policy, publish
from erp_web.services import desktop_version_policy


def test_publish_preserves_floor_verifies_before_activation_and_can_revoke(tmp_path, monkeypatch):
    root = tmp_path / 'public/desktop-updates'
    installer = tmp_path / 'test.dmg'
    installer.write_bytes(b'macOS installer')
    args = dict(root=root, installer=installer, platform='darwin', arch='arm64',
                sha256=hashlib.sha256(installer.read_bytes()).hexdigest())
    first = publish(**args, version='1.3.0', policy='required')
    current = root / 'darwin-arm64/latest.json'
    assert json.loads(current.read_text()) == first
    assert (current.parent / '1.3.0/installer.dmg').read_bytes() == installer.read_bytes()

    def unavailable(url, size):
        assert url.endswith('/darwin-arm64/1.3.1/installer.dmg')
        assert size == len(installer.read_bytes())
        raise RuntimeError('公网下载不可用')

    with pytest.raises(RuntimeError, match='公网下载'):
        publish(**args, version='1.3.1', verify=unavailable)
    assert json.loads(current.read_text()) == first
    assert publish(**args, version='1.3.1')['minimum'] == '1.3.0'
    monkeypatch.setattr(desktop_version_policy, 'RELEASES', root)
    assert desktop_version_policy.minimum_update('darwin-arm64/1.2.8') == '1.3.0'
    assert desktop_version_policy.minimum_update('darwin-arm64/1.3.0') is None
    assert desktop_version_policy.minimum_update('win32-x64/1.2.8') is None
    with pytest.raises(ValueError, match='当前发布版本已经变化'):
        change_policy(root, 'darwin', 'arm64', '1.3.0', 'required')
    assert change_policy(root, 'darwin', 'arm64', '1.3.1', 'required')['minimum'] == '1.3.1'
    assert change_policy(root, 'darwin', 'arm64', '1.3.1', 'optional', '0.0.0')['minimum'] == '0.0.0'
    assert desktop_version_policy.minimum_update('darwin-arm64/1.2.8') is None

    with pytest.raises(ValueError, match='更新的桌面版本'):
        publish(**args, version='1.2.8')
    before = current.read_bytes()
    installer.write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA-256'):
        publish(**args, version='1.3.2')
    args['sha256'] = hashlib.sha256(installer.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='同一版本'):
        publish(**args, version='1.3.1')
    assert current.read_bytes() == before


def test_signed_zip_is_published_in_immutable_native_feed(tmp_path):
    dmg, archive = tmp_path / 'pack.dmg', tmp_path / 'pack.zip'
    dmg.write_bytes(b'installer')
    archive.write_bytes(b'signed update')
    root = tmp_path / 'public'
    manifest = publish(root, platform='darwin', arch='x64', version='1.2.8', installer=dmg,
                       sha256=hashlib.sha256(dmg.read_bytes()).hexdigest(), mac_zip=archive,
                       zip_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    assert manifest['macZip']['file'] == 'update.zip'
    feed = json.loads((root / 'darwin-x64/1.2.8/native.json').read_text())
    assert feed['url'] == 'https://hdgl.cc.cd/desktop-updates/darwin-x64/1.2.8/update.zip'
