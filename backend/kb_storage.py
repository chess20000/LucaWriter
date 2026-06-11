import os
import sqlite3
import json
import time
import uuid
import threading
import hashlib
import re
import shutil
import numpy as np
from contextlib import contextmanager

_local_storage = threading.local()
_db_locks = {}
_db_locks_meta = threading.Lock()

def get_kb_path(book_id):
    from main import get_book_dir
    return os.path.join(get_book_dir(book_id), 'kb.db')

def _get_lock(book_id):
    # 锁按 db 路径而非 book_id：SaaS 多租户下不同租户可能有相同 book_id
    key = get_kb_path(book_id)
    lock = _db_locks.get(key)
    if lock is not None:
        return lock
    with _db_locks_meta:
        lock = _db_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _db_locks[key] = lock
        return lock

@contextmanager
def db_transaction(book_id):
    lock = _get_lock(book_id)
    lock.acquire()
    conn = _get_conn(book_id)
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=5000')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        lock.release()

def _get_conn(book_id):
    db_path = get_kb_path(book_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn

def wal_checkpoint(book_id, mode='passive'):
    """截断 WAL 文件，防止长时间通读导致 WAL 无限增长。mode: passive|full|truncate"""
    try:
        conn = _get_conn(book_id)
        try:
            conn.execute(f'PRAGMA wal_checkpoint({mode})')
        finally:
            conn.close()
    except Exception:
        pass

_initialized_dbs = {}  # db_path -> inode；os.replace 恢复快照/删库后 inode 变化会自动重跑建表
_initialized_dbs_lock = threading.Lock()

def init_db(book_id):
    db_path = get_kb_path(book_id)
    try:
        ino = os.stat(db_path).st_ino
    except OSError:
        ino = None
    if ino is not None:
        with _initialized_dbs_lock:
            if _initialized_dbs.get(db_path) == ino:
                return
    _init_db_schema(book_id)
    try:
        ino = os.stat(db_path).st_ino
    except OSError:
        return
    with _initialized_dbs_lock:
        _initialized_dbs[db_path] = ino

def _init_db_schema(book_id):
    with db_transaction(book_id) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS chapters (
          id              TEXT PRIMARY KEY,
          book_id         TEXT NOT NULL,
          idx             INTEGER NOT NULL,
          title           TEXT NOT NULL,
          content_hash    TEXT,
          summary         TEXT,
          status          TEXT NOT NULL DEFAULT 'pending',
          error           TEXT,
          tokens_used     INTEGER DEFAULT 0,
          updated_at      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id, idx);
        CREATE INDEX IF NOT EXISTS idx_chapters_status ON chapters(book_id, status);

        CREATE TABLE IF NOT EXISTS entities (
          id                TEXT PRIMARY KEY,
          book_id           TEXT NOT NULL,
          canonical_name    TEXT NOT NULL,
          type              TEXT NOT NULL,
          aliases           TEXT NOT NULL DEFAULT '[]',
          first_chapter_id  TEXT,
          updated_at        INTEGER NOT NULL,
          UNIQUE(book_id, canonical_name)
        );
        CREATE INDEX IF NOT EXISTS idx_entities_book ON entities(book_id);
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(book_id, type);

        CREATE TABLE IF NOT EXISTS mentions (
          id           TEXT PRIMARY KEY,
          entity_id    TEXT NOT NULL,
          chapter_id   TEXT NOT NULL,
          fact         TEXT NOT NULL,
          snippet      TEXT,
          created_at   INTEGER NOT NULL,
          FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity_id);
        CREATE INDEX IF NOT EXISTS idx_mentions_chapter ON mentions(chapter_id);

        CREATE TABLE IF NOT EXISTS events (
          id           TEXT PRIMARY KEY,
          book_id      TEXT NOT NULL,
          chapter_id   TEXT NOT NULL,
          story_time   TEXT,
          who          TEXT,
          what         TEXT NOT NULL,
          where_loc    TEXT,
          why          TEXT,
          consequence  TEXT,
          created_at   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_book ON events(book_id, chapter_id);

        CREATE TABLE IF NOT EXISTS foreshadowing (
          id                    TEXT PRIMARY KEY,
          book_id               TEXT NOT NULL,
          hint_chapter_id       TEXT NOT NULL,
          hint                  TEXT NOT NULL,
          status                TEXT NOT NULL DEFAULT 'open',
          resolved_chapter_id   TEXT,
          resolution            TEXT,
          updated_at            INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_foreshadowing_book ON foreshadowing(book_id, status);

        CREATE TABLE IF NOT EXISTS rules (
          id                TEXT PRIMARY KEY,
          book_id           TEXT NOT NULL,
          name              TEXT NOT NULL,
          body              TEXT NOT NULL,
          first_chapter_id  TEXT,
          updated_at        INTEGER NOT NULL,
          UNIQUE(book_id, name)
        );

        CREATE TABLE IF NOT EXISTS rule_mentions (
          id           TEXT PRIMARY KEY,
          rule_id      TEXT NOT NULL,
          chapter_id   TEXT NOT NULL,
          evidence     TEXT,
          created_at   INTEGER NOT NULL,
          FOREIGN KEY (rule_id) REFERENCES rules(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_rule_mentions_rule ON rule_mentions(rule_id);
        CREATE INDEX IF NOT EXISTS idx_rule_mentions_chapter ON rule_mentions(chapter_id);

        CREATE TABLE IF NOT EXISTS timeline_event_meta (
          event_id      TEXT PRIMARY KEY,
          book_id       TEXT NOT NULL,
          story_order   INTEGER,
          segment_id    TEXT,
          segment_title TEXT,
          lane          INTEGER DEFAULT 0,
          importance    INTEGER DEFAULT 2,
          zoom_level    INTEGER DEFAULT 2,
          confidence    REAL DEFAULT 0.5,
          status        TEXT NOT NULL DEFAULT 'ai',
          reason        TEXT,
          evidence      TEXT,
          updated_at    INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_timeline_meta_book ON timeline_event_meta(book_id, story_order);

        CREATE TABLE IF NOT EXISTS timeline_relations (
          id              TEXT PRIMARY KEY,
          book_id         TEXT NOT NULL,
          source_event_id TEXT NOT NULL,
          target_event_id TEXT,
          relation        TEXT NOT NULL,
          confidence      REAL DEFAULT 0.5,
          status          TEXT NOT NULL DEFAULT 'ai',
          evidence        TEXT,
          note            TEXT,
          created_at      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_timeline_rel_book ON timeline_relations(book_id);
        CREATE INDEX IF NOT EXISTS idx_timeline_rel_source ON timeline_relations(source_event_id);

        CREATE TABLE IF NOT EXISTS consistency_alerts (
          id           TEXT PRIMARY KEY,
          book_id      TEXT NOT NULL,
          chapter_id   TEXT,
          kind         TEXT,
          severity     TEXT,
          message      TEXT NOT NULL,
          evidence     TEXT,
          suggestion   TEXT,
          status       TEXT NOT NULL DEFAULT 'open',
          source_hash  TEXT,
          created_at   INTEGER NOT NULL,
          updated_at   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_consistency_alerts_book ON consistency_alerts(book_id, chapter_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS rt_state (
          book_id          TEXT PRIMARY KEY,
          status           TEXT NOT NULL,
          current_idx      INTEGER DEFAULT -1,
          total            INTEGER DEFAULT 0,
          phase            TEXT,
          error            TEXT,
          pause_requested  INTEGER NOT NULL DEFAULT 0,
          stream_buffer    TEXT DEFAULT '',
          active_start_idx INTEGER DEFAULT -1,
          active_end_idx   INTEGER DEFAULT -1,
          updated_at       INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rt_logs (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          book_id     TEXT NOT NULL,
          ts          TEXT NOT NULL,
          msg         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rt_logs_book ON rt_logs(book_id, id);

        CREATE TABLE IF NOT EXISTS embedding_chunks (
          id           TEXT PRIMARY KEY,
          book_id      TEXT NOT NULL,
          source_type  TEXT NOT NULL,
          source_id    TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          backend_id   TEXT NOT NULL,
          embedded_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_embedding_chunks_source ON embedding_chunks(book_id, source_type, source_id);

        CREATE TABLE IF NOT EXISTS vector_entries (
          id            TEXT PRIMARY KEY,
          book_id       TEXT NOT NULL,
          embedding     BLOB NOT NULL,
          dimensions    INTEGER NOT NULL,
          document      TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          updated_at    INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vector_entries_book ON vector_entries(book_id);

        CREATE TABLE IF NOT EXISTS kb_proposals (
          id              TEXT PRIMARY KEY,
          book_id         TEXT NOT NULL,
          table_name      TEXT NOT NULL,
          record_id       TEXT NOT NULL,
          field           TEXT NOT NULL,
          old_value       TEXT,
          new_value       TEXT,
          reason          TEXT,
          status          TEXT NOT NULL DEFAULT 'pending',
          source_message  TEXT,
          created_at      INTEGER NOT NULL,
          resolved_at     INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_proposals_book_status ON kb_proposals(book_id, status, created_at);

        CREATE TABLE IF NOT EXISTS kb_edit_log (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          book_id         TEXT NOT NULL,
          table_name      TEXT NOT NULL,
          record_id       TEXT NOT NULL,
          field           TEXT NOT NULL,
          old_value       TEXT,
          new_value       TEXT,
          reason          TEXT,
          source          TEXT NOT NULL,
          proposal_id     TEXT,
          created_at      INTEGER NOT NULL,
          undone          INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_edit_log_book_time ON kb_edit_log(book_id, created_at DESC);

        -- P1: 实体归并/模糊匹配
        CREATE TABLE IF NOT EXISTS entity_aliases (
          id          TEXT PRIMARY KEY,
          book_id     TEXT NOT NULL,
          entity_id   TEXT NOT NULL,
          alias_norm  TEXT NOT NULL,
          alias_raw   TEXT NOT NULL,
          source      TEXT NOT NULL DEFAULT 'ai',
          created_at  INTEGER NOT NULL,
          FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_entity_aliases_lookup ON entity_aliases(book_id, alias_norm);

        CREATE TABLE IF NOT EXISTS entity_merges (
          id          TEXT PRIMARY KEY,
          book_id     TEXT NOT NULL,
          from_name   TEXT NOT NULL,
          into_id     TEXT NOT NULL,
          reason      TEXT,
          similarity  REAL,
          created_at  INTEGER NOT NULL
        );

        -- P3: 事实版本化
        CREATE TABLE IF NOT EXISTS fact_revisions (
          id           TEXT PRIMARY KEY,
          book_id      TEXT NOT NULL,
          entity_id    TEXT,
          chapter_id   TEXT NOT NULL,
          fact         TEXT NOT NULL,
          snippet      TEXT,
          confidence   REAL DEFAULT 0.6,
          pass_no      INTEGER DEFAULT 1,
          status       TEXT NOT NULL DEFAULT 'active',
          supersedes   TEXT,
          reason       TEXT,
          created_at   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fact_rev_entity ON fact_revisions(book_id, entity_id, status);

        -- P4: 后台自动更新队列
        CREATE TABLE IF NOT EXISTS kb_dirty_queue (
          id          TEXT PRIMARY KEY,
          work_id     TEXT NOT NULL,
          book_id     TEXT NOT NULL,
          chapter_id  TEXT NOT NULL,
          reason      TEXT,
          enqueued_at INTEGER NOT NULL,
          status      TEXT NOT NULL DEFAULT 'pending',
          attempts    INTEGER DEFAULT 0,
          UNIQUE(work_id, book_id, chapter_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dirty_pending ON kb_dirty_queue(work_id, status, enqueued_at);
        ''')
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(rt_state)').fetchall()}
        if 'active_start_idx' not in cols:
            conn.execute('ALTER TABLE rt_state ADD COLUMN active_start_idx INTEGER DEFAULT -1')
        if 'active_end_idx' not in cols:
            conn.execute('ALTER TABLE rt_state ADD COLUMN active_end_idx INTEGER DEFAULT -1')

        # P1: 幂等迁移 - entities 加 name_norm/mention_cnt
        ecols = {r['name'] for r in conn.execute('PRAGMA table_info(entities)').fetchall()}
        if 'name_norm' not in ecols:
            conn.execute('ALTER TABLE entities ADD COLUMN name_norm TEXT DEFAULT \'\'')
        if 'mention_cnt' not in ecols:
            conn.execute('ALTER TABLE entities ADD COLUMN mention_cnt INTEGER DEFAULT 0')

        # P3: mentions 加 confidence/pass_no
        mcols = {r['name'] for r in conn.execute('PRAGMA table_info(mentions)').fetchall()}
        if 'confidence' not in mcols:
            conn.execute('ALTER TABLE mentions ADD COLUMN confidence REAL DEFAULT 0.6')
        if 'pass_no' not in mcols:
            conn.execute('ALTER TABLE mentions ADD COLUMN pass_no INTEGER DEFAULT 1')

# ─── Chapter DAO ───

def upsert_chapter(book_id, chapter_id, idx=None, title=None, content_hash=None, summary=None, status=None, error=None, tokens_used=None):
    now = int(time.time())
    with db_transaction(book_id) as conn:
        existing = conn.execute('SELECT * FROM chapters WHERE id=? AND book_id=?', (chapter_id, book_id)).fetchone()
        if existing:
            fields = ['updated_at=?']
            vals = [now]
            if idx is not None: fields.append('idx=?'); vals.append(idx)
            if title is not None: fields.append('title=?'); vals.append(title)
            if content_hash is not None: fields.append('content_hash=?'); vals.append(content_hash)
            if summary is not None: fields.append('summary=?'); vals.append(summary)
            if status is not None: fields.append('status=?'); vals.append(status)
            if error is not None: fields.append('error=?'); vals.append(error)
            if tokens_used is not None: fields.append('tokens_used=?'); vals.append(tokens_used)
            vals.append(chapter_id)
            vals.append(book_id)
            conn.execute(f'UPDATE chapters SET {",".join(fields)} WHERE id=? AND book_id=?', vals)
        else:
            conn.execute('''INSERT INTO chapters
                (id, book_id, idx, title, content_hash, summary, status, error, tokens_used, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (chapter_id, book_id, idx or 0, title or '', content_hash, summary or '',
                 status or 'pending', error, tokens_used or 0, now))

def get_chapter(book_id, chapter_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT * FROM chapters WHERE id=? AND book_id=?', (chapter_id, book_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def list_chapters_db(book_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('SELECT * FROM chapters WHERE book_id=? ORDER BY idx', (book_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def delete_chapter_artifacts(book_id, chapter_id):
    with db_transaction(book_id) as conn:
        ev_ids = [r['id'] for r in conn.execute(
            'SELECT id FROM events WHERE book_id=? AND chapter_id=?',
            (book_id, chapter_id)
        ).fetchall()]
        if ev_ids:
            ph = ','.join('?' * len(ev_ids))
            conn.execute(f'DELETE FROM timeline_event_meta WHERE event_id IN ({ph})', ev_ids)
            conn.execute(
                f'DELETE FROM timeline_relations WHERE source_event_id IN ({ph}) OR target_event_id IN ({ph})',
                ev_ids + ev_ids,
            )
        conn.execute('''DELETE FROM mentions
            WHERE chapter_id=? AND entity_id IN (SELECT id FROM entities WHERE book_id=?)''',
            (chapter_id, book_id))
        conn.execute('DELETE FROM events WHERE book_id=? AND chapter_id=?', (book_id, chapter_id))
        conn.execute('DELETE FROM foreshadowing WHERE book_id=? AND (hint_chapter_id=? OR resolved_chapter_id=?)',
            (book_id, chapter_id, chapter_id))
        conn.execute('DELETE FROM rule_mentions WHERE chapter_id=?', (chapter_id,))
        rule_ids = [r['id'] for r in conn.execute('SELECT id FROM rules WHERE book_id=? AND first_chapter_id=?', (book_id, chapter_id)).fetchall()]
        if rule_ids:
            ph = ','.join('?' * len(rule_ids))
            conn.execute(f'DELETE FROM rule_mentions WHERE rule_id IN ({ph})', rule_ids)
        conn.execute('DELETE FROM rules WHERE book_id=? AND first_chapter_id=?', (book_id, chapter_id))
        conn.execute('''DELETE FROM entities
            WHERE book_id=? AND first_chapter_id=? AND id NOT IN (SELECT DISTINCT entity_id FROM mentions)''',
            (book_id, chapter_id))

# ─── Entity DAO ───

def upsert_entity(book_id, canonical_name, type_, aliases=None, first_chapter_id=None):
    now = int(time.time())
    eid = str(uuid.uuid4())
    clean_aliases = []
    for a in aliases or []:
        a = str(a).strip()
        if a and a != canonical_name and a not in clean_aliases:
            clean_aliases.append(a)
    aliases_json = json.dumps(clean_aliases, ensure_ascii=False)
    with db_transaction(book_id) as conn:
        existing = conn.execute('SELECT * FROM entities WHERE book_id=? AND canonical_name=?', (book_id, canonical_name)).fetchone()
        if existing:
            if aliases:
                old_aliases = []
                try:
                    old_aliases = json.loads(existing['aliases'] or '[]')
                except Exception:
                    old_aliases = []
                merged = []
                for a in old_aliases + clean_aliases:
                    a = str(a).strip()
                    if a and a != canonical_name and a not in merged:
                        merged.append(a)
                conn.execute('UPDATE entities SET aliases=?, updated_at=? WHERE id=?',
                    (json.dumps(merged, ensure_ascii=False), now, existing['id']))
            if first_chapter_id and not existing['first_chapter_id']:
                conn.execute('UPDATE entities SET first_chapter_id=?, updated_at=? WHERE id=?', (first_chapter_id, now, existing['id']))
            return existing['id']
        conn.execute('''INSERT INTO entities (id, book_id, canonical_name, type, aliases, first_chapter_id, updated_at)
            VALUES (?,?,?,?,?,?,?)''', (eid, book_id, canonical_name, type_, aliases_json, first_chapter_id, now))
        return eid

def get_entity_by_name(book_id, name):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT * FROM entities WHERE book_id=? AND canonical_name=?', (book_id, name)).fetchone()
        if row: return dict(row)
        rows = conn.execute('SELECT * FROM entities WHERE book_id=?', (book_id,)).fetchall()
        for r in rows:
            aliases = json.loads(r['aliases'])
            if name in aliases:
                return dict(r)
        return None
    finally:
        conn.close()

def list_entities(book_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('SELECT * FROM entities WHERE book_id=? ORDER BY canonical_name', (book_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def match_entities_by_name(book_id, query):
    if not query: return []
    q = query.lower()
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('SELECT * FROM entities WHERE book_id=?', (book_id,)).fetchall()
        matched = []
        for r in rows:
            if r['canonical_name'].lower() in q:
                matched.append(dict(r))
                continue
            aliases = json.loads(r['aliases'])
            if any(a.lower() in q for a in aliases):
                matched.append(dict(r))
        return matched
    finally:
        conn.close()

def remaining_entities(book_id, exclude_ids):
    if not exclude_ids:
        return list_entities(book_id)
    conn = _get_conn(book_id)
    try:
        placeholders = ','.join('?' * len(exclude_ids))
        rows = conn.execute(f'SELECT * FROM entities WHERE book_id=? AND id NOT IN ({placeholders}) ORDER BY canonical_name',
            [book_id] + list(exclude_ids)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ─── P1: 实体归并 / 模糊匹配 ───

def normalize_name(s):
    """保守归一化：NFKC + strip + 拉丁小写 + 去装饰空白。不做分词，避免误并。"""
    import unicodedata
    s = unicodedata.normalize('NFKC', s)
    s = s.strip()
    # 去成对标点内的空白（如《 阿Q 》→《阿Q》）
    s = re.sub(r'[〈《（\[【]\s+', lambda m: m.group(0)[0], s)
    s = re.sub(r'\s+[〉》）\]】]', lambda m: m.group(0)[-1], s)
    # 拉丁字母小写
    s = ''.join(c.lower() if 'a' <= c <= 'z' or 'A' <= c <= 'Z' else c for c in s)
    # 压缩连续空白
    s = re.sub(r'\s+', ' ', s)
    return s

# type 相容规则：完全相同 type → 相容；一方为未知 → 相容；同义类映射
_TYPE_COMPAT = {
    '人物': ('人物', '未分类', '未知'),
    '地点': ('地点', '场所', '未分类', '未知'),
    '场所': ('地点', '场所', '未分类', '未知'),
    '势力': ('势力', '组织', '阵营', '未分类', '未知'),
    '组织': ('势力', '组织', '阵营', '未分类', '未知'),
    '阵营': ('势力', '组织', '阵营', '未分类', '未知'),
    '物品': ('物品', '道具', '未分类', '未知'),
    '道具': ('物品', '道具', '未分类', '未知'),
    '事件': ('事件', '未分类', '未知'),
    '规则': ('规则', '设定', '未分类', '未知'),
    '设定': ('规则', '设定', '未分类', '未知'),
}

def _types_compatible(t1, t2):
    if t1 == t2:
        return True
    t1n = t1 or '未分类'
    t2n = t2 or '未分类'
    if t1n in ('未分类', '未知') or t2n in ('未分类', '未知'):
        return True
    compat = _TYPE_COMPAT.get(t1n, (t1n, '未分类', '未知'))
    if t2n in compat:
        return True
    return False

def _insert_entity_aliases(conn, book_id, entity_id, canonical, aliases, source='ai'):
    """将 canonical + aliases 写入 entity_aliases 表。"""
    now = int(time.time())
    seen = set()
    names = [canonical] + (aliases or [])
    for raw in names:
        raw = str(raw).strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        norm = normalize_name(raw)
        eid = str(uuid.uuid4())
        conn.execute(
            'INSERT OR IGNORE INTO entity_aliases (id, book_id, entity_id, alias_norm, alias_raw, source, created_at) VALUES (?,?,?,?,?,?,?)',
            (eid, book_id, entity_id, norm, raw, source, now)
        )

def resolve_entity(book_id, canonical_name, type_, aliases=None, first_chapter_id=None):
    """替代 upsert_entity 的入口：按序解析实体（精确→别名→嵌入→新建）。"""
    now = int(time.time())
    eid = str(uuid.uuid4())
    clean_aliases = []
    for a in aliases or []:
        a = str(a).strip()
        if a and a != canonical_name and a not in clean_aliases:
            clean_aliases.append(a)
    name_norm = normalize_name(canonical_name)
    aliases_json = json.dumps(clean_aliases, ensure_ascii=False)

    with db_transaction(book_id) as conn:
        # 1) 精确匹配 canonical_name
        existing = conn.execute(
            'SELECT * FROM entities WHERE book_id=? AND canonical_name=?',
            (book_id, canonical_name)
        ).fetchone()

        if not existing:
            # 2) 别名反查：normalize_name(incoming_canonical) 或任一 incoming_alias
            norm_checks = [name_norm] + [normalize_name(a) for a in clean_aliases]
            for nc in norm_checks:
                row = conn.execute('''SELECT e.* FROM entities e
                    JOIN entity_aliases a ON a.entity_id=e.id
                    WHERE e.book_id=? AND a.alias_norm=?''', (book_id, nc)).fetchone()
                if row and _types_compatible(row['type'], type_):
                    existing = row
                    break

        if existing:
            # 更新现有实体
            eid = existing['id']
            if clean_aliases:
                old_aliases = []
                try:
                    old_aliases = json.loads(existing['aliases'] or '[]')
                except Exception:
                    old_aliases = []
                merged = []
                for a in old_aliases + clean_aliases:
                    a = str(a).strip()
                    if a and a != existing['canonical_name'] and a not in merged:
                        merged.append(a)
                conn.execute('UPDATE entities SET aliases=?, updated_at=? WHERE id=?',
                    (json.dumps(merged, ensure_ascii=False), now, eid))
            if first_chapter_id and not existing['first_chapter_id']:
                conn.execute('UPDATE entities SET first_chapter_id=?, updated_at=? WHERE id=?', (first_chapter_id, now, eid))
            # 更新 name_norm
            conn.execute('UPDATE entities SET name_norm=COALESCE(name_norm,?), updated_at=? WHERE id=?',
                (name_norm, now, eid))
            # 写入 alias 表
            _insert_entity_aliases(conn, book_id, eid, existing['canonical_name'], clean_aliases)
            return eid

        # 3) 嵌入近邻（可选，需 embedding 后端可用）
        # 不在事务内调嵌入，避免长事务；当前做精确+别名解析后直接新建
        # 嵌入匹配在后续段落通过 resolve_entity_with_embedding 调用

        # 4) 新建实体
        conn.execute('''INSERT INTO entities
            (id, book_id, canonical_name, type, aliases, first_chapter_id, updated_at, name_norm)
            VALUES (?,?,?,?,?,?,?,?)''',
            (eid, book_id, canonical_name, type_, aliases_json, first_chapter_id, now, name_norm))
        _insert_entity_aliases(conn, book_id, eid, canonical_name, clean_aliases)
        return eid

def resolve_entity_with_embedding(book_id, canonical_name, type_, aliases=None, first_chapter_id=None,
                                    embed_fn=None, tau_high=0.86, tau_low=0.78):
    """resolve_entity 的嵌入增强版：在精确/别名均不命中时，走向量检索。"""
    now = int(time.time())
    eid = str(uuid.uuid4())
    clean_aliases = []
    for a in aliases or []:
        a = str(a).strip()
        if a and a != canonical_name and a not in clean_aliases:
            clean_aliases.append(a)
    name_norm = normalize_name(canonical_name)
    aliases_json = json.dumps(clean_aliases, ensure_ascii=False)

    with db_transaction(book_id) as conn:
        # 1) 精确
        existing = conn.execute(
            'SELECT * FROM entities WHERE book_id=? AND canonical_name=?',
            (book_id, canonical_name)
        ).fetchone()
        if not existing:
            # 2) 别名
            norm_checks = [name_norm] + [normalize_name(a) for a in clean_aliases]
            for nc in norm_checks:
                row = conn.execute('''SELECT e.* FROM entities e
                    JOIN entity_aliases a ON a.entity_id=e.id
                    WHERE e.book_id=? AND a.alias_norm=?''', (book_id, nc)).fetchone()
                if row and _types_compatible(row['type'], type_):
                    existing = row
                    break

        if existing:
            eid = existing['id']
            if clean_aliases:
                old_aliases = []
                try:
                    old_aliases = json.loads(existing['aliases'] or '[]')
                except Exception:
                    old_aliases = []
                merged = []
                for a in old_aliases + clean_aliases:
                    a = str(a).strip()
                    if a and a != existing['canonical_name'] and a not in merged:
                        merged.append(a)
                conn.execute('UPDATE entities SET aliases=?, updated_at=? WHERE id=?',
                    (json.dumps(merged, ensure_ascii=False), now, eid))
            if first_chapter_id and not existing['first_chapter_id']:
                conn.execute('UPDATE entities SET first_chapter_id=?, updated_at=? WHERE id=?',
                    (first_chapter_id, now, eid))
            conn.execute('UPDATE entities SET name_norm=COALESCE(name_norm,?), updated_at=? WHERE id=?',
                (name_norm, now, eid))
            _insert_entity_aliases(conn, book_id, eid, existing['canonical_name'], clean_aliases)
            return eid

        # 3) 嵌入近邻
        if embed_fn:
            try:
                query_text = f'{canonical_name} {type_} {clean_aliases[0] if clean_aliases else ""}'
                candidates = _embed_entity_search(conn, book_id, query_text, embed_fn, tau_high)
                if candidates:
                    existing_row = candidates[0]
                    eid = existing_row['id']
                    # 合并
                    _merge_entities_internal(conn, book_id, eid, canonical_name, type_, clean_aliases,
                                              first_chapter_id, name_norm, now, reason='embedding',
                                              similarity=candidates[0].get('_sim', tau_high))
                    _insert_entity_aliases(conn, book_id, eid, canonical_name, clean_aliases)
                    return eid
            except Exception:
                pass  # 嵌入失败→回落新建

        # 4) 新建
        conn.execute('''INSERT INTO entities
            (id, book_id, canonical_name, type, aliases, first_chapter_id, updated_at, name_norm)
            VALUES (?,?,?,?,?,?,?,?)''',
            (eid, book_id, canonical_name, type_, aliases_json, first_chapter_id, now, name_norm))
        _insert_entity_aliases(conn, book_id, eid, canonical_name, clean_aliases)
        return eid

def _embed_entity_search(conn, book_id, query_text, embed_fn, threshold):
    """用嵌入检索 top-3 候选实体，返回 (entity_row, similarity) 列表。"""
    try:
        qv = embed_fn(query_text)
    except Exception:
        return []
    if qv is None or len(qv) == 0:
        return []
    rows = conn.execute('''SELECT e.*, v.dimensions, v.embedding
        FROM entities e
        JOIN vector_entries v ON v.id=e.id AND v.book_id=e.book_id
        WHERE e.book_id=? AND v.source_type='entity'
        ORDER BY v.updated_at DESC''', (book_id,)).fetchall()
    candidates = []
    for r in rows:
        try:
            stored = np.frombuffer(r['embedding'], dtype=np.float32)
            if len(stored) != r['dimensions']:
                stored = stored[:r['dimensions']]
            sim = float(np.dot(qv, stored) / (np.linalg.norm(qv) * np.linalg.norm(stored) + 1e-10))
        except Exception:
            sim = 0
        r_dict = dict(r)
        r_dict['_sim'] = sim
        candidates.append(r_dict)
    candidates.sort(key=lambda x: x['_sim'], reverse=True)
    return [c for c in candidates[:3] if c['_sim'] >= threshold]

def _merge_entities_internal(conn, book_id, into_id, canonical_name, type_, aliases,
                              first_chapter_id, name_norm, now, reason='alias', similarity=None):
    """将外来信息合并到已存在的 into_id 实体。不创建 entity_merges 记录（非全实体合并）。"""
    existing = conn.execute('SELECT * FROM entities WHERE id=? AND book_id=?', (into_id, book_id)).fetchone()
    if not existing:
        return
    # 合并别名
    old_aliases = []
    try:
        old_aliases = json.loads(existing['aliases'] or '[]')
    except Exception:
        old_aliases = []
    merged = []
    for a in old_aliases + aliases:
        a = str(a).strip()
        if a and a != existing['canonical_name'] and a not in merged:
            merged.append(a)
    conn.execute('UPDATE entities SET aliases=?, updated_at=? WHERE id=?',
        (json.dumps(merged, ensure_ascii=False), now, into_id))
    if first_chapter_id and not existing['first_chapter_id']:
        conn.execute('UPDATE entities SET first_chapter_id=?, updated_at=? WHERE id=?',
            (first_chapter_id, now, into_id))
    conn.execute('UPDATE entities SET name_norm=COALESCE(name_norm,?), updated_at=? WHERE id=?',
        (name_norm, now, into_id))

def merge_entities(book_id, into_id, from_id):
    """完整合并两个实体：mentions repoint、aliases 并集、mention_cnt 累加、写 entity_merges。"""
    now = int(time.time())
    with db_transaction(book_id) as conn:
        from_entity = conn.execute('SELECT * FROM entities WHERE id=? AND book_id=?', (from_id, book_id)).fetchone()
        into_entity = conn.execute('SELECT * FROM entities WHERE id=? AND book_id=?', (into_id, book_id)).fetchone()
        if not from_entity or not into_entity:
            return
        # mentions repoint
        conn.execute('UPDATE mentions SET entity_id=? WHERE entity_id=? AND entity_id IN (SELECT id FROM entities WHERE book_id=?)',
            (into_id, from_id, book_id))
        # aliases 并集
        from_aliases = json.loads(from_entity['aliases'] or '[]')
        into_aliases = json.loads(into_entity['aliases'] or '[]')
        merged = list(into_aliases)
        for a in from_aliases:
            a = str(a).strip()
            if a and a != into_entity['canonical_name'] and a not in merged:
                merged.append(a)
        # 把 from_name 也加入别名
        if from_entity['canonical_name'] != into_entity['canonical_name']:
            if from_entity['canonical_name'] not in merged:
                merged.append(from_entity['canonical_name'])
        conn.execute('UPDATE entities SET aliases=?, mention_cnt=COALESCE(mention_cnt,0)+(SELECT COUNT(*) FROM mentions WHERE entity_id=?), updated_at=? WHERE id=?',
            (json.dumps(merged, ensure_ascii=False), from_id, now, into_id))
        # first_chapter_id 取更早
        if from_entity['first_chapter_id'] and not into_entity['first_chapter_id']:
            conn.execute('UPDATE entities SET first_chapter_id=?, updated_at=? WHERE id=?',
                (from_entity['first_chapter_id'], now, into_id))
        # 写入 entity_merges
        merge_id = str(uuid.uuid4())
        conn.execute('INSERT INTO entity_merges (id, book_id, from_name, into_id, reason, created_at) VALUES (?,?,?,?,?,?)',
            (merge_id, book_id, from_entity['canonical_name'], into_id, 'manual', now))
        # 从 from_entity 迁移 alias 记录
        conn.execute('UPDATE entity_aliases SET entity_id=? WHERE entity_id=? AND book_id=?',
            (into_id, from_id, book_id))
        # 删除被并实体
        conn.execute('DELETE FROM entities WHERE id=? AND book_id=?', (from_id, book_id))

def backfill_entity_mentions(book_id):
    """迁移脚本：回填 name_norm、拆 entity_aliases、算 mention_cnt。幂等。"""
    init_db(book_id)
    with db_transaction(book_id) as conn:
        rows = conn.execute('SELECT * FROM entities WHERE book_id=?', (book_id,)).fetchall()
        for r in rows:
            eid = r['id']
            # name_norm
            if not r['name_norm']:
                nn = normalize_name(r['canonical_name'])
                conn.execute('UPDATE entities SET name_norm=? WHERE id=?', (nn, eid))
            # 拆别名到 entity_aliases
            aliases = []
            try:
                aliases = json.loads(r['aliases'] or '[]')
            except Exception:
                aliases = []
            _insert_entity_aliases(conn, book_id, eid, r['canonical_name'], aliases, source='migration')
            # mention_cnt
            cnt = conn.execute('SELECT COUNT(*) as c FROM mentions WHERE entity_id=?', (eid,)).fetchone()['c']
            conn.execute('UPDATE entities SET mention_cnt=? WHERE id=?', (cnt, eid))

def _ensure_str(val):
    """Sanitize AI output: convert list→comma-joined string, None→'', else str()."""
    if val is None:
        return ''
    if isinstance(val, list):
        return ', '.join(str(v) for v in val if v)
    if isinstance(val, dict):
        return str(val)
    return str(val)

# ─── Mention DAO ───

def add_mention(book_id, entity_id, chapter_id, fact, snippet=None, confidence=0.6, pass_no=1):
    mid = str(uuid.uuid4())
    now = int(time.time())
    fact = _ensure_str(fact) if fact else ''
    snippet = _ensure_str(snippet) if snippet else None
    with db_transaction(book_id) as conn:
        conn.execute('INSERT INTO mentions (id, entity_id, chapter_id, fact, snippet, confidence, pass_no, created_at) VALUES (?,?,?,?,?,?,?,?)',
            (mid, entity_id, chapter_id, fact, snippet, confidence, pass_no, now))
        conn.execute('UPDATE entities SET mention_cnt=COALESCE(mention_cnt,0)+1 WHERE id=?', (entity_id,))
    return mid

def get_mentions_for_entity(book_id, entity_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT m.*, c.title as chapter_title, c.idx as chapter_idx
            FROM mentions m LEFT JOIN chapters c ON m.chapter_id=c.id
            WHERE m.entity_id=? ORDER BY c.idx''', (entity_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_mentions_by_chapter(book_id, chapter_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT m.*, e.canonical_name, e.type, e.aliases, e.first_chapter_id
            FROM mentions m JOIN entities e ON m.entity_id=e.id
            WHERE e.book_id=? AND m.chapter_id=?''', (book_id, chapter_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_mentions_for_chapter(book_id, chapter_id):
    """Alias for get_mentions_by_chapter."""
    return get_mentions_by_chapter(book_id, chapter_id)


def add_fact_revision(book_id, entity_id, chapter_id, fact, snippet=None,
                       confidence=0.6, pass_no=1, status='active', supersedes=None, reason=None):
    """写入 fact_revisions 表。"""
    now = int(time.time())
    rev_id = str(uuid.uuid4())
    fact = _ensure_str(fact) if fact else ''
    snippet = _ensure_str(snippet) if snippet else None
    reason = _ensure_str(reason) if reason else None
    with db_transaction(book_id) as conn:
        conn.execute('''INSERT INTO fact_revisions
            (id, book_id, entity_id, chapter_id, fact, snippet, confidence, pass_no, status, supersedes, reason, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (rev_id, book_id, entity_id, chapter_id, fact, snippet, confidence, pass_no, status, supersedes, reason, now))
    return rev_id


def get_entity_recent_mentions_before(book_id, entity_id, before_idx=None, limit=3):
    conn = _get_conn(book_id)
    try:
        query = '''SELECT m.*, c.title as chapter_title, c.idx as chapter_idx
            FROM mentions m LEFT JOIN chapters c ON m.chapter_id=c.id
            WHERE m.entity_id=?'''
        params = [entity_id]
        if before_idx is not None:
            query += ' AND c.idx < ?'
            params.append(before_idx)
        query += ' ORDER BY c.idx DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ─── Event DAO ───

def add_event(book_id, chapter_id, story_time, who, what, where_loc, why, consequence):
    eid = str(uuid.uuid4())
    now = int(time.time())
    who = _ensure_str(who) if who else ''
    what = _ensure_str(what) if what else ''
    where_loc = _ensure_str(where_loc) if where_loc else ''
    why = _ensure_str(why) if why else ''
    consequence = _ensure_str(consequence) if consequence else ''
    with db_transaction(book_id) as conn:
        conn.execute('''INSERT INTO events (id, book_id, chapter_id, story_time, who, what, where_loc, why, consequence, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (eid, book_id, chapter_id, story_time, who, what, where_loc, why, consequence, now))
    return eid

def list_events(book_id, limit=None, filter_entities=None):
    conn = _get_conn(book_id)
    try:
        query = 'SELECT e.*, c.title as chapter_title, c.idx as chapter_idx FROM events e LEFT JOIN chapters c ON e.chapter_id=c.id AND c.book_id=e.book_id WHERE e.book_id=?'
        params = [book_id]
        if filter_entities:
            like_clauses = []
            for ent in filter_entities:
                like_clauses.append('e.who LIKE ?')
                params.append(f'%{ent}%')
            query += ' AND (' + ' OR '.join(like_clauses) + ')'
        query += ' ORDER BY c.idx'
        if limit: query += f' LIMIT {limit}'
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_events_by_chapter(book_id, chapter_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT e.*, c.title as chapter_title, c.idx as chapter_idx,
                   tm.story_order, tm.segment_id, tm.segment_title, tm.lane, tm.importance,
                   tm.zoom_level, tm.confidence, tm.status as timeline_status,
                   tm.reason as timeline_reason, tm.evidence as timeline_evidence
            FROM events e
            LEFT JOIN chapters c ON e.chapter_id=c.id AND c.book_id=e.book_id
            LEFT JOIN timeline_event_meta tm ON tm.event_id=e.id AND tm.book_id=e.book_id
            WHERE e.book_id=? AND e.chapter_id=?
            ORDER BY COALESCE(tm.story_order, c.idx * 1000), e.created_at''',
            (book_id, chapter_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ─── Foreshadowing DAO ───

def add_foreshadowing(book_id, hint_chapter_id, hint, status='open', resolved_chapter_id=None, resolution=None):
    fid = str(uuid.uuid4())
    now = int(time.time())
    hint = _ensure_str(hint) if hint else ''
    resolution = _ensure_str(resolution) if resolution else None
    with db_transaction(book_id) as conn:
        conn.execute('''INSERT INTO foreshadowing (id, book_id, hint_chapter_id, hint, status, resolved_chapter_id, resolution, updated_at)
            VALUES (?,?,?,?,?,?,?,?)''', (fid, book_id, hint_chapter_id, hint, status, resolved_chapter_id, resolution, now))
    return fid

def resolve_foreshadowing(book_id, hint, resolved_chapter_id, resolution):
    now = int(time.time())
    hint = _ensure_str(hint) if hint else ''
    resolution = _ensure_str(resolution) if resolution else ''
    with db_transaction(book_id) as conn:
        conn.execute('''UPDATE foreshadowing SET status='resolved', resolved_chapter_id=?, resolution=?, updated_at=?
            WHERE book_id=? AND hint=? AND status='open' ''', (resolved_chapter_id, resolution, now, book_id, hint))

def list_foreshadowing(book_id, status=None):
    conn = _get_conn(book_id)
    try:
        query = 'SELECT f.*, c.title as chapter_title, c.idx as chapter_idx FROM foreshadowing f LEFT JOIN chapters c ON f.hint_chapter_id=c.id AND c.book_id=f.book_id WHERE f.book_id=?'
        params = [book_id]
        if status:
            query += ' AND f.status=?'
            params.append(status)
        query += ' ORDER BY c.idx'
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ─── Rules DAO ───

def upsert_rule(book_id, name, body, first_chapter_id=None):
    now = int(time.time())
    name = _ensure_str(name) if name else ''
    body = _ensure_str(body) if body else ''
    with db_transaction(book_id) as conn:
        existing = conn.execute('SELECT * FROM rules WHERE book_id=? AND name=?', (book_id, name)).fetchone()
        if existing:
            conn.execute('UPDATE rules SET body=?, updated_at=? WHERE id=?', (body, now, existing['id']))
            return existing['id']
        rid = str(uuid.uuid4())
        conn.execute('INSERT INTO rules (id, book_id, name, body, first_chapter_id, updated_at) VALUES (?,?,?,?,?,?)',
            (rid, book_id, name, body, first_chapter_id, now))
        return rid

def add_rule_mention(book_id, rule_id, chapter_id, evidence=None):
    mid = str(uuid.uuid4())
    now = int(time.time())
    evidence = _ensure_str(evidence) if evidence else None
    with db_transaction(book_id) as conn:
        if not conn.execute('SELECT 1 FROM rules WHERE id=? AND book_id=?', (rule_id, book_id)).fetchone():
            return None
        dup = conn.execute('SELECT id FROM rule_mentions WHERE rule_id=? AND chapter_id=?', (rule_id, chapter_id)).fetchone()
        if dup:
            if evidence:
                conn.execute('UPDATE rule_mentions SET evidence=? WHERE id=?', (evidence, dup['id']))
            return dup['id']
        conn.execute('INSERT INTO rule_mentions (id, rule_id, chapter_id, evidence, created_at) VALUES (?,?,?,?,?)',
            (mid, rule_id, chapter_id, evidence, now))
    return mid

def get_rule_mentions_by_chapter(book_id, chapter_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT rm.*, r.name, r.body, r.first_chapter_id,
                   c.idx as first_chapter_idx, c.title as first_chapter_title
            FROM rule_mentions rm
            JOIN rules r ON rm.rule_id=r.id
            LEFT JOIN chapters c ON r.first_chapter_id=c.id AND c.book_id=r.book_id
            WHERE r.book_id=? AND rm.chapter_id=?
            ORDER BY r.name''', (book_id, chapter_id)).fetchall()
        result = [dict(r) for r in rows]
        seen = {r.get('rule_id') for r in result}
        legacy_rows = conn.execute('''SELECT r.id as rule_id, r.name, r.body, r.first_chapter_id,
                   c.idx as first_chapter_idx, c.title as first_chapter_title
            FROM rules r
            LEFT JOIN chapters c ON r.first_chapter_id=c.id AND c.book_id=r.book_id
            WHERE r.book_id=? AND r.first_chapter_id=?
            ORDER BY r.name''', (book_id, chapter_id)).fetchall()
        for r in legacy_rows:
            d = dict(r)
            if d.get('rule_id') in seen:
                continue
            d.update({'id': None, 'chapter_id': chapter_id, 'evidence': ''})
            result.append(d)
        return result
    finally:
        conn.close()

def list_rules(book_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('SELECT * FROM rules WHERE book_id=? ORDER BY name', (book_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ─── Timeline Meta DAO ───

def upsert_timeline_event_meta(book_id, event_id, story_order=None, segment_id=None, segment_title=None,
                               lane=None, importance=None, zoom_level=None, confidence=None,
                               status='ai', reason=None, evidence=None):
    now = int(time.time())
    with db_transaction(book_id) as conn:
        if not conn.execute('SELECT 1 FROM events WHERE id=? AND book_id=?', (event_id, book_id)).fetchone():
            return False
        existing = conn.execute('SELECT * FROM timeline_event_meta WHERE event_id=? AND book_id=?', (event_id, book_id)).fetchone()
        if existing:
            fields = ['updated_at=?']
            vals = [now]
            for k, v in (
                ('story_order', story_order), ('segment_id', segment_id), ('segment_title', segment_title),
                ('lane', lane), ('importance', importance), ('zoom_level', zoom_level),
                ('confidence', confidence), ('status', status), ('reason', reason), ('evidence', evidence),
            ):
                if v is not None:
                    fields.append(f'{k}=?')
                    vals.append(v)
            vals.extend([event_id, book_id])
            conn.execute(f'UPDATE timeline_event_meta SET {",".join(fields)} WHERE event_id=? AND book_id=?', vals)
        else:
            conn.execute('''INSERT INTO timeline_event_meta
                (event_id, book_id, story_order, segment_id, segment_title, lane, importance, zoom_level,
                 confidence, status, reason, evidence, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (event_id, book_id, story_order, segment_id, segment_title, lane if lane is not None else 0,
                 importance if importance is not None else 2, zoom_level if zoom_level is not None else 2,
                 confidence if confidence is not None else 0.5, status or 'ai', reason, evidence, now))
    return True

def add_timeline_relation(book_id, source_event_id, target_event_id=None, relation='before',
                          confidence=0.5, status='ai', evidence=None, note=None):
    rid = str(uuid.uuid4())
    now = int(time.time())
    with db_transaction(book_id) as conn:
        conn.execute('''INSERT INTO timeline_relations
            (id, book_id, source_event_id, target_event_id, relation, confidence, status, evidence, note, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (rid, book_id, source_event_id, target_event_id, relation, confidence, status, evidence, note, now))
    return rid

def clear_ai_timeline_relations(book_id):
    with db_transaction(book_id) as conn:
        conn.execute("DELETE FROM timeline_relations WHERE book_id=? AND status='ai'", (book_id,))

def list_timeline_events(book_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT e.*, c.title as chapter_title, c.idx as chapter_idx,
                   tm.story_order, tm.segment_id, tm.segment_title, tm.lane, tm.importance,
                   tm.zoom_level, tm.confidence, tm.status as timeline_status,
                   tm.reason as timeline_reason, tm.evidence as timeline_evidence
            FROM events e
            LEFT JOIN chapters c ON e.chapter_id=c.id AND c.book_id=e.book_id
            LEFT JOIN timeline_event_meta tm ON tm.event_id=e.id AND tm.book_id=e.book_id
            WHERE e.book_id=?
            ORDER BY COALESCE(tm.story_order, c.idx * 1000), COALESCE(tm.lane, 0), c.idx, e.created_at''',
            (book_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def list_timeline_relations(book_id, status=None):
    conn = _get_conn(book_id)
    try:
        query = 'SELECT * FROM timeline_relations WHERE book_id=?'
        params = [book_id]
        if status:
            query += ' AND status=?'
            params.append(status)
        query += ' ORDER BY created_at'
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def save_consistency_alerts(book_id, chapter_id, alerts, source_hash=None):
    now = int(time.time())
    saved = []
    with db_transaction(book_id) as conn:
        for a in alerts or []:
            msg = str(a.get('message') or '').strip()
            if not msg:
                continue
            aid = str(uuid.uuid4())
            evidence = _ensure_str(a.get('evidence')) if a.get('evidence') else None
            suggestion = _ensure_str(a.get('suggestion')) if a.get('suggestion') else None
            conn.execute('''INSERT INTO consistency_alerts
                (id, book_id, chapter_id, kind, severity, message, evidence, suggestion, status, source_hash, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (aid, book_id, chapter_id, str(a.get('kind') or 'possible_conflict')[:50],
                 str(a.get('severity') or 'medium')[:20], msg, evidence, suggestion,
                 'open', source_hash, now, now))
            item = dict(a)
            item['id'] = aid
            item['status'] = 'open'
            saved.append(item)
    return saved

def list_consistency_alerts(book_id, chapter_id=None, status='open', limit=20):
    conn = _get_conn(book_id)
    try:
        query = 'SELECT * FROM consistency_alerts WHERE book_id=?'
        params = [book_id]
        if chapter_id:
            query += ' AND chapter_id=?'
            params.append(chapter_id)
        if status:
            query += ' AND status=?'
            params.append(status)
        query += ' ORDER BY updated_at DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def update_consistency_alert_status(book_id, alert_id, status):
    now = int(time.time())
    with db_transaction(book_id) as conn:
        conn.execute('UPDATE consistency_alerts SET status=?, updated_at=? WHERE id=? AND book_id=?',
            (status, now, alert_id, book_id))

def delete_kb_records(book_id, delete_map):
    delete_map = delete_map or {}
    counts = {'mentions': 0, 'events': 0, 'foreshadowing': 0, 'rules': 0}
    with db_transaction(book_id) as conn:
        mention_ids = [str(x) for x in delete_map.get('mentions', []) or [] if x]
        if mention_ids:
            ph = ','.join('?' * len(mention_ids))
            cur = conn.execute(f'''DELETE FROM mentions
                WHERE id IN ({ph}) AND entity_id IN (SELECT id FROM entities WHERE book_id=?)''',
                mention_ids + [book_id])
            counts['mentions'] = cur.rowcount if cur.rowcount is not None else 0

        event_ids = [str(x) for x in delete_map.get('events', []) or [] if x]
        if event_ids:
            ph = ','.join('?' * len(event_ids))
            conn.execute(f'DELETE FROM timeline_event_meta WHERE event_id IN ({ph})', event_ids)
            conn.execute(
                f'DELETE FROM timeline_relations WHERE source_event_id IN ({ph}) OR target_event_id IN ({ph})',
                event_ids + event_ids,
            )
            cur = conn.execute(f'DELETE FROM events WHERE book_id=? AND id IN ({ph})', [book_id] + event_ids)
            counts['events'] = cur.rowcount if cur.rowcount is not None else 0

        fs_ids = [str(x) for x in delete_map.get('foreshadowing', []) or [] if x]
        if fs_ids:
            ph = ','.join('?' * len(fs_ids))
            cur = conn.execute(f'DELETE FROM foreshadowing WHERE book_id=? AND id IN ({ph})', [book_id] + fs_ids)
            counts['foreshadowing'] = cur.rowcount if cur.rowcount is not None else 0

        rule_ids = [str(x) for x in delete_map.get('rules', []) or [] if x]
        if rule_ids:
            ph = ','.join('?' * len(rule_ids))
            conn.execute(f'DELETE FROM rule_mentions WHERE rule_id IN ({ph})', rule_ids)
            cur = conn.execute(f'DELETE FROM rules WHERE book_id=? AND id IN ({ph})', [book_id] + rule_ids)
            counts['rules'] = cur.rowcount if cur.rowcount is not None else 0

        conn.execute('''DELETE FROM entities
            WHERE book_id=? AND id NOT IN (SELECT DISTINCT entity_id FROM mentions)''', (book_id,))
    return counts

# ─── RT State DAO ───

def set_rt_state(book_id, **kw):
    now = int(time.time())
    kw['updated_at'] = now
    with db_transaction(book_id) as conn:
        existing = conn.execute('SELECT * FROM rt_state WHERE book_id=?', (book_id,)).fetchone()
        if existing:
            fields = []
            vals = []
            for k, v in kw.items():
                if k in ('status', 'current_idx', 'total', 'phase', 'error', 'pause_requested', 'stream_buffer', 'active_start_idx', 'active_end_idx', 'updated_at'):
                    fields.append(f'{k}=?')
                    vals.append(v)
            if fields:
                vals.append(book_id)
                conn.execute(f'UPDATE rt_state SET {",".join(fields)} WHERE book_id=?', vals)
        else:
            conn.execute('''INSERT INTO rt_state
                (book_id, status, current_idx, total, phase, error, pause_requested, stream_buffer, active_start_idx, active_end_idx, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (book_id, kw.get('status', 'idle'), kw.get('current_idx', -1), kw.get('total', 0),
                 kw.get('phase', ''), kw.get('error'), kw.get('pause_requested', 0), kw.get('stream_buffer', ''),
                 kw.get('active_start_idx', -1), kw.get('active_end_idx', -1), now))

def get_rt_state(book_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT * FROM rt_state WHERE book_id=?', (book_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_pause_requested(book_id):
    st = get_rt_state(book_id)
    return bool(st and st['pause_requested'])

def set_pause_requested(book_id, val):
    set_rt_state(book_id, pause_requested=1 if val else 0)

def append_stream(book_id, token):
    with db_transaction(book_id) as conn:
        conn.execute('UPDATE rt_state SET stream_buffer=stream_buffer || ? WHERE book_id=?', (token, book_id))

# ─── RT Logs ───

def rt_log(book_id, msg):
    from datetime import datetime
    now = datetime.now()
    ts = now.strftime('%H:%M:%S')
    with db_transaction(book_id) as conn:
        conn.execute('INSERT INTO rt_logs (book_id, ts, msg) VALUES (?,?,?)', (book_id, ts, msg))
        conn.execute('DELETE FROM rt_logs WHERE id NOT IN (SELECT id FROM rt_logs WHERE book_id=? ORDER BY id DESC LIMIT 500)', (book_id,))
    try:
        from main import get_book_dir
        log_path = os.path.join(get_book_dir(book_id), 'readthrough.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f'{now.strftime("%Y-%m-%d %H:%M:%S")}  {msg}\n')
    except Exception:
        pass

def get_rt_logs(book_id, limit=50):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('SELECT ts, msg FROM rt_logs WHERE book_id=? ORDER BY id DESC LIMIT ?', (book_id, limit)).fetchall()
        return [{'time': r['ts'], 'msg': r['msg']} for r in reversed(rows)]
    finally:
        conn.close()

# ─── Embedding Chunks ───

def register_embedding_chunk(book_id, chunk_id, source_type, source_id, content_hash, backend_id):
    now = int(time.time())
    with db_transaction(book_id) as conn:
        conn.execute('''INSERT OR REPLACE INTO embedding_chunks
            (id, book_id, source_type, source_id, content_hash, backend_id, embedded_at)
            VALUES (?,?,?,?,?,?,?)''',
            (chunk_id, book_id, source_type, source_id, content_hash, backend_id, now))

def get_embedding_chunk(book_id, chunk_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT * FROM embedding_chunks WHERE book_id=? AND id=?', (book_id, chunk_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_embedding_backend_id(book_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT backend_id FROM embedding_chunks WHERE book_id=? LIMIT 1', (book_id,)).fetchone()
        return row['backend_id'] if row else None
    finally:
        conn.close()

def clear_embedding_chunks(book_id):
    with db_transaction(book_id) as conn:
        conn.execute('DELETE FROM embedding_chunks WHERE book_id=?', (book_id,))

def reset_book_kb(book_id):
    init_db(book_id)
    with db_transaction(book_id) as conn:
        for table in (
            'consistency_alerts', 'timeline_relations', 'timeline_event_meta',
            'rule_mentions',
            'mentions', 'events', 'foreshadowing', 'rules',
            'entities', 'chapters', 'rt_logs', 'rt_state',
            'embedding_chunks', 'vector_entries',
        ):
            if table == 'mentions':
                conn.execute('''DELETE FROM mentions WHERE entity_id IN
                            (SELECT id FROM entities WHERE book_id=?)''', (book_id,))
            elif table == 'rule_mentions':
                conn.execute('''DELETE FROM rule_mentions WHERE rule_id IN
                            (SELECT id FROM rules WHERE book_id=?)''', (book_id,))
            else:
                conn.execute(f'DELETE FROM {table} WHERE book_id=?', (book_id,))

def count_embedding_chunks(book_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT COUNT(*) as cnt FROM embedding_chunks WHERE book_id=?', (book_id,)).fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()

# ─── SQLite Vector Store ───

def _metadata_matches(metadata, where):
    if not where:
        return True
    if not isinstance(where, dict):
        return False
    if '$and' in where:
        values = where.get('$and')
        return isinstance(values, list) and all(_metadata_matches(metadata, item) for item in values)
    if '$or' in where:
        values = where.get('$or')
        return isinstance(values, list) and any(_metadata_matches(metadata, item) for item in values)
    for key, expected in where.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            if '$eq' in expected and actual != expected['$eq']:
                return False
            if '$ne' in expected and actual == expected['$ne']:
                return False
            if '$in' in expected and actual not in expected['$in']:
                return False
            if '$nin' in expected and actual in expected['$nin']:
                return False
        elif actual != expected:
            return False
    return True

def embed_upsert_many(book_id, ids, documents, embeddings, metadatas=None):
    if not ids:
        return
    if len(ids) != len(documents) or len(ids) != len(embeddings):
        raise ValueError('向量写入参数长度不一致')
    metadatas = metadatas or [{} for _ in ids]
    if len(metadatas) != len(ids):
        raise ValueError('向量元数据长度不一致')
    init_db(book_id)
    now = int(time.time())
    rows = []
    for chunk_id, document, embedding, metadata in zip(ids, documents, embeddings, metadatas):
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if not vector.size or not np.all(np.isfinite(vector)):
            raise ValueError(f'无效向量: {chunk_id}')
        rows.append((
            chunk_id,
            book_id,
            sqlite3.Binary(vector.tobytes()),
            int(vector.size),
            document or '',
            json.dumps(metadata or {}, ensure_ascii=False, separators=(',', ':')),
            now,
        ))
    with db_transaction(book_id) as conn:
        conn.executemany('''INSERT OR REPLACE INTO vector_entries
            (id, book_id, embedding, dimensions, document, metadata_json, updated_at)
            VALUES (?,?,?,?,?,?,?)''', rows)

def embed_upsert(book_id, chunk_id, text, embedding_backend, source_type=None, source_id=None):
    vec = embedding_backend.embed([text])[0]
    metadata = {}
    if source_type: metadata['source_type'] = source_type
    if source_id: metadata['source_id'] = source_id
    embed_upsert_many(book_id, [chunk_id], [text], [vec], [metadata])
    content_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    register_embedding_chunk(book_id, chunk_id, source_type or '', source_id or '', content_hash, embedding_backend.backend_id)

def embed_query(book_id, query_text, embedding_backend, top_k=10, where=None):
    init_db(book_id)
    query = np.asarray(embedding_backend.embed([query_text])[0], dtype=np.float32).reshape(-1)
    query_norm = float(np.linalg.norm(query))
    if not query.size or not np.isfinite(query_norm) or query_norm == 0:
        return []
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT id, embedding, dimensions, metadata_json
            FROM vector_entries WHERE book_id=?''', (book_id,)).fetchall()
    finally:
        conn.close()
    hits = []
    for row in rows:
        if row['dimensions'] != query.size:
            continue
        try:
            metadata = json.loads(row['metadata_json'] or '{}')
        except (TypeError, ValueError):
            metadata = {}
        if not _metadata_matches(metadata, where):
            continue
        vector = np.frombuffer(row['embedding'], dtype=np.float32, count=row['dimensions'])
        vector_norm = float(np.linalg.norm(vector))
        if not np.isfinite(vector_norm) or vector_norm == 0:
            continue
        similarity = float(np.dot(query, vector) / (query_norm * vector_norm))
        hits.append({
            'id': row['id'],
            'distance': 1.0 - max(-1.0, min(1.0, similarity)),
            'metadata': metadata,
        })
    hits.sort(key=lambda item: item['distance'])
    return hits[:max(0, int(top_k))]

def prune_vector_entries(book_id, expected_ids):
    init_db(book_id)
    expected = set(expected_ids or [])
    with db_transaction(book_id) as conn:
        stored = {
            row['id']
            for row in conn.execute(
                'SELECT id FROM vector_entries WHERE book_id=?', (book_id,)
            ).fetchall()
        }
        stale = [(book_id, chunk_id) for chunk_id in stored - expected]
        if stale:
            conn.executemany(
                'DELETE FROM vector_entries WHERE book_id=? AND id=?', stale
            )
            conn.executemany(
                'DELETE FROM embedding_chunks WHERE book_id=? AND id=?', stale
            )

# ─── P5: ANN 近似近邻检索 ───

_ANN_INDICES = {}  # book_id -> _ANNIndex instance
_ANN_LOCK = threading.Lock()

class _ANNIndex:
    """hnswlib 索引封装。缺库时回落暴力搜索。"""
    def __init__(self, book_id, dim):
        self.book_id = book_id
        self.dim = dim
        self.index = None
        self.ids = []  # 原始 id 列表，与 index 位置对应
        self._hnsw = None

    def build(self, vectors, ids):
        """用全量向量构建索引。vectors: list of np.array, ids: list of str"""
        try:
            import hnswlib
            self._hnsw = hnswlib
            num = len(vectors)
            if num == 0:
                return
            self.index = hnswlib.Index(space='cosine', dim=self.dim)
            self.index.init_index(max_elements=max(num, 100), ef_construction=200, M=16)
            data = np.vstack(vectors).astype(np.float32)
            self.index.add_items(data, np.arange(num))
            self.index.set_ef(50)
            self.ids = list(ids)
        except ImportError:
            self._hnsw = None
            self.index = None
            self.ids = []

    def query(self, query_vec, top_k, where_filter=None):
        """查询 top_k 近邻。返回 [(id, distance)]，支持 where 后过滤。"""
        if self.index is not None and self._hnsw is not None and where_filter is None:
            q = np.asarray(query_vec, dtype=np.float32).reshape(1, -1)
            labels, distances = self.index.knn_query(q, k=min(top_k * 2, len(self.ids)))
            results = []
            for idx, dist in zip(labels[0], distances[0]):
                if idx < len(self.ids):
                    results.append((self.ids[idx], float(dist)))
            return results[:top_k]
        return None  # 回落暴力

    def clear(self):
        self.index = None
        self.ids = []
        self._hnsw = None


def get_ann_index(book_id, force_rebuild=False):
    """获取或重建 ANN 索引。"""
    with _ANN_LOCK:
        if force_rebuild and book_id in _ANN_INDICES:
            _ANN_INDICES[book_id].clear()
            del _ANN_INDICES[book_id]
        if book_id in _ANN_INDICES:
            return _ANN_INDICES[book_id]
    # 检查 hnswlib 是否可用
    try:
        import hnswlib
    except ImportError:
        return None
    # 加载向量数据
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT id, embedding, dimensions, metadata_json
            FROM vector_entries WHERE book_id=? ORDER BY id''', (book_id,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    dim = rows[0]['dimensions']
    vectors = []
    ids = []
    for r in rows:
        vec = np.frombuffer(r['embedding'], dtype=np.float32, count=r['dimensions'])
        if len(vec) != dim:
            continue
        vectors.append(vec)
        ids.append(r['id'])
    if not vectors:
        return None
    idx = _ANNIndex(book_id, dim)
    idx.build(vectors, ids)
    with _ANN_LOCK:
        _ANN_INDICES[book_id] = idx
    return idx


def rebuild_ann_index(book_id):
    """显式重建 ANN 索引（嵌入变更后调用）。"""
    get_ann_index(book_id, force_rebuild=True)


def embed_query_ann(book_id, query_text, embedding_backend, top_k=10, where=None):
    """embed_query 的 ANN 加速版。无 ANN 或 where 过滤时回落暴力。"""
    query = np.asarray(embedding_backend.embed([query_text])[0], dtype=np.float32).reshape(-1)
    query_norm = float(np.linalg.norm(query))
    if not query.size or not np.isfinite(query_norm) or query_norm == 0:
        return []

    if where is not None:
        # 有过滤条件 → 直接回落暴力
        return embed_query(book_id, query_text, embedding_backend, top_k=top_k, where=where)

    idx = get_ann_index(book_id)
    if idx is not None:
        ann_results = idx.query(query, top_k)
        if ann_results is not None:
            conn = _get_conn(book_id)
            try:
                id_list = [r[0] for r in ann_results]
                if not id_list:
                    return []
                ph = ','.join('?' * len(id_list))
                rows = conn.execute(
                    f'SELECT id, metadata_json FROM vector_entries WHERE book_id=? AND id IN ({ph})',
                    [book_id] + id_list
                ).fetchall()
                row_map = {r['id']: r for r in rows}
                hits = []
                for rid, dist in ann_results:
                    row = row_map.get(rid)
                    if not row:
                        continue
                    try:
                        metadata = json.loads(row['metadata_json'] or '{}')
                    except (TypeError, ValueError):
                        metadata = {}
                    hits.append({'id': rid, 'distance': dist, 'metadata': metadata})
                return hits[:top_k]
            finally:
                conn.close()

    # 回落暴力
    return embed_query(book_id, query_text, embedding_backend, top_k=top_k, where=where)


def embed_clear(book_id):
    init_db(book_id)
    with db_transaction(book_id) as conn:
        conn.execute('DELETE FROM vector_entries WHERE book_id=?', (book_id,))
        conn.execute('DELETE FROM embedding_chunks WHERE book_id=?', (book_id,))
    from main import get_book_dir
    shutil.rmtree(os.path.join(get_book_dir(book_id), '.vector_db'), ignore_errors=True)

def get_done_chapter_count(book_id):
    init_db(book_id)
    conn = _get_conn(book_id)
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM chapters WHERE book_id=? AND status IN ('done','skipped')", (book_id,)).fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()

def get_kb_overview(book_id, current_idx=None):
    init_db(book_id)
    conn = _get_conn(book_id)
    try:
        overview = {}
        for status in ('pending', 'processing', 'done', 'failed', 'skipped'):
            row = conn.execute('SELECT COUNT(*) as cnt FROM chapters WHERE book_id=? AND status=?', (book_id, status)).fetchone()
            overview[f'{status}_chapters'] = row['cnt'] if row else 0
        overview['mentions'] = conn.execute('''SELECT COUNT(*) as cnt
            FROM mentions m JOIN entities e ON m.entity_id=e.id
            WHERE e.book_id=?''', (book_id,)).fetchone()['cnt']
        overview['events'] = conn.execute('SELECT COUNT(*) as cnt FROM events WHERE book_id=?', (book_id,)).fetchone()['cnt']
        overview['rules'] = conn.execute('SELECT COUNT(*) as cnt FROM rules WHERE book_id=?', (book_id,)).fetchone()['cnt']
        overview['foreshadowing_open'] = conn.execute(
            'SELECT COUNT(*) as cnt FROM foreshadowing WHERE book_id=? AND status=\'open\'', (book_id,)
        ).fetchone()['cnt']
        overview['foreshadowing_resolved'] = conn.execute(
            'SELECT COUNT(*) as cnt FROM foreshadowing WHERE book_id=? AND status=\'resolved\'', (book_id,)
        ).fetchone()['cnt']
        overview['embedding_chunks'] = conn.execute(
            'SELECT COUNT(*) as cnt FROM embedding_chunks WHERE book_id=?', (book_id,)
        ).fetchone()['cnt']

        current = None
        if current_idx is not None and current_idx >= 0:
            current = conn.execute('''SELECT id, idx, title, status, summary, error
                FROM chapters WHERE book_id=? AND idx=?''', (book_id, current_idx)).fetchone()
        if current:
            overview['current_chapter'] = dict(current)
        else:
            overview['current_chapter'] = None

        rows = conn.execute('''SELECT id, idx, title, summary, updated_at
            FROM chapters
            WHERE book_id=? AND status='done' AND COALESCE(summary, '') <> ''
            ORDER BY idx DESC LIMIT 5''', (book_id,)).fetchall()
        overview['latest_notes'] = [dict(r) for r in rows]
        return overview
    finally:
        conn.close()

def embed_collection_count(book_id):
    init_db(book_id)
    conn = _get_conn(book_id)
    try:
        row = conn.execute(
            'SELECT COUNT(*) AS cnt FROM vector_entries WHERE book_id=?',
            (book_id,),
        ).fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()

# ─── Content hashing ───

def hash_content(content):
    if not content: return ''
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


# ─── KB Proposals & Edit Log DAO ───

_EDITABLE_FIELDS = {
    'entities': ('canonical_name', 'type', 'aliases', 'first_chapter_id'),
    'mentions': ('fact', 'snippet', 'chapter_id'),
    'events': ('story_time', 'who', 'what', 'where_loc', 'why', 'consequence', 'chapter_id'),
    'foreshadowing': ('hint', 'status', 'resolved_chapter_id', 'resolution', 'hint_chapter_id'),
    'rules': ('name', 'body', 'first_chapter_id'),
}

def _check_editable(table_name, field):
    allowed = _EDITABLE_FIELDS.get(table_name)
    if not allowed:
        raise ValueError(f'不允许修改的表：{table_name}')
    if field not in allowed:
        raise ValueError(f'不允许修改的字段：{table_name}.{field}')

def _record_exists(conn, table_name, record_id, book_id):
    if table_name in ('entities', 'events', 'foreshadowing', 'rules'):
        row = conn.execute(f'SELECT 1 FROM {table_name} WHERE id=? AND book_id=?', (record_id, book_id)).fetchone()
    elif table_name == 'mentions':
        row = conn.execute('''SELECT 1 FROM mentions m JOIN entities e ON m.entity_id=e.id
            WHERE m.id=? AND e.book_id=?''', (record_id, book_id)).fetchone()
    else:
        return False
    return row is not None

def _read_field(conn, table_name, record_id, field, book_id):
    if table_name in ('entities', 'events', 'foreshadowing', 'rules'):
        row = conn.execute(f'SELECT {field} FROM {table_name} WHERE id=? AND book_id=?',
            (record_id, book_id)).fetchone()
    elif table_name == 'mentions':
        row = conn.execute(f'''SELECT m.{field} FROM mentions m JOIN entities e ON m.entity_id=e.id
            WHERE m.id=? AND e.book_id=?''', (record_id, book_id)).fetchone()
    else:
        return None
    return row[field] if row else None

def _write_field(conn, table_name, record_id, field, new_value, book_id):
    if table_name in ('entities', 'foreshadowing', 'rules'):
        timestamp_col = 'updated_at'
        conn.execute(f'UPDATE {table_name} SET {field}=?, {timestamp_col}=? WHERE id=? AND book_id=?',
            (new_value, int(time.time()), record_id, book_id))
    elif table_name == 'events':
        conn.execute(f'UPDATE events SET {field}=? WHERE id=? AND book_id=?',
            (new_value, record_id, book_id))
    elif table_name == 'mentions':
        conn.execute(f'''UPDATE mentions SET {field}=? WHERE id=? AND entity_id IN
            (SELECT id FROM entities WHERE book_id=?)''', (new_value, record_id, book_id))

def create_proposal(book_id, table_name, record_id, field, new_value, reason=None, source_message=None):
    _check_editable(table_name, field)
    pid = str(uuid.uuid4())
    now = int(time.time())
    with db_transaction(book_id) as conn:
        if not _record_exists(conn, table_name, record_id, book_id):
            raise ValueError(f'记录不存在：{table_name}/{record_id}')
        old_val = _read_field(conn, table_name, record_id, field, book_id)
        conn.execute('''INSERT INTO kb_proposals
            (id, book_id, table_name, record_id, field, old_value, new_value, reason, status, source_message, created_at)
            VALUES (?,?,?,?,?,?,?,?, 'pending', ?, ?)''',
            (pid, book_id, table_name, record_id, field,
             '' if old_val is None else str(old_val),
             '' if new_value is None else str(new_value),
             reason, source_message, now))
    return pid

def list_proposals(book_id, status='pending', limit=50):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT * FROM kb_proposals
            WHERE book_id=? AND status=? ORDER BY created_at DESC LIMIT ?''',
            (book_id, status, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_proposal(book_id, proposal_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT * FROM kb_proposals WHERE id=? AND book_id=?',
            (proposal_id, book_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def confirm_proposal(book_id, proposal_id):
    now = int(time.time())
    with db_transaction(book_id) as conn:
        row = conn.execute('SELECT * FROM kb_proposals WHERE id=? AND book_id=? AND status=?',
            (proposal_id, book_id, 'pending')).fetchone()
        if not row:
            raise ValueError('提议不存在或已处理')
        p = dict(row)
        _check_editable(p['table_name'], p['field'])
        if not _record_exists(conn, p['table_name'], p['record_id'], book_id):
            raise ValueError(f'记录不存在：{p["table_name"]}/{p["record_id"]}')
        actual_old = _read_field(conn, p['table_name'], p['record_id'], p['field'], book_id)
        _write_field(conn, p['table_name'], p['record_id'], p['field'], p['new_value'], book_id)
        conn.execute('UPDATE kb_proposals SET status=?, resolved_at=? WHERE id=?',
            ('confirmed', now, proposal_id))
        conn.execute('''INSERT INTO kb_edit_log
            (book_id, table_name, record_id, field, old_value, new_value, reason, source, proposal_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (book_id, p['table_name'], p['record_id'], p['field'],
             '' if actual_old is None else str(actual_old),
             p['new_value'], p['reason'], 'ai-confirmed', proposal_id, now))
        return p

def reject_proposal(book_id, proposal_id):
    now = int(time.time())
    with db_transaction(book_id) as conn:
        conn.execute('UPDATE kb_proposals SET status=?, resolved_at=? WHERE id=? AND book_id=? AND status=?',
            ('rejected', now, proposal_id, book_id, 'pending'))

def apply_kb_edit(book_id, table_name, record_id, field, new_value, reason=None, source='user'):
    _check_editable(table_name, field)
    now = int(time.time())
    with db_transaction(book_id) as conn:
        if not _record_exists(conn, table_name, record_id, book_id):
            raise ValueError(f'记录不存在：{table_name}/{record_id}')
        old_val = _read_field(conn, table_name, record_id, field, book_id)
        _write_field(conn, table_name, record_id, field, new_value, book_id)
        cur = conn.execute('''INSERT INTO kb_edit_log
            (book_id, table_name, record_id, field, old_value, new_value, reason, source, proposal_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,NULL,?)''',
            (book_id, table_name, record_id, field,
             '' if old_val is None else str(old_val),
             '' if new_value is None else str(new_value),
             reason, source, now))
        log_id = cur.lastrowid
    return {'log_id': log_id, 'old_value': old_val, 'new_value': new_value}

def get_edit_log(book_id, log_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT * FROM kb_edit_log WHERE id=? AND book_id=?',
            (log_id, book_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def list_edit_log(book_id, limit=30, include_undone=False):
    conn = _get_conn(book_id)
    try:
        q = 'SELECT * FROM kb_edit_log WHERE book_id=?'
        params = [book_id]
        if not include_undone:
            q += ' AND undone=0'
        q += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def undo_edit(book_id, log_id):
    now = int(time.time())
    with db_transaction(book_id) as conn:
        row = conn.execute('SELECT * FROM kb_edit_log WHERE id=? AND book_id=? AND undone=0',
            (log_id, book_id)).fetchone()
        if not row:
            raise ValueError('修改记录不存在或已撤销')
        e = dict(row)
        _check_editable(e['table_name'], e['field'])
        if not _record_exists(conn, e['table_name'], e['record_id'], book_id):
            raise ValueError(f'记录不存在：{e["table_name"]}/{e["record_id"]}')
        _write_field(conn, e['table_name'], e['record_id'], e['field'], e['old_value'], book_id)
        conn.execute('UPDATE kb_edit_log SET undone=1 WHERE id=?', (log_id,))
        conn.execute('''INSERT INTO kb_edit_log
            (book_id, table_name, record_id, field, old_value, new_value, reason, source, proposal_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,NULL,?)''',
            (book_id, e['table_name'], e['record_id'], e['field'],
             e['new_value'], e['old_value'], f'撤销 log#{log_id}', 'undo', now))
        return e

def get_kb_cloud(book_id, limit=40):
    """Return top entities by mention count for word cloud visualization."""
    init_db(book_id)
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('''SELECT e.id, e.canonical_name, e.type, COUNT(m.id) as weight
            FROM entities e
            LEFT JOIN mentions m ON m.entity_id=e.id
            WHERE e.book_id=?
            GROUP BY e.id
            ORDER BY weight DESC
            LIMIT ?''', (book_id, limit)).fetchall()
        return [{'id': r['id'], 'name': r['canonical_name'], 'type': r['type'], 'weight': r['weight']} for r in rows]
    finally:
        conn.close()


# ─── KB lookup helpers (for AI tool use) ───

def lookup_kb(book_id, query, types=None, limit=10, entity_kind=None):
    """Search across entities/mentions/events/foreshadowing/rules.
    Returns list of dicts with {kind, id, ...record-specific fields, chapter_id?}.
    entity_kind: optional filter for entity type (e.g. '人物', '功法'). Only affects entities branch.
    """
    q_norm = (query or '').strip().lower()
    if not q_norm:
        return []
    types = set(types) if types else {'entities', 'mentions', 'events', 'foreshadowing', 'rules'}
    results = []
    conn = _get_conn(book_id)
    try:
        if 'entities' in types:
            if entity_kind:
                rows = conn.execute('SELECT * FROM entities WHERE book_id=? AND type=?', (book_id, entity_kind)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM entities WHERE book_id=?', (book_id,)).fetchall()
            for r in rows:
                hay = r['canonical_name'].lower()
                aliases = []
                try:
                    aliases = json.loads(r['aliases'] or '[]')
                except Exception:
                    pass
                hay_aliases = ' '.join(a.lower() for a in aliases)
                if q_norm in hay or q_norm in hay_aliases:
                    results.append({
                        'kind': 'entity', 'id': r['id'],
                        'canonical_name': r['canonical_name'], 'type': r['type'],
                        'aliases': aliases, 'first_chapter_id': r['first_chapter_id'],
                    })
        if 'mentions' in types:
            rows = conn.execute('''SELECT m.*, e.canonical_name as entity_name, c.idx as chapter_idx, c.title as chapter_title
                FROM mentions m JOIN entities e ON m.entity_id=e.id
                LEFT JOIN chapters c ON m.chapter_id=c.id
                WHERE e.book_id=?''', (book_id,)).fetchall()
            for r in rows:
                fact = r['fact'] or ''
                snip = r['snippet'] or ''
                ent = r['entity_name'] or ''
                if q_norm in fact.lower() or q_norm in snip.lower() or q_norm in ent.lower():
                    results.append({
                        'kind': 'mention', 'id': r['id'], 'entity_id': r['entity_id'],
                        'entity_name': ent, 'fact': fact, 'snippet': snip,
                        'chapter_id': r['chapter_id'], 'chapter_idx': r['chapter_idx'],
                        'chapter_title': r['chapter_title'],
                    })
        if 'events' in types:
            rows = conn.execute('''SELECT e.*, c.idx as chapter_idx, c.title as chapter_title
                FROM events e LEFT JOIN chapters c ON e.chapter_id=c.id
                WHERE e.book_id=?''', (book_id,)).fetchall()
            for r in rows:
                hay = ' '.join(str(r[k] or '') for k in ('story_time','who','what','where_loc','why','consequence')).lower()
                if q_norm in hay:
                    results.append({
                        'kind': 'event', 'id': r['id'],
                        'story_time': r['story_time'], 'who': r['who'], 'what': r['what'],
                        'where_loc': r['where_loc'], 'why': r['why'], 'consequence': r['consequence'],
                        'chapter_id': r['chapter_id'], 'chapter_idx': r['chapter_idx'],
                        'chapter_title': r['chapter_title'],
                    })
        if 'foreshadowing' in types:
            rows = conn.execute('SELECT * FROM foreshadowing WHERE book_id=?', (book_id,)).fetchall()
            for r in rows:
                hay = ' '.join(str(r[k] or '') for k in ('hint','resolution')).lower()
                if q_norm in hay:
                    results.append({
                        'kind': 'foreshadowing', 'id': r['id'],
                        'hint': r['hint'], 'status': r['status'],
                        'hint_chapter_id': r['hint_chapter_id'],
                        'resolved_chapter_id': r['resolved_chapter_id'],
                        'resolution': r['resolution'],
                    })
        if 'rules' in types:
            rows = conn.execute('SELECT * FROM rules WHERE book_id=?', (book_id,)).fetchall()
            for r in rows:
                hay = ' '.join(str(r[k] or '') for k in ('name','body')).lower()
                if q_norm in hay:
                    results.append({
                        'kind': 'rule', 'id': r['id'], 'name': r['name'],
                        'body': r['body'], 'first_chapter_id': r['first_chapter_id'],
                    })
    finally:
        conn.close()
    return results[:limit]

def get_kb_record(book_id, table_name, record_id):
    """Fetch a single record by table+id for citation/context."""
    conn = _get_conn(book_id)
    try:
        if table_name == 'entities':
            row = conn.execute('SELECT * FROM entities WHERE id=? AND book_id=?', (record_id, book_id)).fetchone()
        elif table_name == 'mentions':
            row = conn.execute('''SELECT m.*, e.canonical_name as entity_name, c.idx as chapter_idx, c.title as chapter_title
                FROM mentions m JOIN entities e ON m.entity_id=e.id
                LEFT JOIN chapters c ON m.chapter_id=c.id
                WHERE m.id=? AND e.book_id=?''', (record_id, book_id)).fetchone()
        elif table_name == 'events':
            row = conn.execute('''SELECT e.*, c.idx as chapter_idx, c.title as chapter_title
                FROM events e LEFT JOIN chapters c ON e.chapter_id=c.id
                WHERE e.id=? AND e.book_id=?''', (record_id, book_id)).fetchone()
        elif table_name == 'foreshadowing':
            row = conn.execute('SELECT * FROM foreshadowing WHERE id=? AND book_id=?', (record_id, book_id)).fetchone()
        elif table_name == 'rules':
            row = conn.execute('SELECT * FROM rules WHERE id=? AND book_id=?', (record_id, book_id)).fetchone()
        else:
            return None
        return dict(row) if row else None
    finally:
        conn.close()


# ─── P4: 脏队列 DAO ───

def enqueue_dirty(work_id, book_id, chapter_id, reason='save'):
    """将变更章加入脏队列（同章去重）。"""
    now = int(time.time())
    eid = str(uuid.uuid4())
    with db_transaction(work_id) as conn:
        existing = conn.execute(
            'SELECT id FROM kb_dirty_queue WHERE work_id=? AND book_id=? AND chapter_id=? AND status=?',
            (work_id, book_id, chapter_id, 'pending')
        ).fetchone()
        if existing:
            return existing['id']
        conn.execute('''INSERT OR IGNORE INTO kb_dirty_queue
            (id, work_id, book_id, chapter_id, reason, enqueued_at, status)
            VALUES (?,?,?,?,?,?,'pending')''',
            (eid, work_id, book_id, chapter_id, reason, now))
    return eid


def dequeue_dirty_batch(work_id, limit=5):
    """取出该 work 的一批待处理章，标 processing。"""
    conn = _get_conn(work_id)
    try:
        rows = conn.execute('''SELECT * FROM kb_dirty_queue
            WHERE work_id=? AND status='pending'
            ORDER BY enqueued_at ASC LIMIT ?''', (work_id, limit)).fetchall()
        if not rows:
            return []
        ids = [r['id'] for r in rows]
        ph = ','.join('?' * len(ids))
        conn.execute(f"UPDATE kb_dirty_queue SET status='processing' WHERE id IN ({ph})", ids)
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_dirty_done(work_id, item_id, error=None):
    """标记脏队列项完成/错误。"""
    with db_transaction(work_id) as conn:
        if error:
            conn.execute('UPDATE kb_dirty_queue SET status=?, attempts=attempts+1 WHERE id=?',
                         ('error', item_id))
        else:
            conn.execute('UPDATE kb_dirty_queue SET status=? WHERE id=?',
                         ('done', item_id))


def reset_stale_dirty(work_id):
    """启动时把 processing 复位为 pending。"""
    with db_transaction(work_id) as conn:
        conn.execute("UPDATE kb_dirty_queue SET status='pending' WHERE work_id=? AND status='processing'",
                     (work_id,))

def count_pending_dirty(work_id):
    """统计待处理的脏队列项数。"""
    conn = _get_conn(work_id)
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM kb_dirty_queue WHERE work_id=? AND status='pending'",
            (work_id,)).fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()
