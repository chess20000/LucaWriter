import os
import sqlite3
import json
import time
import uuid
import threading
import hashlib
import re
from contextlib import contextmanager

_local_storage = threading.local()
_db_locks = {}

def get_kb_path(book_id):
    from main import get_book_dir
    return os.path.join(get_book_dir(book_id), 'kb.db')

def _get_lock(book_id):
    if book_id not in _db_locks:
        _db_locks[book_id] = threading.RLock()
    return _db_locks[book_id]

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

def init_db(book_id):
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
        ''')
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(rt_state)').fetchall()}
        if 'active_start_idx' not in cols:
            conn.execute('ALTER TABLE rt_state ADD COLUMN active_start_idx INTEGER DEFAULT -1')
        if 'active_end_idx' not in cols:
            conn.execute('ALTER TABLE rt_state ADD COLUMN active_end_idx INTEGER DEFAULT -1')

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
        return conn.execute('SELECT * FROM chapters WHERE id=? AND book_id=?', (chapter_id, book_id)).fetchone()
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
        conn.execute('DELETE FROM mentions WHERE chapter_id=?', (chapter_id,))
        conn.execute('DELETE FROM events WHERE chapter_id=?', (chapter_id,))
        conn.execute('DELETE FROM foreshadowing WHERE hint_chapter_id=? OR resolved_chapter_id=?', (chapter_id, chapter_id))
        conn.execute('DELETE FROM rules WHERE first_chapter_id=?', (chapter_id,))
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

# ─── Mention DAO ───

def add_mention(book_id, entity_id, chapter_id, fact, snippet=None):
    mid = str(uuid.uuid4())
    now = int(time.time())
    with db_transaction(book_id) as conn:
        conn.execute('INSERT INTO mentions (id, entity_id, chapter_id, fact, snippet, created_at) VALUES (?,?,?,?,?,?)',
            (mid, entity_id, chapter_id, fact, snippet, now))
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
        rows = conn.execute('''SELECT m.*
            FROM mentions m JOIN entities e ON m.entity_id=e.id
            WHERE e.book_id=? AND m.chapter_id=?''', (book_id, chapter_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ─── Event DAO ───

def add_event(book_id, chapter_id, story_time, who, what, where_loc, why, consequence):
    eid = str(uuid.uuid4())
    now = int(time.time())
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

# ─── Foreshadowing DAO ───

def add_foreshadowing(book_id, hint_chapter_id, hint, status='open', resolved_chapter_id=None, resolution=None):
    fid = str(uuid.uuid4())
    now = int(time.time())
    with db_transaction(book_id) as conn:
        conn.execute('''INSERT INTO foreshadowing (id, book_id, hint_chapter_id, hint, status, resolved_chapter_id, resolution, updated_at)
            VALUES (?,?,?,?,?,?,?,?)''', (fid, book_id, hint_chapter_id, hint, status, resolved_chapter_id, resolution, now))
    return fid

def resolve_foreshadowing(book_id, hint, resolved_chapter_id, resolution):
    now = int(time.time())
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
    with db_transaction(book_id) as conn:
        existing = conn.execute('SELECT * FROM rules WHERE book_id=? AND name=?', (book_id, name)).fetchone()
        if existing:
            conn.execute('UPDATE rules SET body=?, updated_at=? WHERE id=?', (body, now, existing['id']))
            return existing['id']
        rid = str(uuid.uuid4())
        conn.execute('INSERT INTO rules (id, book_id, name, body, first_chapter_id, updated_at) VALUES (?,?,?,?,?,?)',
            (rid, book_id, name, body, first_chapter_id, now))
        return rid

def list_rules(book_id):
    conn = _get_conn(book_id)
    try:
        rows = conn.execute('SELECT * FROM rules WHERE book_id=? ORDER BY name', (book_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

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
            'mentions', 'events', 'foreshadowing', 'rules',
            'entities', 'chapters', 'rt_logs', 'rt_state',
            'embedding_chunks',
        ):
            conn.execute(f'DELETE FROM {table} WHERE book_id=?' if table not in ('mentions',) else
                         '''DELETE FROM mentions WHERE entity_id IN
                            (SELECT id FROM entities WHERE book_id=?)''', (book_id,))

def count_embedding_chunks(book_id):
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT COUNT(*) as cnt FROM embedding_chunks WHERE book_id=?', (book_id,)).fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()

# ─── ChromaDB Wrapper ───

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
import numpy as np

_chroma_clients = {}
_CHROMA_KB_COLLECTION = 'luca_kb'

def _get_chroma_client(book_id):
    from main import get_book_dir
    persist_dir = os.path.join(get_book_dir(book_id), '.vector_db')
    if book_id not in _chroma_clients:
        _chroma_clients[book_id] = chromadb.PersistentClient(path=persist_dir)
    return _chroma_clients[book_id]

def _get_chroma_collection(book_id, embedding_function):
    client = _get_chroma_client(book_id)
    try:
        return client.get_collection(name=_CHROMA_KB_COLLECTION, embedding_function=embedding_function)
    except:
        return client.create_collection(name=_CHROMA_KB_COLLECTION, embedding_function=embedding_function)

def embed_upsert(book_id, chunk_id, text, embedding_backend, source_type=None, source_id=None):
    ef = _ChromaEmbeddingFunc(embedding_backend)
    collection = _get_chroma_collection(book_id, ef)
    vec = embedding_backend.embed([text])[0]
    metadata = {}
    if source_type: metadata['source_type'] = source_type
    if source_id: metadata['source_id'] = source_id
    collection.upsert(ids=[chunk_id], embeddings=[vec], metadatas=[metadata])
    content_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    register_embedding_chunk(book_id, chunk_id, source_type or '', source_id or '', content_hash, embedding_backend.backend_id)

def embed_query(book_id, query_text, embedding_backend, top_k=10):
    ef = _ChromaEmbeddingFunc(embedding_backend)
    try:
        collection = _get_chroma_collection(book_id, ef)
    except:
        return []
    q_vec = embedding_backend.embed([query_text])[0]
    results = collection.query(query_embeddings=[q_vec], n_results=top_k)
    hits = []
    if results['ids'] and results['ids'][0]:
        for i, id_ in enumerate(results['ids'][0]):
            hits.append({
                'id': id_,
                'distance': results['distances'][0][i] if results.get('distances') else 0,
                'metadata': results['metadatas'][0][i] if results.get('metadatas') else {},
            })
    return hits

def embed_clear(book_id):
    try:
        client = _get_chroma_client(book_id)
        try:
            client.delete_collection(name=_CHROMA_KB_COLLECTION)
        except:
            pass
    except:
        pass
    clear_embedding_chunks(book_id)

def get_done_chapter_count(book_id):
    init_db(book_id)
    conn = _get_conn(book_id)
    try:
        row = conn.execute('SELECT COUNT(*) as cnt FROM chapters WHERE book_id=? AND status=\'done\'', (book_id,)).fetchone()
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
        overview['entities'] = conn.execute('SELECT COUNT(*) as cnt FROM entities WHERE book_id=?', (book_id,)).fetchone()['cnt']
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
    try:
        client = _get_chroma_client(book_id)
        collection = client.get_collection(name=_CHROMA_KB_COLLECTION)
        return collection.count()
    except:
        return 0

class _ChromaEmbeddingFunc(EmbeddingFunction):
    def __init__(self, backend):
        self._backend = backend
    def __call__(self, input: Documents) -> Embeddings:
        return self._backend.embed(list(input))

# ─── Content hashing ───

def hash_content(content):
    if not content: return ''
    return hashlib.sha256(content.encode('utf-8')).hexdigest()
