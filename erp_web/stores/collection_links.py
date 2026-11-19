"""个人空间的 URL 采集清单、最近结果及诊断记录。"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4

from erp_shared.source_sites import detect_source_site
from erp_web.schemas.collection import CollectionLink, CollectionLinkInput, LegacyCollectionLink
from erp_web.schemas.requests import RequestValidationError


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _saved_product(row):
    summary = json.loads(row['summary'])
    return {key: summary[key] for key in (
        'productId', 'title', 'image', 'price', 'currency', 'skuCount', 'imageCount', 'partial',
    ) if key in summary}


def _conflict():
    return RequestValidationError('链接已在其他页面更新，请按最新状态操作。', status_code=409)


class CollectionLinks:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._write() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS links (
                id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, mode TEXT NOT NULL,
                status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                summary TEXT NOT NULL DEFAULT '{}', verification TEXT NOT NULL DEFAULT '{}',
                updated TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                link_id TEXT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
                status TEXT NOT NULL, message TEXT NOT NULL, code TEXT NOT NULL, at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS events_link ON events(link_id)")

    def mark_interrupted(self):
        """仅在统一后端启动恢复阶段调用，普通页面读取不会改变运行状态。"""
        with self._write() as db:
            for row in db.execute("SELECT id FROM links WHERE status='running'").fetchall():
                self._finish(db, row['id'], 'unconfirmed', {
                    'error': '后台服务已中断，请先在采集箱核对本次结果。',
                    'errorCode': 'COLLECT_RESULT_UNCONFIRMED', 'nextAction': '',
                }, {})

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def _write(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _public(row) -> CollectionLink:
        return {**json.loads(row['summary']), 'id': row['id'], 'url': row['url'], 'mode': row['mode'],
                'status': row['status'], 'revision': row['revision'], 'updatedAt': row['updated'],
                'platform': detect_source_site(row['url'])}

    def list(self):
        with closing(self._connect()) as db:
            return {'items': [self._public(row) for row in db.execute('SELECT id,url,mode,status,revision,updated,summary FROM links ORDER BY rowid')]}

    def history(self, key):
        with closing(self._connect()) as db:
            return {'items': [dict(row) for row in db.execute(
                'SELECT at,status,message,code FROM events WHERE link_id=? ORDER BY rowid DESC', (key,))]}

    @staticmethod
    def _check(db, key, revision):
        row = db.execute('SELECT * FROM links WHERE id=?', (key,)).fetchone()
        if row is None or row['revision'] != revision or row['status'] == 'running':
            raise _conflict()
        return row

    @staticmethod
    def _event(db, key, status, message, code=''):
        db.execute('INSERT INTO events VALUES(?,?,?,?,?)', (key, status, message, code, _now()))

    def add(self, entries, *, legacy=False):
        if not isinstance(entries, list) or not entries:
            raise RequestValidationError('请填写要添加的商品链接。')
        schema = LegacyCollectionLink if legacy else CollectionLinkInput
        items = [schema.model_validate(entry) for entry in entries]
        added = 0
        with self._write() as db:
            for item in items:
                key, status, summary, verification = str(uuid4()), 'pending', {}, {}
                if legacy:
                    status = item.status
                    summary = item.model_dump(include={'title', 'image', 'productId', 'error', 'errorCode', 'nextAction'})
                    old = item.verification or {}
                    if status == 'waiting_verification':
                        if old.get('sourceUrl') == item.url and old.get('browserTabId'):
                            verification = {'source_url': item.url, 'browser_tab_id': old['browserTabId'],
                                            'platform': detect_source_site(item.url)}
                        else:
                            status = 'pending'
                inserted = db.execute("""INSERT INTO links(id,url,mode,status,summary,verification,updated)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(url) DO NOTHING""",
                    (key, item.url, item.mode, status, json.dumps(summary), json.dumps(verification), _now()))
                if inserted.rowcount:
                    added += 1
                    self._event(db, key, status, '从本机采集列表导入' if legacy else '已添加链接')
        return {'added': added, 'duplicates': len(items) - added}

    def manage(self, body):
        action = body['action']
        if action in ('add', 'import'):
            return self.add(body.get('items'), legacy=action == 'import')
        key = body.get('id')
        with self._write() as db:
            row = self._check(db, key, body.get('revision'))
            if action == 'remove':
                db.execute('DELETE FROM links WHERE id=?', (key,))
            elif action == 'edit':
                item = CollectionLinkInput.model_validate(body)
                if item.url != row['url']:
                    if db.execute('SELECT 1 FROM links WHERE url=?', (item.url,)).fetchone():
                        raise RequestValidationError('此 URL 已在列表中。')
                    db.execute('DELETE FROM events WHERE link_id=?', (key,))
                    summary = {}
                elif item.mode == row['mode']:
                    return {'ok': True}
                else:
                    summary = _saved_product(row)
                db.execute("""UPDATE links SET url=?,mode=?,revision=revision+1,status='pending',
                    summary=?,verification='{}',updated=? WHERE id=?""",
                           (item.url, item.mode, json.dumps(summary), _now(), key))
                self._event(db, key, 'pending', '已更新链接设置')
            else:
                raise RequestValidationError('未知的链接操作。')
        return {'ok': True}

    def target(self, key, revision):
        with closing(self._connect()) as db:
            row = self._check(db, key, revision)
            return row['url'], row['mode'], json.loads(row['verification'])

    def begin(self, key, revision):
        with self._write() as db:
            row = self._check(db, key, revision)
            db.execute("""UPDATE links SET status='running',revision=revision+1,summary=?,updated=? WHERE id=?""",
                       (json.dumps(_saved_product(row)), _now(), key))
            self._event(db, key, 'running', '开始采集')
            return revision + 1

    def verification_failed(self, key, revision, message):
        with self._write() as db:
            self._check(db, key, revision)
            self._finish(db, key, 'failed', {'error': message, 'errorCode': 'TAB_NOT_ACCESSIBLE', 'nextAction': '请重新开始采集。'}, {})

    def finish(self, key, running_revision, status, summary, verification=None):
        with self._write() as db:
            active = db.execute("SELECT 1 FROM links WHERE id=? AND status='running' AND revision=?",
                                (key, running_revision)).fetchone()
            if not active:
                raise _conflict()
            self._finish(db, key, status, summary, verification or {})
            return self._public(db.execute('SELECT * FROM links WHERE id=?', (key,)).fetchone())

    def _finish(self, db, key, status, summary, verification):
        previous = _saved_product(db.execute('SELECT summary FROM links WHERE id=?', (key,)).fetchone())
        previous.update(summary)
        db.execute("""UPDATE links SET status=?,summary=?,verification=?,revision=revision+1,
                    updated=? WHERE id=?""",
                   (status, json.dumps(previous), json.dumps(verification), _now(), key))
        self._event(db, key, status, summary.get('error') or summary.get('message') or summary.get('nextAction') or '采集完成',
                    summary.get('errorCode', ''))
