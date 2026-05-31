import os
import sys
import json
import time
import hashlib
import hmac
import zipfile
import io
import secrets
import glob
import re
import shutil
import base64
import xml.etree.ElementTree as ET
import subprocess
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, unquote
import urllib.request
import threading
import queue
from datetime import datetime
from ipaddress import ip_network, ip_address
from http.cookies import SimpleCookie
import html as _html_mod
import ssl
if not os.environ.get('SSL_CERT_FILE'):
    try:
        import certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
    except ImportError:
        _macos_cert = '/etc/ssl/cert.pem'
        if sys.platform == 'darwin' and os.path.exists(_macos_cert):
            os.environ['SSL_CERT_FILE'] = _macos_cert

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.config import Settings as _ChromaSettings
_CHROMA_SETTINGS = _ChromaSettings(anonymized_telemetry=False)
import numpy as np

import kb_pipeline
import kb_storage

_default_ssl_context = None
def _get_ssl_context():
    global _default_ssl_context
    if _default_ssl_context is None:
        _default_ssl_context = ssl.create_default_context()
        cert_file = os.environ.get('SSL_CERT_FILE')
        if cert_file and os.path.exists(cert_file):
            _default_ssl_context.load_verify_locations(cert_file)
        else:
            _default_ssl_context.load_default_certs()
    return _default_ssl_context

try:
    import docx as docx_mod
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    from PyPDF2 import PdfReader
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    import ebooklib
    from ebooklib import epub as epub_mod
    from html.parser import HTMLParser
    HAS_EPUB = True
except ImportError:
    HAS_EPUB = False

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as _sym_padding
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as _crypto_hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# 导入浏览器控制模块
try:
    import browser_agent
    HAS_BROWSER_AGENT = True
except ImportError:
    HAS_BROWSER_AGENT = False
    browser_agent = None

# 导入图标生成器
try:
    import icon_generator
    HAS_ICON_GENERATOR = True
except ImportError:
    HAS_ICON_GENERATOR = False
    icon_generator = None

LW_MAGIC = b'LW1'
LW_SALT_LEN = 16
LW_IV_LEN = 16
LW_PBKDF2_ITERS = 100000

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def _find_frontend_dir():
    for d in [os.path.join(SCRIPT_DIR, '..', 'frontend'), os.path.join(SCRIPT_DIR, 'frontend'), SCRIPT_DIR, '/workspace/lucawriter/frontend', '/app']:
        if os.path.exists(os.path.join(d, 'index.html')):
            return d
    return SCRIPT_DIR

FRONTEND_DIR = os.environ.get('FRONTEND_DIR', _find_frontend_dir())

def _find_data_dir():
    env = os.environ.get('DATA_DIR')
    if env:
        return env
    usrdata = os.path.normpath(os.path.join(SCRIPT_DIR, '..', 'usrdata'))
    os.makedirs(usrdata, exist_ok=True)
    return usrdata

DATA_DIR = _find_data_dir()
PORT = 20000 if os.environ.get('DATA_DIR') else 10000
BOOKS_DIR = os.path.join(DATA_DIR, 'books')
LOG_DIR = os.path.join(DATA_DIR, 'logs')
MESSAGES_DIR = os.path.join(DATA_DIR, 'messages')
CHAT_SESSIONS_DIR = os.path.join(DATA_DIR, 'chat_sessions')
os.makedirs(CHAT_SESSIONS_DIR, exist_ok=True)
USER_FONTS_DIR = os.path.join(DATA_DIR, 'fonts')
GLOBAL_CHAT_HISTORY_FILE = os.path.join(DATA_DIR, 'chat_history.json')
SALT_FILE = os.path.join(DATA_DIR, 'salt')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
SESSIONS_FILE = os.path.join(DATA_DIR, 'sessions.json')
FONT_EXTS = {'.ttf': ('font/ttf', 'truetype'), '.otf': ('font/otf', 'opentype')}
BUILTIN_EDITOR_FONT_IDS = {'builtin_serif', 'builtin_sans', 'builtin_mono'}
_MB = 1024 * 1024
ZIP_MAX_ENTRIES = 5000
ZIP_MAX_TOTAL_BYTES = 500 * _MB
ZIP_MAX_ENTRY_BYTES = 100 * _MB
ZIP_READ_CHUNK = 256 * 1024
DOCX_MAX_XML_BYTES = 40 * _MB
EPUB_MAX_META_BYTES = 5 * _MB
EPUB_MAX_HTML_BYTES = 12 * _MB
EPUB_MAX_TEXT_TOTAL_BYTES = 120 * _MB
EPUB_MAX_COVER_BYTES = 12 * _MB

RESERVED_FILES = {'settings', 'users', 'messages', 'salt', 'outline', 'sessions', 'meta'}

_LOCAL_LLM_PORT_FILE = os.path.join(DATA_DIR, 'local_llm_port')
_LOCAL_LLM_PORT_CACHE = None
_LOCAL_LLM_PORT_MIN = 20000
_LOCAL_LLM_PORT_MAX = 65000

def _is_local_llm_preset(p):
    name = (p.get('name') or '').lower() if isinstance(p, dict) else ''
    return 'llama.cpp' in name

def _local_llm_port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', int(port)))
        return True
    except Exception:
        return False

def _get_local_llm_port():
    """返回稳定的随机端口；首次生成后持久化到 usrdata/local_llm_port。"""
    global _LOCAL_LLM_PORT_CACHE
    if _LOCAL_LLM_PORT_CACHE:
        return _LOCAL_LLM_PORT_CACHE
    if os.path.exists(_LOCAL_LLM_PORT_FILE):
        try:
            with open(_LOCAL_LLM_PORT_FILE, 'r', encoding='utf-8') as f:
                p = int(f.read().strip())
            if _LOCAL_LLM_PORT_MIN <= p <= _LOCAL_LLM_PORT_MAX:
                _LOCAL_LLM_PORT_CACHE = p
                return p
        except Exception:
            pass
    span = _LOCAL_LLM_PORT_MAX - _LOCAL_LLM_PORT_MIN + 1
    port = None
    for _ in range(80):
        candidate = _LOCAL_LLM_PORT_MIN + secrets.randbelow(span)
        if candidate != PORT and _local_llm_port_available(candidate):
            port = candidate
            break
    if port is None:
        port = _LOCAL_LLM_PORT_MIN + secrets.randbelow(span)
    try:
        with open(_LOCAL_LLM_PORT_FILE, 'w', encoding='utf-8') as f:
            f.write(str(port))
    except Exception:
        pass
    _LOCAL_LLM_PORT_CACHE = port
    return port

def _local_llm_base_url(port=None):
    return f"http://127.0.0.1:{int(port or _get_local_llm_port())}/v1"

def _normalize_local_llm_preset(p, port=None):
    if not isinstance(p, dict) or not _is_local_llm_preset(p):
        return False
    expected = _local_llm_base_url(port)
    changed = False
    fields = {
        'base_url': expected,
        'api_key': '',
        'use_custom_json': False,
        'custom_json': '',
        'context_length': 65536,
    }
    for k, v in fields.items():
        if p.get(k) != v:
            p[k] = v
            changed = True
    return changed

DEFAULT_PROVIDER_PRESETS = [
    {'name': 'LMStudio', 'base_url': 'http://localhost:1234/v1', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''},
    {'name': 'DeepSeek', 'base_url': 'https://api.deepseek.com', 'api_key': '', 'model': 'deepseek-chat', 'use_custom_json': False, 'custom_json': ''},
    {'name': '自定义1', 'base_url': '', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''},
    {'name': '自定义2', 'base_url': '', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''},
    {'name': '本地 Llama.cpp', 'base_url': _local_llm_base_url(), 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': '', 'context_length': 65536},
]

DEFAULT_SETTINGS = {
    'base_url': '', 'api_key': '', 'model': '', 'models': [],
    'ai_frequency': 500, 'ai_max_tokens': 512, 'ai_temperature': None,
    'ai_auto_comment': True,
    'ai_system_prompt': '你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。温文尔雅，沉稳从容。惜字如金，只输出简练聊天文字，不加任何markdown标记。根据接入模型的不同，你的性格可能有细微差别，但核心身份不变。\n\n【绝对禁止】\n禁止展开描述自己的身份、角色、人设。被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话就够了，严禁展开。\n禁止自我评价："我很真诚""我是个XX的人"之类。你的品格应从言行中自然流露，不是说出来的。',
    'outline_enabled': True, 'outline_frequency': 2000,
    'provider_presets': [],
    'active_provider_idx': 0,
    'model_context_length': 0,
    'shortcut_focus_ai': 'alt',
    'search_api_key': '',
    'search_provider': 'duckduckgo',
    'access_scope': '127.0.0.1',
    'keep_background': False,
    'network_search': 'on',
    'theme_accent': '#E8CC7A',
    'theme_mode': 'dark',
    'ui_scale': 1.0,
    'content_font_size': 20,
    'editor_font_weight': 200,
    'editor_font_preset_id': '',
    'editor_font_presets': [],
    'embedding_backend': 'local',
    'local_embedding_model': 'BAAI/bge-small-zh-v1.5',
    'embedding_model': 'text-embedding-3-small',
    'custom_colors': {},
}
DEFAULT_OUTLINE = {
    'worldview': '', 'characters': [], 'timeline': [],
    'key_events': [], 'rules': [], 'updated': 0, 'chapter_summaries': {},
    'timeline_nodes': [],
    'ai_suggestions': {
        'worldview': '', 'characters': [], 'timeline': [],
        'key_events': [], 'rules': [], 'updated': 0,
    },
}


def _lw_derive_key(password, salt):
    if HAS_CRYPTO:
        kdf = PBKDF2HMAC(
            algorithm=_crypto_hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=LW_PBKDF2_ITERS,
        )
        return kdf.derive(password.encode('utf-8'))
    else:
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, LW_PBKDF2_ITERS, dklen=32)


def _lw_encrypt(data, password):
    salt = os.urandom(LW_SALT_LEN)
    key = _lw_derive_key(password, salt)
    if HAS_CRYPTO:
        iv = os.urandom(LW_IV_LEN)
        padder = _sym_padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ct = encryptor.update(padded) + encryptor.finalize()
        hmac_val = hashlib.sha256(key + iv + ct).digest()[:16]
        return LW_MAGIC + salt + iv + hmac_val + ct
    else:
        iv = os.urandom(LW_IV_LEN)
        keystream_seed = hashlib.sha256(key + iv).digest()
        ct = bytearray()
        for i in range(len(data)):
            ki = i % 32
            if ki == 0:
                keystream_seed = hashlib.sha256(key + keystream_seed).digest()
            ct.append(data[i] ^ keystream_seed[ki])
        hmac_val = hashlib.sha256(key + iv + bytes(ct)).digest()[:16]
        return LW_MAGIC + salt + iv + hmac_val + bytes(ct)


def _lw_decrypt(raw, password):
    if len(raw) < 3 + LW_SALT_LEN + LW_IV_LEN + 16:
        raise ValueError('文件格式无效')
    if raw[:3] != LW_MAGIC:
        raise ValueError('不是加密的 .lucawrite 文件')
    off = 3
    salt = raw[off:off + LW_SALT_LEN]; off += LW_SALT_LEN
    iv = raw[off:off + LW_IV_LEN]; off += LW_IV_LEN
    hmac_val = raw[off:off + 16]; off += 16
    ct = raw[off:]
    key = _lw_derive_key(password, salt)
    expected_hmac = hashlib.sha256(key + iv + ct).digest()[:16]
    if not hmac.compare_digest(hmac_val, expected_hmac):
        raise ValueError('密码错误或文件已损坏')
    if HAS_CRYPTO:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ct) + decryptor.finalize()
        unpadder = _sym_padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    else:
        keystream_seed = hashlib.sha256(key + iv).digest()
        pt = bytearray()
        for i in range(len(ct)):
            ki = i % 32
            if ki == 0:
                keystream_seed = hashlib.sha256(key + keystream_seed).digest()
            pt.append(ct[i] ^ keystream_seed[ki])
        return bytes(pt)


def _lw_is_encrypted(raw):
    return len(raw) >= 3 and raw[:3] == LW_MAGIC


def _build_lucawrite_zip(bid):
    bd = get_book_dir(bid)
    if not os.path.isdir(bd):
        raise FileNotFoundError(f'书本目录不存在: {bid}')
    meta = get_book_meta(bid) or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            'format_version': 1,
            'format_name': 'lucawrite',
            'app_name': 'LucaWriter',
            'exported_at': time.time(),
            'book': {
                'id': meta.get('id', bid),
                'title': meta.get('title', ''),
                'author': '',
                'description': '',
                'created': meta.get('created', 0),
                'updated': meta.get('updated', 0),
            },
            'encrypted': False,
        }
        if os.path.exists(os.path.join(bd, 'cover')):
            manifest['book']['cover_file'] = 'cover'
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for root, dirs, files in os.walk(bd):
            for fn in files:
                if fn.startswith('.') or fn.endswith('.tmp'):
                    continue
                fp = os.path.join(root, fn)
                arcname = os.path.relpath(fp, bd).replace('\\', '/')
                try:
                    with open(fp, 'rb') as f:
                        zf.writestr(arcname, f.read())
                except Exception:
                    continue
    return buf.getvalue()


def _import_lucawrite_zip(raw, password=None):
    if _lw_is_encrypted(raw):
        if not password:
            raise ValueError('该文件已加密，请输入密码')
        raw = _lw_decrypt(raw, password)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw), 'r')
    except zipfile.BadZipFile:
        raise ValueError('无效的 .lucawrite 文件（无法解压）')
    try:
        _validate_zip_archive(zf)
        manifest_str = _zip_read_limited(zf, 'manifest.json', EPUB_MAX_META_BYTES).decode('utf-8')
        manifest = json.loads(manifest_str)
    except Exception:
        raise ValueError('无效的 .lucawrite 文件（缺少 manifest.json）')
    if manifest.get('format_name') != 'lucawrite':
        raise ValueError('不是有效的 .lucawrite 文件')
    bid = 'book_' + str(int(time.time() * 1000))
    bd = get_book_dir(bid)
    os.makedirs(bd, exist_ok=True)
    # zip bomb 防护：单文件 100MB，总解压 500MB；逐 chunk 流式写避免一次性 RAM
    _MAX_PER_ENTRY = 100 * 1024 * 1024
    _MAX_TOTAL = 500 * 1024 * 1024
    _CHUNK = 256 * 1024
    total_written = 0
    for info in zf.infolist():
        if info.filename == 'manifest.json':
            continue
        if info.is_dir():
            os.makedirs(os.path.join(bd, info.filename), exist_ok=True)
            continue
        safe_name = info.filename.replace('\\', '/')
        if safe_name.startswith('/') or '..' in safe_name.split('/'):
            continue
        if info.file_size > _MAX_PER_ENTRY:
            log_action('LW_IMPORT_SKIP_BIG', f'{safe_name} size={info.file_size}')
            continue
        if total_written + info.file_size > _MAX_TOTAL:
            raise ValueError(f'.lucawrite 解压超过 {_MAX_TOTAL // 1024 // 1024}MB 上限')
        fp = os.path.join(bd, safe_name)
        if not os.path.realpath(fp).startswith(os.path.realpath(bd)):
            continue
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        try:
            written_this = 0
            with zf.open(info.filename) as src, open(fp, 'wb') as dst:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk: break
                    written_this += len(chunk)
                    if written_this > _MAX_PER_ENTRY:
                        # 压缩比异常，提前中止
                        raise ValueError(f'{safe_name} 解压超过单文件上限')
                    dst.write(chunk)
            total_written += written_this
        except ValueError:
            raise
        except Exception:
            continue
    meta = get_book_meta(bid) or {}
    book_info = manifest.get('book', {})
    if book_info.get('author'):
        meta['author'] = book_info['author']
    if book_info.get('description'):
        meta['description'] = book_info['description']
    meta['id'] = bid
    if not meta.get('title'):
        meta['title'] = book_info.get('title', '导入的书本')
    save_json(os.path.join(bd, 'meta.json'), meta)
    cover_file = book_info.get('cover_file', '')
    if cover_file and os.path.exists(os.path.join(bd, cover_file)):
        try:
            with open(os.path.join(bd, cover_file), 'rb') as f:
                cover_data = f.read()
            with open(os.path.join(bd, 'cover'), 'wb') as f:
                f.write(cover_data)
        except Exception:
            pass
    zf.close()
    return bid, meta, manifest


def ensure_dirs():
    for d in [DATA_DIR, BOOKS_DIR, LOG_DIR, MESSAGES_DIR, USER_FONTS_DIR]:
        os.makedirs(d, exist_ok=True)


ensure_dirs()


def _import_builtin_books():
    builtin_dir = os.environ.get('BUILTIN_BOOKS_DIR', '')
    if not builtin_dir or not os.path.isdir(builtin_dir):
        return
    # Clean up old builtin books from previous versions to avoid duplicates
    data_dir = os.environ.get('DATA_DIR', os.path.join(SCRIPT_DIR, 'data'))
    if os.path.isdir(data_dir):
        for entry in os.listdir(data_dir):
            if entry.startswith('builtin_') and entry != 'builtin_LUCA_Legend':
                old_bd = os.path.join(data_dir, entry)
                if os.path.isdir(old_bd):
                    try:
                        import shutil
                        shutil.rmtree(old_bd)
                        log_action('BUILTIN_CLEAN', f'Removed old builtin book: {entry}')
                    except Exception as e:
                        log_action('BUILTIN_CLEAN_ERR', f'{entry}: {str(e)[:200]}')
    for fn in sorted(os.listdir(builtin_dir)):
        ext = os.path.splitext(fn)[1].lower()
        if ext not in IMPORT_PARSERS:
            continue
        filepath = os.path.join(builtin_dir, fn)
        if not os.path.isfile(filepath):
            continue
        bid = 'builtin_' + re.sub(r'[^\w]', '_', os.path.splitext(fn)[0][:30])
        bd = get_book_dir(bid)
        if os.path.exists(bd):
            continue
        try:
            with open(filepath, 'rb') as f:
                raw = f.read()
            parser = IMPORT_PARSERS[ext]
            result = parser(raw, fn)
            if len(result) == 3:
                chapters, book_title, err = result
            else:
                chapters, err = result
                book_title = ''
            if err or not chapters:
                continue
            ch_dir = os.path.join(bd, 'chapters')
            os.makedirs(ch_dir, exist_ok=True)
            os.makedirs(os.path.join(bd, 'trash'), exist_ok=True)
            order = []
            for i, ch in enumerate(chapters):
                cid = 'ch_' + re.sub(r'[^\w]', '_', ch.get('title', 'untitled')[:30]) + '_' + str(int(time.time() * 1000)) + str(i)
                if not is_valid_id(cid):
                    cid = 'ch_' + str(int(time.time() * 1000)) + str(i)
                ch_data = {'id': cid, 'title': ch.get('title', '未命名')[:200], 'content': ch.get('content', ''), 'updated': time.time()}
                save_json(os.path.join(ch_dir, f"{cid}.json"), ch_data)
                order.append(cid)
            title = book_title or os.path.splitext(fn)[0]
            meta = {'id': bid, 'title': title, 'created': time.time(), 'updated': time.time(), 'chapter_order': order, 'current_chapter_id': order[0] if order else ''}
            save_json(os.path.join(bd, 'meta.json'), meta)
            save_json(os.path.join(bd, 'outline.json'), dict(DEFAULT_OUTLINE))
            log_action('BUILTIN_IMPORT', f'{bid}: {len(chapters)} chapters from {fn}')
        except Exception as e:
            log_action('BUILTIN_IMPORT_ERR', f'{fn}: {str(e)[:200]}')


def get_salt():
    if os.path.exists(SALT_FILE):
        with open(SALT_FILE, 'r') as f:
            s = f.read().strip()
            if s: return s
    salt = secrets.token_hex(32)
    with open(SALT_FILE, 'w') as f: f.write(salt)
    return salt


_salt = get_salt()

_ENCRYPT_KEY_FILE = os.path.join(DATA_DIR, '.enckey')


def _get_encrypt_key():
    if os.path.exists(_ENCRYPT_KEY_FILE):
        with open(_ENCRYPT_KEY_FILE, 'rb') as f:
            return f.read()
    key = os.urandom(32)
    with open(_ENCRYPT_KEY_FILE, 'wb') as f:
        f.write(key)
    return key


_encrypt_key = _get_encrypt_key()


def _encrypt_str(plaintext):
    if not plaintext:
        return ''
    if HAS_CRYPTO:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(_encrypt_key)
        ct = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
        return 'ENC2:' + base64.b64encode(nonce + ct).decode()
    else:
        data = plaintext.encode('utf-8')
        result = bytes([data[i] ^ _encrypt_key[i % len(_encrypt_key)] for i in range(len(data))])
        return 'ENC:' + base64.b64encode(result).decode()


def _decrypt_str(ciphertext):
    if not ciphertext:
        return ciphertext
    if ciphertext.startswith('ENC2:'):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = base64.b64decode(ciphertext[5:])
            nonce, ct = raw[:12], raw[12:]
            aesgcm = AESGCM(_encrypt_key)
            return aesgcm.decrypt(nonce, ct, None).decode('utf-8')
        except Exception:
            return ciphertext
    if ciphertext.startswith('ENC:'):
        try:
            data = base64.b64decode(ciphertext[4:])
            result = bytes([data[i] ^ _encrypt_key[i % len(_encrypt_key)] for i in range(len(data))])
            return result.decode('utf-8')
        except Exception:
            return ciphertext
    return ciphertext


# Rate limiter (in-memory, per-IP)
_rate_limit_store = {}
_rate_limit_lock = threading.Lock()


def check_rate_limit(key, max_requests, window_seconds):
    now = time.time()
    with _rate_limit_lock:
        if key not in _rate_limit_store:
            _rate_limit_store[key] = []
        times = [t for t in _rate_limit_store[key] if now - t < window_seconds]
        if len(times) >= max_requests:
            return False
        times.append(now)
        _rate_limit_store[key] = times
        if len(_rate_limit_store) > 10000:
            stale = [k for k, v in _rate_limit_store.items() if not v or now - v[-1] > 300]
            for k in stale:
                del _rate_limit_store[k]
        return True


PW_HASH_ITERS = 200000

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

def _check_account_lockout(users, u):
    if u not in users:
        return False
    user = users[u]
    locked_until = user.get('locked_until', 0)
    if locked_until and time.time() < locked_until:
        return True
    return False

def _record_failed_attempt(users, u):
    if u not in users:
        return
    user = users[u]
    user['failed_attempts'] = user.get('failed_attempts', 0) + 1
    if user['failed_attempts'] >= MAX_LOGIN_ATTEMPTS:
        user['locked_until'] = time.time() + LOCKOUT_MINUTES * 60
        log_action('ACCOUNT_LOCKED', f'{u}: {user["failed_attempts"]} failed attempts')
    save_json(USERS_FILE, users)

def _reset_failed_attempts(users, u):
    if u not in users:
        return
    user = users[u]
    user.pop('failed_attempts', None)
    user.pop('locked_until', None)
    save_json(USERS_FILE, users)

def hash_password(pw):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt.encode(), PW_HASH_ITERS, dklen=32)
    return f'pbkdf2:{salt}:{dk.hex()}'

def verify_password(pw, stored):
    if not stored or not pw:
        return False
    if stored.startswith('pbkdf2:'):
        try:
            _, salt, expected = stored.split(':')
            dk = hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt.encode(), PW_HASH_ITERS, dklen=32)
            return hmac.compare_digest(dk.hex(), expected)
        except Exception:
            return False
    # 兼容旧版 SHA-256 哈希
    return hashlib.sha256((pw + _salt).encode()).hexdigest() == stored

def is_old_password_hash(stored):
    return not stored.startswith('pbkdf2:')


def load_json(path, default=dict):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        except:
            # 如果主文件损坏，尝试读取临时文件
            tmp = path + '.tmp'
            if os.path.exists(tmp):
                try:
                    with open(tmp, 'r', encoding='utf-8') as f: return json.load(f)
                except: return default()
            return default()
    return default()


_json_write_lock = threading.Lock()

def save_json(path, data):
    """所有 JSON 写入走 tmp + os.replace 原子替换，断电/崩溃不会留下半写文件。
    全局锁串行化所有写入；tmp 文件名加 pid/tid 后缀，避免崩溃进程残留与本进程内冲突。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _json_write_lock:
        tmp = f'{path}.tmp.{os.getpid()}.{threading.get_ident()}'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise


_log_lock = threading.Lock()

def log_action(action, details=''):
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(LOG_DIR, f'{today}.log')
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] {action}"
        if details: line += f" - {details}"
        with _log_lock:
            with open(log_file, 'a', encoding='utf-8') as f: f.write(line + "\n")
    except: pass


def is_valid_id(oid):
    if not oid or not isinstance(oid, str): return False
    if '..' in oid or '/' in oid or '\\' in oid: return False
    return bool(re.match(r'^[a-zA-Z0-9_\-]+$', oid))


_MD_TABLE_SEP_RE = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$')

def _strip_md_tables(text):
    """剥除 markdown 表格：分隔行整行删除，数据行把 | 替换成两个空格。"""
    if not text or '|' not in text:
        return text
    out = []
    for line in text.split('\n'):
        if _MD_TABLE_SEP_RE.match(line):
            continue
        if line.count('|') >= 2:
            out.append(re.sub(r'\s*\|\s*', '  ', line).strip())
        else:
            out.append(line)
    return '\n'.join(out)


def _clean_ai_text(text):
    """统一清洗 AI 输出：去 markdown 修饰字符 + 剥表格。"""
    if not text:
        return text
    return _strip_md_tables(re.sub(r'[#*`~]', '', text))


def _resolve_chapter_id(raw_id, chapter_order):
    """解析 AI 给的 chapter_id。AI 经常把'第N章'的 N 当 ID，做兜底映射。
    返回真实存在于 chapter_order 中的 ID，找不到返回 None。
    """
    if not raw_id:
        return None
    raw = str(raw_id).strip()
    order = list(chapter_order or [])
    if raw in order:
        return raw
    m = re.match(r'^第?\s*(\d+)\s*章?$', raw)
    if m:
        n = int(m.group(1))
        if 1 <= n <= len(order):
            return order[n - 1]
    return None


def _read_chapter_subagent(settings, chapter_title, chapter_content, fallback_temperature):
    """子代理：以客观第三人称阅读章节，返回结构化摘要，不代入 Luca 的身份或视角。"""
    if not chapter_content or not settings or not settings.get('base_url') or not settings.get('model'):
        return None
    max_chars = 10000
    text = chapter_content[:max_chars] if len(chapter_content) > max_chars else chapter_content
    prompt = f"""你是一位不带立场的第三方文本分析员。请阅读以下小说章节，只陈述原文明确写出的内容，不做文学批评、不推测作者意图、不代入角色视角。

章节标题：{chapter_title}

正文：
{text}

请输出结构化 JSON，不要代码块：
{{
  "summary": "200-400 字的事实摘要，只包含原文明确陈述的信息",
  "entities": [{{"name": "出现的人物/实体名", "type": "人物/物品/地点/势力/概念", "facts": ["原文明确提到的事实"]}}],
  "events": [{{"description": "章节中发生的具体事件"}}],
  "key_points": ["关键事实点（每条约 10-20 字）"]
}}"""
    msgs = [
        {'role': 'system', 'content': '你是客观的第三方文本分析员。只陈述原文明确写出的内容。不推测、不评价、不代入任何角色或作者视角。输出严格 JSON。'},
        {'role': 'user', 'content': prompt},
    ]
    result, _, err = call_ai_full(settings, msgs, 2000, 0.2, timeout=120)
    if err:
        return None
    return result.strip()


def is_safe_url(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ('http', 'https'): return False
        host = parsed.hostname
        if not host: return False
        if host.lower() in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
            return True
        try:
            ip = ip_address(host)
            if ip.is_loopback: return True
            if ip.is_multicast or ip.is_reserved: return False
            for n in [ip_network('10.0.0.0/8'), ip_network('172.16.0.0/12'),
                       ip_network('192.168.0.0/16'), ip_network('169.254.0.0/16')]:
                if ip in n: return True
        except ValueError: pass
        return True
    except: return False


def _clean_editor_font_id(value):
    return re.sub(r'[^A-Za-z0-9_-]', '', str(value or ''))[:80]


def _clean_editor_font_name(value):
    name = os.path.splitext(os.path.basename(str(value or '')))[0].strip()
    return (name or 'Custom Font')[:80]


def _normalize_editor_font_presets(presets):
    if not isinstance(presets, list):
        return []
    clean = []
    seen = set()
    fonts_root = os.path.normpath(USER_FONTS_DIR)
    for p in presets:
        if not isinstance(p, dict):
            continue
        fid = _clean_editor_font_id(p.get('id'))
        if not fid or fid in seen:
            continue
        file_name = os.path.basename(str(p.get('file') or ''))
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in FONT_EXTS:
            continue
        fp = os.path.normpath(os.path.join(USER_FONTS_DIR, file_name))
        if not fp.startswith(fonts_root) or not os.path.isfile(fp):
            continue
        content_type, fmt = FONT_EXTS[ext]
        family = 'LWUserFont_' + fid.replace('-', '_')
        clean.append({
            'id': fid,
            'name': _clean_editor_font_name(p.get('name') or file_name),
            'file': file_name,
            'family': family,
            'url': '/api/editor-fonts/' + quote(file_name),
            'format': fmt,
            'content_type': content_type,
        })
        seen.add(fid)
    return clean


def _looks_like_font(raw, ext):
    if not raw or len(raw) < 12:
        return False
    sig = raw[:4]
    if ext == '.otf':
        return sig == b'OTTO'
    if ext == '.ttf':
        return sig in (b'\x00\x01\x00\x00', b'true', b'typ1')
    return False


def get_settings():
    s = load_json(SETTINGS_FILE)
    changed = False
    for k, v in DEFAULT_SETTINGS.items():
        if k not in s: s[k] = v; changed = True
    if s.get('ai_auto_comment') is not True:
        s['ai_auto_comment'] = True
        changed = True
    # 从旧版 browser_enabled 迁移到 network_search
    if 'browser_enabled' in s and 'network_search' not in s:
        s['network_search'] = 'auto' if s['browser_enabled'] else 'off'
        del s['browser_enabled']
        changed = True
    if s.get('ai_temperature') is not None:
        s['ai_temperature'] = None
        changed = True
    try:
        fw = max(100, min(900, int(s.get('editor_font_weight') or 200)))
    except Exception:
        fw = 200
    if fw != s.get('editor_font_weight'):
        s['editor_font_weight'] = fw
        changed = True
    normalized_fonts = _normalize_editor_font_presets(s.get('editor_font_presets', []))
    if normalized_fonts != s.get('editor_font_presets', []):
        s['editor_font_presets'] = normalized_fonts
        changed = True
    selected_font = _clean_editor_font_id(s.get('editor_font_preset_id', ''))
    if selected_font and selected_font not in BUILTIN_EDITOR_FONT_IDS and not any(p.get('id') == selected_font for p in normalized_fonts):
        selected_font = ''
    if selected_font != s.get('editor_font_preset_id', ''):
        s['editor_font_preset_id'] = selected_font
        changed = True
    # 迁移：旧版本 max_tokens 默认 80，对长上下文+推理模型不够，自动提升
    if s.get('ai_max_tokens', 0) < 200:
        s['ai_max_tokens'] = 512; changed = True
    # 迁移/初始化 provider_presets —— 只补缺失的，不覆盖已有的
    presets = s.get('provider_presets', [])
    if not presets:
        # 全新用户：用默认预设
        old_url = s.get('base_url', '')
        old_key = s.get('api_key', '')
        old_model = s.get('model', '')
        presets = [dict(p) for p in DEFAULT_PROVIDER_PRESETS]
        if old_url or old_key or old_model:
            presets[0]['base_url'] = old_url or presets[0]['base_url']
            presets[0]['api_key'] = old_key
            presets[0]['model'] = old_model
        s['provider_presets'] = presets
        changed = True
    else:
        # 已有预设：只补缺失的默认预设（不覆盖用户已配置的）
        existing_names = {p.get('name', '') for p in presets}
        for dp in DEFAULT_PROVIDER_PRESETS:
            if dp['name'] not in existing_names:
                presets.append(dict(dp))
                changed = True
        # 迁移：把旧版 Ollama 预设替换为 DeepSeek
        for i, p in enumerate(presets):
            if p.get('name') == 'Ollama':
                p['name'] = 'DeepSeek'
                p['base_url'] = 'https://api.deepseek.com'
                p['model'] = 'deepseek-chat'
                changed = True
        # 迁移：旧版占位名 "预设4"/"预设5" → "自定义1"/"自定义2"
        for i, p in enumerate(presets):
            if p.get('name') == '预设4':
                p['name'] = '自定义1'
                changed = True
            elif p.get('name') == '预设5':
                p['name'] = '自定义2'
                changed = True
        # 迁移：确保本地 Llama.cpp 预设存在
        has_local = any('llama.cpp' in (p.get('name') or '').lower() for p in presets)
        if not has_local:
            presets.append({'name': '本地 Llama.cpp', 'base_url': _local_llm_base_url(), 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': '', 'context_length': 65536})
            changed = True
    # 自动同步本地 Llama.cpp 预设的 model 为检测到的第一个 gguf
    detected_model_path = _detect_local_model()
    detected_model_name = os.path.splitext(os.path.basename(detected_model_path))[0] if detected_model_path else ''
    for p in presets:
        if _is_local_llm_preset(p):
            if _normalize_local_llm_preset(p):
                changed = True
            if detected_model_name and p.get('model') != detected_model_name:
                p['model'] = detected_model_name
                changed = True
            break
    # 确保 active_provider_idx 有效
    idx = s.get('active_provider_idx', 0)
    if idx < 0 or idx >= len(presets):
        idx = 0
        s['active_provider_idx'] = idx
        changed = True
    if changed: save_json(SETTINGS_FILE, s)
    # 解密所有预设的 api_key
    for p in presets:
        if p.get('api_key'):
            p['api_key'] = _decrypt_str(p['api_key'])
    # 解密顶层 api_key
    if s.get('api_key'):
        s['api_key'] = _decrypt_str(s['api_key'])
    # 解密 search_api_key
    if s.get('search_api_key'):
        s['search_api_key'] = _decrypt_str(s['search_api_key'])
    # 将当前激活 preset 的字段提升到顶层，保持向后兼容
    active = presets[idx]
    if active.get('use_custom_json') and active.get('custom_json'):
        try:
            custom = json.loads(active['custom_json'])
            if isinstance(custom, dict):
                s['base_url'] = custom.get('base_url', active.get('base_url', ''))
                s['api_key'] = custom.get('api_key', active.get('api_key', ''))
                s['model'] = custom.get('model', active.get('model', ''))
            else:
                s['base_url'] = active.get('base_url', '')
                s['api_key'] = active.get('api_key', '')
                s['model'] = active.get('model', '')
        except:
            s['base_url'] = active.get('base_url', '')
            s['api_key'] = active.get('api_key', '')
            s['model'] = active.get('model', '')
    else:
        s['base_url'] = active.get('base_url', '')
        s['api_key'] = active.get('api_key', '')
        s['model'] = active.get('model', '')
    return s


def _save_settings_with_encrypted_keys(settings):
    save_settings = json.loads(json.dumps(settings))
    save_presets = list(save_settings.get('provider_presets', []))
    for p in save_presets:
        if p.get('api_key'):
            p['api_key'] = _encrypt_str(p['api_key'])
    save_settings['provider_presets'] = save_presets
    if save_settings.get('api_key'):
        save_settings['api_key'] = _encrypt_str(save_settings['api_key'])
    if save_settings.get('search_api_key'):
        save_settings['search_api_key'] = _encrypt_str(save_settings['search_api_key'])
    save_json(SETTINGS_FILE, save_settings)


def _activate_local_llm_provider():
    settings = get_settings()
    presets = settings.get('provider_presets') or []
    local_idx = -1
    for i, p in enumerate(presets):
        if _is_local_llm_preset(p):
            _normalize_local_llm_preset(p)
            local_idx = i
            break
    if local_idx < 0:
        presets.append({'name': '本地 Llama.cpp', 'base_url': _local_llm_base_url(), 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': '', 'context_length': 65536})
        local_idx = len(presets) - 1
    settings['provider_presets'] = presets
    settings['active_provider_idx'] = local_idx
    active = presets[local_idx]
    settings['base_url'] = active.get('base_url', '')
    settings['api_key'] = ''
    settings['model'] = active.get('model', '')
    _save_settings_with_encrypted_keys(settings)
    return settings


def _make_cover_svg(title):
    safe_title = _html_mod.escape(title or '未命名')
    # 智能换行：中文按字、英文按词
    lines = []
    current = ''
    for ch in safe_title:
        if ch == ' ' and len(current) >= 2:
            lines.append(current)
            current = ''
        else:
            current += ch
    if current:
        lines.append(current)
    if not lines:
        lines = [safe_title]

    W, H = 600, 800
    BORDER = 16
    MAX_W = W - BORDER * 2  # 568px
    MAX_H = H - BORDER * 2  # 768px
    FONT_FAMILY = '-apple-system,BlinkMacSystemFont,Noto Sans SC,sans-serif'

    # 二分找出不爆框的最大字号
    lo, hi = 8, 200
    best_size = 8
    while lo <= hi:
        mid = (lo + hi) // 2
        # 估算每行宽度和总高度
        ok = True
        total_h = 0
        for i, ln in enumerate(lines):
            # 粗略宽度：CJK 字宽≈字号，英文≈0.55*字号
            w_est = 0
            for ch in ln:
                if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef':
                    w_est += mid
                else:
                    w_est += mid * 0.55
            if w_est > MAX_W:
                # 需要换行
                # Do a smarter wrap
                pass  # fall through to overflow check
            # Simple check: estimate line height and total
            if w_est > MAX_W and len(ln) == 1 and ord(ln[0]) > 127:
                ok = False
                break
            # For rough estimate: treat each line as 1.2*font_size height
            wraps = max(1, int(w_est / MAX_W) + (1 if w_est % MAX_W > 0 else 0))
            total_h += wraps * mid * 1.25
        if not ok or total_h > MAX_H:
            hi = mid - 1
        else:
            best_size = mid
            lo = mid + 1

    # 用最佳字号做实际排版
    font_size = max(14, best_size)
    line_height = int(font_size * 1.25)

    # 实际排版：逐行计算，长行自动折行
    rendered_lines = []
    for ln in lines:
        if not ln.strip():
            rendered_lines.append('')
            continue
        words = ''
        for ch in ln:
            w = font_size if ('\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef') else font_size * 0.55
            if _est_text_width(words + ch, font_size) > MAX_W and words:
                rendered_lines.append(words.rstrip())
                words = ch
            else:
                words += ch
        if words:
            rendered_lines.append(words.rstrip())

    total_h = len(rendered_lines) * line_height
    start_y = (H - total_h) // 2 + int(font_size * 0.88)

    tspans = ''
    for i, ln in enumerate(rendered_lines):
        y = start_y + i * line_height
        tspans += f'<tspan x="{W//2}" y="{y}" text-anchor="middle">{ln}</tspan>'

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#383838"/>
      <stop offset="100%" stop-color="#2a2a2a"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <rect x="{BORDER}" y="{BORDER}" width="{W - BORDER*2}" height="{H - BORDER*2}" rx="8" fill="none" stroke="#ffffff" stroke-opacity="0.1" stroke-width="1"/>
  <text fill="#ffffff" fill-opacity="0.95" font-family="{FONT_FAMILY}" font-weight="600" font-size="{font_size}px" letter-spacing="1">
    {tspans}
  </text>
  <text x="{W//2}" y="{H - 30}" text-anchor="middle" fill="#999999" font-size="11" font-family="-apple-system,sans-serif">LucaWriter</text>
</svg>'''


def _est_text_width(text, font_size):
    w = 0
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef':
            w += font_size
        else:
            w += font_size * 0.55
    return w

def _make_series_cover_svg(title, book_count):
    safe_title = _html_mod.escape(title or '未命名')
    # 智能换行：中文按字、英文按词
    lines = []
    current = ''
    for ch in safe_title:
        if ch == ' ' and len(current) >= 2:
            lines.append(current)
            current = ''
        else:
            current += ch
    if current:
        lines.append(current)
    if not lines:
        lines = [safe_title]

    W, H = 600, 800
    BORDER = 20
    MAX_W = W - BORDER * 2
    MAX_H = H - BORDER * 2 - 60  # 顶部留空给装饰线
    FONT_FAMILY = '-apple-system,BlinkMacSystemFont,Noto Sans SC,sans-serif'

    lo, hi = 8, 200
    best_size = 8
    while lo <= hi:
        mid = (lo + hi) // 2
        ok = True
        total_h = 0
        for ln in lines:
            w_est = 0
            for ch in ln:
                if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef':
                    w_est += mid
                else:
                    w_est += mid * 0.55
            if w_est > MAX_W and len(ln) == 1 and ord(ln[0]) > 127:
                ok = False
                break
            wraps = max(1, int(w_est / MAX_W) + (1 if w_est % MAX_W > 0 else 0))
            total_h += wraps * mid * 1.25
        if not ok or total_h > MAX_H:
            hi = mid - 1
        else:
            best_size = mid
            lo = mid + 1

    font_size = max(14, best_size)
    line_height = int(font_size * 1.25)

    rendered_lines = []
    for ln in lines:
        if not ln.strip():
            rendered_lines.append('')
            continue
        words = ''
        for ch in ln:
            w = font_size if ('\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef') else font_size * 0.55
            if _est_text_width(words + ch, font_size) > MAX_W and words:
                rendered_lines.append(words.rstrip())
                words = ch
            else:
                words += ch
        if words:
            rendered_lines.append(words.rstrip())

    total_h = len(rendered_lines) * line_height
    start_y = (H - total_h) // 2 + int(font_size * 0.88) + 20

    tspans = ''
    for i, ln in enumerate(rendered_lines):
        y = start_y + i * line_height
        tspans += f'<tspan x="{W//2}" y="{y}" text-anchor="middle">{ln}</tspan>'

    count_text = f'系列 · {book_count} 本'

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#383838"/>
      <stop offset="100%" stop-color="#2a2a2a"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <line x1="{BORDER + 20}" y1="{BORDER + 12}" x2="{W - BORDER - 20}" y2="{BORDER + 12}" stroke="#ffffff" stroke-opacity="0.1" stroke-width="2"/>
  <rect x="{BORDER + 20}" y="{BORDER + 12}" width="{W - (BORDER + 20)*2}" height="{H - (BORDER + 12)*2}" rx="6" fill="none" stroke="#ffffff" stroke-opacity="0.07" stroke-width="1"/>
  <text fill="#ffffff" fill-opacity="0.95" font-family="{FONT_FAMILY}" font-weight="600" font-size="{font_size}px" letter-spacing="1">
    {tspans}
  </text>
  <text x="{W//2}" y="{H - 30}" text-anchor="middle" fill="#999999" font-size="11" font-family="-apple-system,sans-serif">{count_text}</text>
</svg>'''

def get_book_dir(book_id):
    return os.path.join(BOOKS_DIR, book_id)


def get_book_meta(book_id):
    p = os.path.join(get_book_dir(book_id), 'meta.json')
    return load_json(p) if os.path.exists(p) else None


def list_chapter_files(book_id):
    d = os.path.join(get_book_dir(book_id), 'chapters')
    if not os.path.isdir(d): return []
    return sorted([f for f in os.listdir(d) if f.endswith('.json') and not f.startswith('.')],
                   key=lambda f: os.path.getmtime(os.path.join(d, f)))


def get_outline(book_id):
    p = os.path.join(get_book_dir(book_id), 'outline.json')
    o = load_json(p)
    changed = False
    for k, v in DEFAULT_OUTLINE.items():
        if k not in o: o[k] = v; changed = True
    if changed: save_json(p, o)
    return o


def get_core_memory(book_id):
    p = os.path.join(get_book_dir(book_id), 'core_memory.md')
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return f.read()
    return ''


def save_core_memory(book_id, content):
    p = os.path.join(get_book_dir(book_id), 'core_memory.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)


def get_chapter_summary(book_id, chapter_id):
    p = os.path.join(get_book_dir(book_id), 'chapter_summaries', f"{chapter_id}.md")
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return f.read()
    return ''


def save_chapter_summary(book_id, chapter_id, content):
    d = os.path.join(get_book_dir(book_id), 'chapter_summaries')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{chapter_id}.md")
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)


def get_volume_summaries(book_id):
    d = os.path.join(get_book_dir(book_id), 'volume_summaries')
    if not os.path.isdir(d): return {}
    result = {}
    for fn in sorted(os.listdir(d)):
        if fn.endswith('.md'):
            with open(os.path.join(d, fn), 'r', encoding='utf-8') as f:
                result[fn.replace('.md', '')] = f.read()
    return result


def build_pyramid_context(book_id):
    core = get_core_memory(book_id) or '（尚无核心记忆）'
    vols = get_volume_summaries(book_id)
    vol_text = ''
    for k, v in vols.items():
        vol_text += f'\n## {k}\n{v}'
    meta = get_book_meta(book_id) or {}
    order = meta.get('chapter_order', [])
    recent_ch = ''
    for cid in order[-3:]:
        cs = get_chapter_summary(book_id, cid)
        if cs:
            recent_ch += f'\n--- 章节摘要 ---\n{cs}'
    return f"""【全书核心记忆】
{core}

【卷级脉络】
{vol_text}

【最近章节摘要】
{recent_ch}
"""


def has_users():
    return bool(load_json(USERS_FILE))


def _get_user_book_titles():
    """获取用户创建的所有书籍标题（排除内置示例书）"""
    titles = []
    if not os.path.isdir(BOOKS_DIR):
        return titles
    for bid in os.listdir(BOOKS_DIR):
        bd = os.path.join(BOOKS_DIR, bid)
        if not os.path.isdir(bd):
            continue
        # 跳过内置示例书
        if bid.startswith('builtin_'):
            continue
        meta_file = os.path.join(bd, 'meta.json')
        if os.path.exists(meta_file):
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                    title = meta.get('title', '')
                    if title:
                        titles.append(title)
            except:
                pass
    return titles


def _build_bookshelf_tree():
    """构建书库目录树字符串，类似 GitHub 项目结构图。"""
    if not os.path.isdir(BOOKS_DIR):
        return '（书库为空）'
    standalone = []
    series_list = []
    # 第一遍：区分系列和独立书本
    for bid in os.listdir(BOOKS_DIR):
        bd = os.path.join(BOOKS_DIR, bid)
        if not os.path.isdir(bd) or bid.startswith('builtin_'):
            continue
        meta = load_json(os.path.join(bd, 'meta.json'), dict)
        if not meta:
            continue
        if meta.get('type') == 'series':
            series_list.append({'id': bid, 'title': meta.get('title', '未命名'), 'books': []})
        else:
            order = meta.get('chapter_order', []) or []
            chapters = []
            for cid in order:
                cp = os.path.join(bd, 'chapters', f'{cid}.json')
                if os.path.exists(cp):
                    cd = load_json(cp, dict)
                    chapters.append(cd.get('title', '') or '')
            standalone.append({'id': bid, 'title': meta.get('title', '未命名'), 'chapter_count': len(chapters), 'chapters': chapters})
    # 第二遍：把书本归入系列
    for s in series_list:
        sm = load_json(os.path.join(BOOKS_DIR, s['id'], 'meta.json'), dict)
        sids = sm.get('series_books', []) or []
        for sid in sids:
            sb_meta = get_book_meta(sid)
            if sb_meta:
                sb_title = sb_meta.get('title', '未命名')
                sb_order = sb_meta.get('chapter_order', []) or []
                sb_ch = []
                sb_bd = get_book_dir(sid)
                for cid in sb_order:
                    cp = os.path.join(sb_bd, 'chapters', f'{cid}.json')
                    if os.path.exists(cp):
                        cd = load_json(cp, dict)
                        sb_ch.append(cd.get('title', '') or '')
                s['books'].append({'title': sb_title, 'chapter_count': len(sb_ch), 'chapters': sb_ch})
                standalone[:] = [b for b in standalone if b['id'] != sid]
    if not series_list and not standalone:
        return '（书库为空）'
    lines = ['[书库]']
    all_items = series_list + standalone
    for idx, item in enumerate(all_items):
        is_last = idx == len(all_items) - 1
        prefix = '└── ' if is_last else '├── '
        if 'books' in item:
            # 系列
            lines.append(f'{prefix}[系列] {item["title"]}')
            for bi, b in enumerate(item['books']):
                b_prefix = ('    ' if is_last else '│   ') + ('└── ' if bi == len(item['books']) - 1 else '├── ')
                lines.append(f'{b_prefix}{b["title"]}（{b["chapter_count"]}章）')
                for ci, ch in enumerate(b['chapters']):
                    c_prefix = ('    ' if is_last else '│   ') + ('    ' if bi == len(item['books']) - 1 else '│   ') + ('└── ' if ci == len(b['chapters']) - 1 else '├── ')
                    lines.append(f'{c_prefix}{ch or "未命名"}')
        else:
            # 独立书本
            lines.append(f'{prefix}{item["title"]}（{item["chapter_count"]}章）')
            for ci, ch in enumerate(item['chapters']):
                c_prefix = ('    ' if is_last else '│   ') + ('└── ' if ci == len(item['chapters']) - 1 else '├── ')
                lines.append(f'{c_prefix}{ch or "未命名"}')
    return '\n'.join(lines)


def _verify_book_title_in_terminal():
    """在终端中验证用户输入的书名，返回 (success, message)"""
    titles = _get_user_book_titles()
    if not titles:
        return False, '书库中没有用户创建的书籍，无法验证身份'

    print('\n' + '='*60)
    print('密码重置请求')
    print('='*60)
    print('请输入书库中任意一本书的完整书名以确认身份：')
    print('（输入错误或留空将取消重置）')
    print('-'*60)
    for i, t in enumerate(titles[:10], 1):
        print(f'  {i}. {t}')
    if len(titles) > 10:
        print(f'  ... 还有 {len(titles) - 10} 本书')
    print('='*60)

    try:
        user_input = input('\n书名: ').strip()
    except EOFError:
        return False, '无法读取输入'

    if not user_input:
        return False, '未输入书名，已取消重置'

    if user_input in titles:
        return True, '验证通过'
    else:
        return False, f'书名不匹配。您输入的是: {user_input}'


def _is_electron_mode():
    """检查是否在 Electron 桌面版环境中运行"""
    return bool(os.environ.get('DATA_DIR'))


def validate_session(token):
    if not token: return False
    sessions = load_json(SESSIONS_FILE, list)
    now = time.time()
    for s in sessions:
        if hmac.compare_digest(s.get('token', ''), token) and s.get('expires', 0) > now:
            # Sliding expiration: extend if more than halfway expired
            created = s.get('created', 0)
            if created > 0:
                lifetime = s['expires'] - created
                if lifetime > 0 and (s['expires'] - now) < lifetime * 0.5:
                    s['expires'] = now + lifetime
                    save_json(SESSIONS_FILE, sessions)
            return True
    return False


def get_cookie_token(headers):
    c = headers.get('Cookie', '')
    if not c: return None
    cookie = SimpleCookie()
    try: cookie.load(c)
    except: return None
    return cookie['session'].value if 'session' in cookie else None


def make_session(username, remember=False, device_name=''):
    token = secrets.token_hex(32)
    sessions = load_json(SESSIONS_FILE, list)
    sessions = [s for s in sessions if s.get('expires', 0) > time.time()]
    now = time.time()
    if remember:
        sessions.append({'token': token, 'user': username, 'created': now, 'expires': now + 86400 * 90, 'device_name': device_name})
    else:
        sessions.append({'token': token, 'user': username, 'created': now, 'expires': now + 86400, 'device_name': device_name})
    save_json(SESSIONS_FILE, sessions)
    return token


_V0_MIGRATION_MARKER = os.path.join(DATA_DIR, '.migration_v0_done')


def migrate_old_data():
    # v0.x → v1.x 一次性迁移；marker 存在就跳过，避免重复扫描误吞 ai_providers.json / local_strategy.json
    # 等运行时生成的 dict 文件，产生空"我的小说"幽灵书
    if os.path.exists(_V0_MIGRATION_MARKER):
        return
    old = []
    for f in glob.glob(os.path.join(DATA_DIR, '*.json')):
        fn = os.path.basename(f)
        name = fn.replace('.json', '')
        if name.startswith('.') or name in RESERVED_FILES: continue
        try:
            with open(f, 'r', encoding='utf-8') as fp: ch = json.load(fp)
        except: continue
        # 只接受形状像 v0.x 章节的 dict：必须同时有 id/title/content 三个字符串字段
        if not isinstance(ch, dict): continue
        if not (isinstance(ch.get('id'), str) and isinstance(ch.get('title'), str) and isinstance(ch.get('content'), str)):
            continue
        ch['_old_file'] = f
        old.append(ch)
    if not old:
        try:
            with open(_V0_MIGRATION_MARKER, 'w', encoding='utf-8') as mf: mf.write(str(time.time()))
        except: pass
        return
    bid = 'book_' + str(int(time.time()))
    bd = get_book_dir(bid)
    cd = os.path.join(bd, 'chapters')
    os.makedirs(cd, exist_ok=True)
    os.makedirs(os.path.join(bd, 'trash'), exist_ok=True)
    order = [ch.get('id', f'ch_{i}') for i, ch in enumerate(old)]
    save_json(os.path.join(bd, 'meta.json'), {'id': bid, 'title': '我的小说', 'created': time.time(), 'updated': time.time(), 'chapter_order': order})
    for ch in old:
        cid = ch.get('id', 'ch_migrated')
        save_json(os.path.join(cd, f"{cid}.json"), {'id': cid, 'title': ch.get('title', ''), 'content': ch.get('content', ''), 'updated': ch.get('updated', time.time())})
        of = ch.get('_old_file')
        if of and os.path.exists(of):
            try: os.remove(of)
            except: pass
    save_json(os.path.join(bd, 'outline.json'), dict(DEFAULT_OUTLINE))
    log_action('MIGRATE', f'{len(old)} chapters to {bid}')
    try:
        with open(_V0_MIGRATION_MARKER, 'w', encoding='utf-8') as mf: mf.write(str(time.time()))
    except: pass


migrate_old_data()


_CHAPTER_SPLIT_RE = re.compile(r'^\s*(?:第[一二三四五六七八九十百千万\d]+[章回节]|Chapter\s+\d+|CHAPTER\s+\d+)')
_MD_CHAPTER_RE = re.compile(r'^##\s+')

def parse_txt(text, filename):
    chapters = []
    lines = text.split('\n')
    # 检测是否有章节标题
    has_chapters = any(_CHAPTER_SPLIT_RE.match(l) for l in lines[:5000])
    if not has_chapters or len(lines) < 10:
        return [{'title': filename.replace('.txt', ''), 'content': text.strip()}]
    current_title = filename.replace('.txt', '')
    current_lines = []
    for line in lines:
        if _CHAPTER_SPLIT_RE.match(line):
            if current_lines:
                chapters.append({'title': current_title, 'content': '\n'.join(current_lines).strip()})
            current_title = line.strip()[:100]
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        chapters.append({'title': current_title, 'content': '\n'.join(current_lines).strip()})
    return chapters


def parse_md(text, filename):
    chapters = []
    lines = text.split('\n')
    # Extract book title from first heading if available
    book_title = ''
    for line in lines[:50]:
        line_stripped = line.strip()
        if line_stripped.startswith('# ') and not line_stripped.startswith('## '):
            book_title = line_stripped.lstrip('#').strip()
            break
    has_chapters = any(_MD_CHAPTER_RE.match(l) for l in lines[:5000])
    if not has_chapters or len(lines) < 10:
        return [{'title': book_title or filename.replace('.md', ''), 'content': text.strip()}], book_title
    current_title = book_title or filename.replace('.md', '')
    current_lines = []
    for line in lines:
        if _MD_CHAPTER_RE.match(line):
            if current_lines:
                chapters.append({'title': current_title, 'content': '\n'.join(current_lines).strip()})
            current_title = line.lstrip('#').strip()[:100]
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        chapters.append({'title': current_title, 'content': '\n'.join(current_lines).strip()})
    return chapters, book_title


def parse_docx_bytes(raw, filename):
    try:
        with zipfile.ZipFile(io.BytesIO(raw), 'r') as zf:
            _validate_zip_archive(zf, max_total=120 * _MB, max_entry=60 * _MB)
            info = zf.getinfo('word/document.xml')
            if info.file_size > DOCX_MAX_XML_BYTES:
                return None, f'DOCX 正文超过 {DOCX_MAX_XML_BYTES // _MB}MB 安全上限'
    except zipfile.BadZipFile:
        return None, 'DOCX 文件不是有效压缩包'
    except KeyError:
        return None, 'DOCX 缺少 word/document.xml'
    except Exception as e:
        return None, f'DOCX 安全检查失败: {str(e)[:80]}'
    if HAS_DOCX:
        try:
            doc = docx_mod.Document(io.BytesIO(raw))
            chapters = []
            current_title = filename.replace('.docx', '')
            current_parts = []
            for p in doc.paragraphs:
                if p.style and p.style.name and 'Heading' in p.style.name and current_parts:
                    chapters.append({'title': current_title, 'content': '\n\n'.join(current_parts)})
                    current_title = p.text.strip()[:100]
                    current_parts = []
                elif p.text.strip():
                    current_parts.append(p.text.strip())
            if current_parts:
                chapters.append({'title': current_title, 'content': '\n\n'.join(current_parts)})
            return chapters, None
        except Exception as e:
            log_action('DOCX_PARSE_ERROR', str(e)[:200])
    try:
        with zipfile.ZipFile(io.BytesIO(raw), 'r') as zf:
            # 读取前先按解压后大小设限，防止 DOCX 压缩炸弹耗尽内存
            info = zf.getinfo('word/document.xml')
            if info.file_size > DOCX_MAX_XML_BYTES:
                # 超限文件不截断导入，直接提示用户拆分
                return None, f'DOCX 正文超过 {DOCX_MAX_XML_BYTES // _MB}MB 安全上限'
            else:
                xml = _zip_read_limited(zf, 'word/document.xml', DOCX_MAX_XML_BYTES).decode('utf-8', errors='ignore')
        # 按段落提取文本，避免 ElementTree 对大 XML 的内存开销
        paras = []
        for m in re.finditer(r'<w:p\b[^>]*>(.*?)</w:p>', xml, re.S):
            p_xml = m.group(1)
            parts = []
            for tm in re.finditer(r'<w:t[^>]*>([^<]*)</w:t>', p_xml):
                t = tm.group(1)
                if t: parts.append(t)
            if parts:
                paras.append(''.join(parts))
        content = '\n\n'.join(paras)
        if not content:
            return None, 'DOCX 内容为空'
        return [{'title': filename.replace('.docx', ''), 'content': content}], None
    except Exception as e:
        log_action('DOCX_PARSE_ERROR', str(e)[:200])
        return None, f'DOCX解析失败: {str(e)[:80]}'


def parse_pdf_bytes(raw, filename):
    if not HAS_PDF: return None, '需安装 PyPDF2（pip install PyPDF2）'
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = reader.pages[:500]  # 限制 500 页，防止超大 PDF 卡死
        texts = []
        for page in pages:
            try:
                t = page.extract_text()
                if t: texts.append(t)
            except:
                continue
        text = '\n\n'.join(texts)
        return parse_txt(text, filename), None
    except Exception as e:
        log_action('PDF_PARSE_ERROR', str(e)[:200])
        return None, f'PDF解析失败: {str(e)[:80]}'


_TAG_RE = re.compile(r'<[^>]+>', re.S)
_SCRIPT_RE = re.compile(r'<script[^>]*>.*?</script>', re.S|re.I)
_STYLE_RE = re.compile(r'<style[^>]*>.*?</style>', re.S|re.I)
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.S|re.I)
_H1_RE = re.compile(r'<h[12][^>]*>(.*?)</h[12]>', re.S|re.I)

_BLOCK_TAG_RE = re.compile(r'</?(?:p|div|h[1-6]|li|tr|section|article|blockquote|pre|address)[^>]*>', re.S|re.I)
_BR_RE = re.compile(r'<br\s*/?>', re.S|re.I)

def _strip_tags(html):
    """轻量 HTML 到纯文本，保留段落换行"""
    text = _SCRIPT_RE.sub(' ', html)
    text = _STYLE_RE.sub(' ', text)
    # 块级标签和 <br> 替换为换行
    text = _BR_RE.sub('\n', text)
    text = _BLOCK_TAG_RE.sub('\n', text)
    text = _TAG_RE.sub(' ', text)
    # 合并连续空格，但保留换行；再把连续3个以上换行压缩为2个
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        line = re.sub(r'[ \t]+', ' ', line).strip()
        if line:
            cleaned.append(line)
    return '\n\n'.join(cleaned)

def _is_internal_url(url):
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return True
        if host.lower() in ('localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'):
            return True
        try:
            ip = ip_address(host)
            if ip.is_loopback or ip.is_private or ip.is_reserved or ip.is_link_local:
                return True
        except ValueError:
            pass
        return False
    except Exception:
        return True


def _validate_zip_archive(zf, max_entries=ZIP_MAX_ENTRIES, max_total=ZIP_MAX_TOTAL_BYTES, max_entry=ZIP_MAX_ENTRY_BYTES):
    infos = zf.infolist()
    if len(infos) > max_entries:
        raise ValueError(f'压缩包文件数量超过 {max_entries} 个')
    total = 0
    for info in infos:
        if info.is_dir():
            continue
        if info.file_size > max_entry:
            raise ValueError(f'压缩包内文件过大: {info.filename}')
        total += info.file_size
        if total > max_total:
            raise ValueError(f'压缩包解压后超过 {max_total // _MB}MB 上限')


def _zip_read_limited(zf, name, max_bytes):
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError(f'压缩包内文件过大: {name}')
    out = bytearray()
    with zf.open(name) as src:
        while True:
            chunk = src.read(ZIP_READ_CHUNK)
            if not chunk:
                break
            out.extend(chunk)
            if len(out) > max_bytes:
                raise ValueError(f'压缩包内文件解压超过限制: {name}')
    return bytes(out)


def _fetch_url_content(url, max_chars=8000):
    """抓取网页并提取纯文本"""
    try:
        if not url.startswith(('http://', 'https://')):
            return 'URL格式不支持，只支持http/https'
        if _is_internal_url(url):
            return '不允许访问内网地址'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        with urllib.request.urlopen(req, timeout=15, context=_get_ssl_context()) as resp:
            raw = resp.read()
            charset = 'utf-8'
            ct = resp.headers.get('Content-Type', '')
            m = re.search(r'charset=([^\s;]+)', ct, re.I)
            if m:
                charset = m.group(1).strip().strip('"').strip("'")
            try:
                html = raw.decode(charset, errors='ignore')
            except:
                html = raw.decode('utf-8', errors='ignore')
            text = _strip_tags(html)
            if len(text) > max_chars:
                text = text[:max_chars] + '\n\n[内容已截断，后续省略]'
            return text
    except Exception as e:
        return f'抓取失败: {str(e)[:200]}'

def _search_web(query, max_results=5):
    """搜索网页，返回 {title, link, snippet} 列表。
    优先使用用户在设置中配置的搜索 API（Brave Search），否则回退到 DuckDuckGo Lite。"""
    settings = get_settings()
    api_key = settings.get('search_api_key', '').strip()
    provider = settings.get('search_provider', 'duckduckgo')

    # 1) 如果配置了 Brave Search API Key，优先使用
    if api_key and provider == 'brave':
        try:
            q = urllib.parse.quote(query)
            url = f'https://api.search.brave.com/res/v1/web/search?q={q}&count={max_results}&text_decorations=0'
            req = urllib.request.Request(url, headers={
                'X-Subscription-Token': api_key,
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=15, context=_get_ssl_context()) as resp:
                data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                results = []
                for r in data.get('web', {}).get('results', [])[:max_results]:
                    results.append({
                        'title': r.get('title', ''),
                        'link': r.get('url', ''),
                        'snippet': r.get('description', '')
                    })
                if results:
                    return results
        except Exception as e:
            log_action('SEARCH_BRAVE_ERROR', str(e)[:200])
            # fallthrough to DuckDuckGo

    # 2) 回退到 DuckDuckGo Lite
    try:
        q = urllib.parse.quote(query)
        url = f'https://lite.duckduckgo.com/lite/?q={q}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        with urllib.request.urlopen(req, timeout=15, context=_get_ssl_context()) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            results = []
            # 模式 A：DuckDuckGo Lite 经典结构
            link_matches = re.findall(
                r'<a rel="nofollow" href="([^"]+)" class=\'result-link\'>(.*?)</a>',
                html, re.S
            )
            snippets = re.findall(
                r'<td class=\'result-snippet\'>\s*(.*?)\s*</td>',
                html, re.S
            )
            # 模式 B：如果 Lite 结构变了，尝试更通用的匹配
            if not link_matches:
                link_matches = re.findall(
                    r'<a[^>]+class=["\']result-link["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    html, re.S
                ) or re.findall(
                    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\']result-link["\'][^>]*>(.*?)</a>',
                    html, re.S
                )
            if not snippets:
                snippets = re.findall(
                    r'<td[^>]+class=["\']result-snippet["\'][^>]*>(.*?)</td>',
                    html, re.S
                )
            for i, (href, title_html) in enumerate(link_matches):
                if i >= max_results:
                    break
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                real_url = ''
                if 'uddg=' in href:
                    m = re.search(r'uddg=([^&]+)', href)
                    if m:
                        real_url = urllib.parse.unquote(m.group(1))
                elif href.startswith('http'):
                    real_url = href
                else:
                    real_url = 'https:' + href if href.startswith('//') else href
                snippet = ''
                if i < len(snippets):
                    snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                results.append({'title': title, 'link': real_url, 'snippet': snippet})
            if results:
                return results
            # 模式 C：如果 Lite 完全没结果，尝试解析 HTML 版 DuckDuckGo 的简化结果
            fallback_links = re.findall(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\']result__a["\'][^>]*>(.*?)</a>',
                html, re.S
            )
            fallback_snippets = re.findall(
                r'<a[^>]+class=["\']result__snippet["\'][^>]*>(.*?)</a>',
                html, re.S
            )
            for i, (href, title_html) in enumerate(fallback_links):
                if i >= max_results:
                    break
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                real_url = href if href.startswith('http') else ''
                snippet = ''
                if i < len(fallback_snippets):
                    snippet = re.sub(r'<[^>]+>', '', fallback_snippets[i]).strip()
                results.append({'title': title, 'link': real_url, 'snippet': snippet})
            return results
    except Exception as e:
        return [{'title': '搜索失败', 'link': '', 'snippet': str(e)[:200]}]

def _extract_title(html):
    m = _H1_RE.search(html)
    if m:
        return _TAG_RE.sub('', m.group(1)).strip()
    m = _TITLE_RE.search(html)
    if m:
        return _TAG_RE.sub('', m.group(1)).strip()
    return ''

def _extract_html_heading(html):
    """从HTML中提取可能的章节标题（优先h1/h2/h3，不依赖<title>）"""
    for tag in ['h1', 'h2', 'h3']:
        m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.S|re.I)
        if m:
            text = _TAG_RE.sub('', m.group(1)).strip()
            if text and len(text) < 200 and text.lower() not in ('cover', 'bookcover', '封面', '标题', 'title', 'contents', '目录'):
                return text
    return ''

_PHOTO_RE = re.compile(r'照片|图片|插图|图\s*\d|photo|picture|image|caption|fig\.?\s*\d', re.I)

def _guess_chapter_from_text(text):
    """从正文开头猜测章节名"""
    lines = text.split('\n')
    for i, line in enumerate(lines[:25]):
        line = line.strip()
        if not line:
            continue
        # 常见中文章节格式（含轻小说/剧本：话/幕/折）
        if re.match(r'^第[一二三四五六七八九十百千零\d]+[章回节卷篇部集话幕折辑]', line):
            return line[:100]
        # 序/引/楔/终/跋/后记/前言/序言/尾声/引子
        if re.match(r'^[序前引终楔跋][章曲言子]$', line):
            return line[:100]
        if line in ('引子', '楔子', '尾声', '后记', '前言', '序言', '终章', '跋', '序章', '引言', '前传', '外传', '番外', '彩页', '插图'):
            return line[:100]
        # 括号编号章节：（一）xxx、（1）xxx
        m = re.match(r'^（[一二三四五六七八九十百千零\d]+）[\.、:：]?\s*(.+)', line)
        if m and len(line) < 60:
            return line[:100]
        # 数字/中文数字+点/顿号，且不像图片说明
        m = re.match(r'^[\d一二三四五六七八九十]+[\.、]\s*(.+)', line)
        if m and len(line) < 50:
            tail = m.group(1)
            # 排除明显是图注的内容
            if not _PHOTO_RE.search(tail) and len(tail) > 1:
                return line[:100]
        # 英文 Part / Chapter / Book / Section
        if re.match(r'^(Part|Chapter|Book|Section)\s+[\dIVX]+', line, re.I):
            return line[:100]
        # 英文数字+空格+大写标题（如 "1 THE SEARCH BEGINS"），排除大数字（如地址 175 Fifth Ave）
        m = re.match(r'^(\d+)\s+[A-Z]', line)
        if m and int(m.group(1)) <= 99 and len(line) < 100:
            return line[:100]
        # 单独数字行，下一行是大写标题（如 "1\n\nBetween One Footstep..."）
        m = re.match(r'^(\d+)$', line)
        if m and int(m.group(1)) <= 99:
            for j in range(i+1, min(i+4, len(lines))):
                nxt = lines[j].strip()
                if nxt and re.match(r'^[A-Z]', nxt) and len(nxt) < 100:
                    return f"{line} {nxt}"[:100]
        # Prologue / Epilogue / Introduction / Preface
        if re.match(r'^(Prologue|Epilog(ue)?|Introduction|Preface)\b', line, re.I):
            return line[:100]
        # 卷/集开头
        if re.match(r'^[卷集][一二三四五六七八九十百千零\d]+[\.、:：]?\s*', line):
            return line[:100]
    return ''

def parse_epub_bytes(raw, filename):
    """解析EPUB，返回 (chapters, book_title, err, cover_bytes)"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw), 'r') as zf:
            _validate_zip_archive(zf)
            text_bytes_read = 0
            namelist = zf.namelist()
            opf_name = next((n for n in namelist if n.endswith('.opf')), None)
            if not opf_name:
                return None, '', 'EPUB中没有找到OPF文件', None

            opf_raw = _zip_read_limited(zf, opf_name, EPUB_MAX_META_BYTES)
            opf = opf_raw.decode('utf-8', errors='ignore')

            # 提取书名
            book_title = ''
            title_match = re.search(r'<dc:title[^>]*>(.*?)</dc:title>', opf, re.S|re.I)
            if title_match:
                book_title = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', title_match.group(1)).strip()
                book_title = _TAG_RE.sub('', book_title).strip()
            if not book_title:
                book_title = filename
                for ext_test in ['.epub', '.txt', '.md']:
                    if book_title.lower().endswith(ext_test):
                        book_title = book_title[:-len(ext_test)]
                        break

            # 提取 spine
            spine_ids = []
            for m in re.finditer(r'<itemref[^>]+idref=["\']([^"\']+)', opf):
                spine_ids.append(m.group(1))

            # 建立 manifest id->href 映射（支持两种属性顺序）
            id_to_href = {}
            for m in re.finditer(r'<item[^>]+id=["\']([^"\']+)["\'][^>]+href=["\']([^"\']+)', opf):
                id_to_href[m.group(1)] = m.group(2)
            for m in re.finditer(r'<item[^>]+href=["\']([^"\']+)["\'][^>]+id=["\']([^"\']+)', opf):
                id_to_href[m.group(2)] = m.group(1)

            # 提取封面图片
            cover_bytes = None
            cover_id = None
            # EPUB2: <meta name="cover" content="cover-image-id"/>
            cover_meta = re.search(r'<meta[^>]+name=["\']cover["\'][^>]+content=["\']([^"\']+)', opf, re.I)
            if cover_meta:
                cover_id = cover_meta.group(1)
            # EPUB3: <item properties="cover-image" .../>
            if not cover_id:
                cover_item_match = re.search(r'<item[^>]+properties=["\'][^"\']*cover-image[^"\']*["\'][^>]+id=["\']([^"\']+)', opf, re.I)
                if cover_item_match:
                    cover_id = cover_item_match.group(1)
                else:
                    cover_item_match = re.search(r'<item[^>]+id=["\']([^"\']+)["\'][^>]+properties=["\'][^"\']*cover-image', opf, re.I)
                    if cover_item_match:
                        cover_id = cover_item_match.group(1)
            if cover_id and cover_id in id_to_href:
                cover_href = id_to_href[cover_id]
                cover_full = next((n for n in namelist if n.endswith('/' + cover_href) or n == cover_href), None)
                if cover_full:
                    try:
                        cover_bytes = _zip_read_limited(zf, cover_full, EPUB_MAX_COVER_BYTES)
                    except:
                        cover_bytes = None
            # Fallback: look for any image item with "cover" in id or href
            if not cover_bytes:
                for m in re.finditer(r'<item[^>]+id=["\']([^"\']*cover[^"\']*)["\']', opf, re.I):
                    cid = m.group(1)
                    if cid in id_to_href:
                        chref = id_to_href[cid]
                        cfull = next((n for n in namelist if n.endswith('/' + chref) or n == chref), None)
                        if cfull:
                            try:
                                raw_img = _zip_read_limited(zf, cfull, EPUB_MAX_COVER_BYTES)
                                if raw_img[:3] in (b'\xff\xd8\xff', b'\x89PNG'):
                                    cover_bytes = raw_img
                                    break
                            except:
                                continue

            # 读取 NCX 建立 href->title 映射
            ncx_name = next((n for n in namelist if n.endswith('.ncx')), None)
            ncx_titles = {}
            if ncx_name:
                try:
                    ncx = _zip_read_limited(zf, ncx_name, EPUB_MAX_META_BYTES).decode('utf-8', errors='ignore')
                    for m in re.finditer(r'<navPoint[^>]*>.*?<text[^>]*>(.*?)</text>.*?<content[^>]+src=["\']([^"\']+)', ncx, re.S|re.I):
                        title_text = _TAG_RE.sub('', m.group(1)).strip()
                        src = m.group(2).split('#')[0]
                        ncx_titles[src] = title_text
                except:
                    pass

            # ── 阶段 A: 按 spine 顺序收集所有 HTML 项的原始信息 ──
            raw_items = []
            seen = set()
            for sid in spine_ids:
                href = id_to_href.get(sid, '')
                if not href:
                    continue
                full_name = next((n for n in namelist if n.endswith('/' + href) or n == href), None)
                if not full_name or full_name in seen:
                    continue
                seen.add(full_name)
                try:
                    html_raw = _zip_read_limited(zf, full_name, EPUB_MAX_HTML_BYTES)
                    text_bytes_read += len(html_raw)
                    if text_bytes_read > EPUB_MAX_TEXT_TOTAL_BYTES:
                        return None, book_title, f'EPUB 正文超过 {EPUB_MAX_TEXT_TOTAL_BYTES // _MB}MB 安全上限', None
                    html = html_raw.decode('utf-8', errors='ignore')
                except:
                    continue
                text = _strip_tags(html)
                if not text:
                    continue

                ncx_title = ncx_titles.get(href, '') or ''
                heading_title = _extract_html_heading(html)
                guessed_title = _guess_chapter_from_text(text)
                file_title = full_name.split('/')[-1].replace('.xhtml', '').replace('.html', '')[:50]

                if ncx_title:
                    title, src = ncx_title, 'ncx'
                elif heading_title:
                    title, src = heading_title, 'heading'
                elif guessed_title:
                    title, src = guessed_title, 'guess'
                else:
                    title, src = file_title, 'filename'

                # 取正文首个非空行（去标题），最多 80 字，给 AI 校验用
                first_line = ''
                for ln in text.split('\n'):
                    s = ln.strip()
                    if s and s != title:
                        first_line = s[:80]
                        break

                raw_items.append({
                    'href': href,
                    'source_file': full_name,
                    'title': title,
                    'title_source': src,
                    'ncx_title': ncx_title,
                    'heading_title': heading_title,
                    'guessed_title': guessed_title,
                    'file_title': file_title,
                    'content': text,
                    'char_count': len(text),
                    'first_line': first_line,
                })

            # ── 阶段 B: 过滤元信息页 + 合并短标题页到下一章 ──
            skip_titles_low = {
                'cover', 'bookcover', 'title page', 'contents',
                'landmarks', 'toc', 'navigation', 'nav',
                'frontmatter', 'backmatter', 'copyright',
            }
            skip_titles_zh = {
                '封面', '标题', '目录', '版权页', '版权信息', '版权', '制作信息',
                '彩插', '彩页', '扉页', '奥付', '奥附',
                '转载信息', 'table of contents',
            }
            # 章节标题模式：用于识别"短标题页"
            CHAPTER_TITLE_RE = re.compile(r'^第[一二三四五六七八九十百千零\d]+[章回节卷篇部集话幕折辑]|^[序前引终楔跋][章曲言子]?$|^(序章|序幕|楔子|引子|尾声|后记|终章|前言|序言|引言|跋|番外|外传|前传|prologue|epilogue|epilog)\b', re.I)

            def _looks_like_meta_page(item):
                t_low = (item['title'] or '').strip().lower()
                t = (item['title'] or '').strip()
                if t_low in skip_titles_low or t in skip_titles_zh:
                    return True
                # NCX 命中且长度 >= 1000 字，认为是用户想保留的内容（如详细的版权说明）→ 不跳
                if item['title_source'] == 'ncx' and item['char_count'] >= 1000:
                    return False
                # 文件名 fallback 的"Section00X 等"，且内容极短 + 仅有标题模式 → 后面交给合并逻辑处理
                return False

            def _looks_like_title_page(item):
                """短到几乎只有标题的页：< 200 字 且 内容以章节模式开头"""
                if item['char_count'] >= 200:
                    return False
                # 第一行匹配章节标题模式
                for ln in item['content'].split('\n'):
                    s = ln.strip()
                    if not s:
                        continue
                    return bool(CHAPTER_TITLE_RE.match(s))
                return False

            chapters = []
            pending_title_override = None  # 来自前一短标题页的标题
            pending_meta_extra = None

            for item in raw_items:
                if _looks_like_meta_page(item):
                    # 直接丢弃元信息页；不传染标题
                    continue
                if _looks_like_title_page(item):
                    # 把这一页的标题带到下一章
                    # 优先用 guessed_title（它从正文识别"第N话「xxx」"最准）或 heading_title
                    carry_title = item['guessed_title'] or item['heading_title'] or item['ncx_title'] or item['title']
                    pending_title_override = carry_title
                    pending_meta_extra = {
                        'merged_from_file': item['source_file'],
                        'merged_from_title': item['title'],
                    }
                    continue

                # 普通正文章节
                final_title = item['title']
                final_source = item['title_source']
                merged = None
                if pending_title_override:
                    # 仅当当前章标题是 fallback（filename）时，才用前面带过来的标题
                    if final_source == 'filename':
                        final_title = pending_title_override
                        final_source = 'merged_prev_title_page'
                    merged = pending_meta_extra
                    pending_title_override = None
                    pending_meta_extra = None

                chapters.append({
                    'title': final_title or f'章节{len(chapters)+1}',
                    'content': item['content'],
                    '_import_meta': {
                        'source_file': item['source_file'],
                        'href': item['href'],
                        'ncx_title': item['ncx_title'],
                        'heading_title': item['heading_title'],
                        'guessed_title': item['guessed_title'],
                        'file_title': item['file_title'],
                        'title_source': final_source,
                        'char_count': item['char_count'],
                        'first_line': item['first_line'],
                        'merged': merged,
                    },
                })

            # 兜底：如果没按 spine 读到，遍历所有 html
            if not chapters:
                for name in namelist:
                    if not (name.endswith('.xhtml') or name.endswith('.html') or name.endswith('.htm')):
                        continue
                    if name in seen or name.startswith('META-INF/'):
                        continue
                    try:
                        html_raw = _zip_read_limited(zf, name, EPUB_MAX_HTML_BYTES)
                        text_bytes_read += len(html_raw)
                        if text_bytes_read > EPUB_MAX_TEXT_TOTAL_BYTES:
                            return None, book_title, f'EPUB 正文超过 {EPUB_MAX_TEXT_TOTAL_BYTES // _MB}MB 安全上限', None
                        html = html_raw.decode('utf-8', errors='ignore')
                    except:
                        continue
                    text = _strip_tags(html)
                    if not text or len(text) < 30:
                        continue
                    title = _extract_html_heading(html) or _guess_chapter_from_text(text)
                    if not title:
                        title = name.split('/')[-1].replace('.xhtml', '').replace('.html', '')[:50]
                    chapters.append({
                        'title': title or f'章节{len(chapters)+1}',
                        'content': text,
                        '_import_meta': {'source_file': name, 'title_source': 'fallback_scan', 'char_count': len(text)},
                    })

            if not chapters:
                return None, book_title, '未能解析出有效章节', None
            return chapters, book_title, None, cover_bytes

    except Exception as e:
        log_action('EPUB_PARSE_ERROR', str(e)[:200])
        return None, '', f'EPUB解析失败: {str(e)[:80]}', None


IMPORT_PARSERS = {
    '.txt': lambda raw, fn: (parse_txt(raw.decode('utf-8', errors='ignore'), fn), None),
    '.md': lambda raw, fn: parse_md(raw.decode('utf-8', errors='ignore'), fn) + (None,),
    '.docx': parse_docx_bytes,
    '.pdf': parse_pdf_bytes,
    '.epub': parse_epub_bytes,
}


def _do_import_book_task(task_id, raw, filename, ext):
    """后台线程跑解析+建书；前端轮询 /api/import-book-status?task_id=X 拿结果。
    放后台是为了 Cloudflare Tunnel 这种代理：上传完成后 HTTP 响应不会再卡 100s 超时，解析也不会丢。"""
    import_start = time.time()
    try:
        bg_task_update(task_id, progress=5, result=json.dumps({'phase': '解析中...'}))
        parser = IMPORT_PARSERS[ext]
        result = parser(raw, filename)
        cover_data = None
        if len(result) == 4:
            chapters, book_title, err, cover_data = result
        elif len(result) == 3:
            chapters, book_title, err = result
        else:
            chapters, err = result
            book_title = ''
        if err:
            log_action('IMPORT_BOOK_PARSE_ERR', f'{filename}: {err}')
            bg_task_done(task_id, err); return
        if not chapters:
            bg_task_done(task_id, '未能解析出章节'); return
        bg_task_update(task_id, progress=50, result=json.dumps({'phase': '写入章节中...', 'chapter_count': len(chapters)}))
        bid = 'book_' + str(int(time.time() * 1000))
        bd = get_book_dir(bid)
        ch_dir = os.path.join(bd, 'chapters')
        os.makedirs(ch_dir, exist_ok=True)
        os.makedirs(os.path.join(bd, 'trash'), exist_ok=True)
        order = []
        imported = 0
        total = len(chapters)
        for ch in chapters:
            try:
                cid = 'ch_' + re.sub(r'[^\w]', '_', ch.get('title', 'untitled')[:30]) + '_' + str(int(time.time() * 1000)) + str(imported)
                if not is_valid_id(cid):
                    cid = 'ch_' + str(int(time.time() * 1000)) + str(imported)
                content = ch.get('content', '')
                ch_data = {'id': cid, 'title': ch.get('title', '未命名')[:200], 'content': content, 'updated': time.time()}
                if ch.get('_import_meta'):
                    ch_data['_import_meta'] = ch['_import_meta']
                save_json(os.path.join(ch_dir, f"{cid}.json"), ch_data)
                order.append(cid)
                imported += 1
                if imported % 20 == 0:
                    bg_task_update(task_id, progress=50 + int(40 * imported / max(total, 1)))
            except Exception:
                continue
        title = book_title or filename
        if not book_title:
            for ext_test in ['.txt', '.md', '.docx', '.pdf', '.epub']:
                if title.lower().endswith(ext_test):
                    title = title[:-len(ext_test)]
                    break
        meta = {'id': bid, 'title': title, 'created': time.time(), 'updated': time.time(), 'chapter_order': order}
        save_json(os.path.join(bd, 'meta.json'), meta)
        if cover_data and isinstance(cover_data, bytes) and len(cover_data) > 100:
            try:
                with open(os.path.join(bd, 'cover'), 'wb') as f:
                    f.write(cover_data)
                log_action('EPUB_COVER_IMPORT', bid)
            except Exception:
                pass
        save_json(os.path.join(bd, 'outline.json'), dict(DEFAULT_OUTLINE))
        elapsed = round(time.time() - import_start, 2)
        log_action('IMPORT_BOOK', f'{bid}: {imported} chapters from {filename} in {elapsed}s')
        bg_task_update(task_id, progress=100, result=json.dumps({'phase': 'done', 'book_id': bid, 'title': title, 'imported': imported}))
        bg_task_done(task_id)
    except Exception as e:
        log_action('IMPORT_BOOK_TASK_ERR', f'{filename}: {str(e)[:200]}')
        bg_task_done(task_id, f'解析失败: {str(e)[:100]}')


_import_builtin_books()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, fmt, *args): pass

    def _is_local_origin(self, origin):
        try:
            host = urlparse(origin).hostname
            return host in ('localhost', '127.0.0.1', '::1', '0.0.0.0')
        except Exception:
            return False

    def _origin_matches_host(self, origin):
        try:
            parsed = urlparse(origin)
            host = (self.headers.get('Host') or '').lower()
            return parsed.scheme in ('http', 'https') and parsed.netloc.lower() == host
        except Exception:
            return False

    def send_cors(self):
        origin = self.headers.get('Origin', '')
        if not origin:
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Luca-Client')
            return
        if self._origin_matches_host(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Luca-Client')

    def json_resp(self, code, data, extra_headers=None):
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Connection', 'close')
            self.send_cors()
            if extra_headers:
                for k, v in extra_headers.items(): self.send_header(k, v)
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
        except: pass

    def html_resp(self, content):
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except: pass

    def read_json(self):
        cl = int(self.headers.get('Content-Length', 0))
        if cl > 200 * 1024 * 1024: return None
        body = self.rfile.read(cl) if cl > 0 else b'{}'
        try: return json.loads(body.decode('utf-8')) if body else {}
        except: return {}

    def is_authed(self):
        if not has_users(): return False
        return validate_session(get_cookie_token(self.headers))

    def _check_access(self):
        scope = '127.0.0.1'
        try:
            s = load_json(SETTINGS_FILE)
            scope = s.get('access_scope', '127.0.0.1')
        except Exception:
            pass
        if scope == '127.0.0.1':
            if self.client_address[0] != '127.0.0.1':
                self.json_resp(403, {'error': '仅限本机访问'})
                return False
        return True

    def _check_csrf(self):
        try:
            s = load_json(SETTINGS_FILE)
            scope = s.get('access_scope', '127.0.0.1')
        except Exception:
            scope = '127.0.0.1'
        origin = self.headers.get('Origin', '')
        if not origin:
            referer = self.headers.get('Referer', '')
            if referer:
                try:
                    p = urlparse(referer)
                    origin = f'{p.scheme}://{p.netloc}'
                except Exception:
                    pass
        if not origin:
            # 浏览器跨域 POST 一律带 Origin（或至少 Referer）；两者都缺通常是非浏览器客户端。
            # 但是把"没 Origin"当通行证太宽松——加一道客户端标识门，至少要求声明自己是受信客户端
            # （Electron preload 注入 X-Luca-Client），否则拒绝。
            if self.headers.get('X-Luca-Client', '').strip():
                return True
            self.json_resp(403, {'error': 'CSRF check failed: missing Origin/Referer'})
            return False
        if self._origin_matches_host(origin):
            return True
        self.json_resp(403, {'error': 'CSRF check failed'})
        return False

    def serve_file(self, name):
        for p in [os.path.join(FRONTEND_DIR, name), f'/app/{name}', f'./{name}']:
            try:
                with open(p, 'r', encoding='utf-8') as f: return f.read()
            except: continue
        return None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def _track_me(self):
        """每个请求都记录客户端"""
        try:
            ip = self.client_address[0]
            ua = self.headers.get('User-Agent', '')
            lc = self.headers.get('X-Luca-Client', '')
            _track_http_client(ip, ua, lc)
        except Exception:
            pass

    def do_GET(self):
        self._track_me()
        if not self._check_access(): return
        path = urlparse(self.path).path

        # 兼容：支持 /summary 作为 /readthrough 的别名（前端/外部可能使用 summary 命名）
        if '/summary/' in path:
            path = path.replace('/summary/', '/readthrough/')
        if path == '/summary':
            path = '/readthrough'

        if path in ('/', '/index.html'):
            # 未登录直接 302 到 /login，避免浏览器先渲染一瞬主界面壳再被 JS 跳走
            # （壳本身不含用户数据，但能减少信息暴露面 + 改善体验）
            if not self.is_authed():
                self.send_response(302)
                self.send_header('Location', '/login')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                return
            c = self.serve_file('index.html')
            if c: self.html_resp(c)
            else: self.json_resp(500, {'error': 'no index.html'})
            return

        if path == '/login':
            c = self.serve_file('login.html')
            if c: self.html_resp(c)
            else: self.json_resp(500, {'error': 'no login.html'})
            return

        # 动态主题图标生成
        if path == '/icon.png' or path == '/icon.ico':
            if HAS_ICON_GENERATOR:
                try:
                    settings = get_settings()
                    theme_accent = settings.get('theme_accent', '#E8CC7A')
                    icon_bytes = icon_generator.get_icon_bytes(theme_accent)
                    if icon_bytes:
                        ct = 'image/png' if path.endswith('.png') else 'image/x-icon'
                        self.send_response(200)
                        self.send_header('Content-Type', ct)
                        self.send_header('Content-Length', str(len(icon_bytes)))
                        self.send_header('Cache-Control', 'no-cache')
                        self.end_headers()
                        self.wfile.write(icon_bytes)
                        return
                except Exception:
                    pass
            # 回退到静态文件
            fp = os.path.join(FRONTEND_DIR, os.path.basename(path))
            if os.path.isfile(fp):
                with open(fp, 'rb') as f:
                    body = f.read()
                ct = 'image/png' if path.endswith('.png') else 'image/x-icon'
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'public, max-age=3600')
                self.end_headers()
                self.wfile.write(body)
                return

        # 静态文件（图片、字体、CSS、JS 等）— 支持子目录，放在认证之前
        _static_exts = ('.png','.svg','.ico','.jpg','.jpeg','.gif','.webp','.css','.js','.woff2','.ttf','.otf')
        if path.endswith(_static_exts):
            rel = path.lstrip('/')
            if '..' not in rel:
                fp = os.path.join(FRONTEND_DIR, rel)
                if os.path.isfile(fp) and os.path.normpath(fp).startswith(os.path.normpath(FRONTEND_DIR)):
                    ext_map = {'.png':'image/png','.svg':'image/svg+xml','.ico':'image/x-icon','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.css':'text/css','.js':'application/javascript','.woff2':'font/woff2','.ttf':'font/ttf','.otf':'font/otf'}
                    ct = ext_map.get(os.path.splitext(fp)[1].lower(), 'application/octet-stream')
                    with open(fp, 'rb') as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', ct)
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'public, max-age=3600')
                    self.end_headers()
                    self.wfile.write(body)
                    return

        if path == '/api/auth/status':
            self.json_resp(200, {'has_users': has_users(), 'logged_in': self.is_authed()})
            return

        if path == '/api/settings':
            gs = get_settings()
            if not self.is_authed():
                # 登录页只用主题色 + 明暗模式，其他字段（provider URL / 自定义 JSON / access_scope 等）
                # 都可能含用户配置或敏感信息，未登录一律剥掉
                gs = {
                    'theme_accent': gs.get('theme_accent', '#E8CC7A'),
                    'theme_mode': gs.get('theme_mode', ''),
                }
            self.json_resp(200, gs); return

        if not self.is_authed():
            self.json_resp(401, {'error': '未登录'}); return

        # 下面这些会暴露客户端 IP / AI 活动等元数据，必须先过认证
        if path == '/api/connected-clients':
            self.json_resp(200, {'clients': get_connected_clients()}); return

        if path == '/api/active-connections':
            self.json_resp(200, {'connections': get_active_connections()}); return

        if path == '/api/ai-activity':
            q = queue.Queue()
            with _ai_sse_lock:
                _ai_sse_clients.append(q)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            n = len(_ai_connections)
            try:
                self.wfile.write(f'data: {json.dumps({"count": n})}\n\n'.encode())
                self.wfile.flush()
            except:
                with _ai_sse_lock:
                    try: _ai_sse_clients.remove(q)
                    except ValueError: pass
                return
            try:
                while True:
                    try:
                        msg = q.get(timeout=30)
                        self.wfile.write(msg)
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b': keepalive\n\n')
                        self.wfile.flush()
            except:
                pass
            finally:
                with _ai_sse_lock:
                    try: _ai_sse_clients.remove(q)
                    except ValueError: pass
            return

        if path == '/api/editor-fonts':
            self.json_resp(200, {'fonts': get_settings().get('editor_font_presets', [])}); return

        if path.startswith('/api/editor-fonts/'):
            file_name = os.path.basename(unquote(path.split('/api/editor-fonts/', 1)[1]))
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in FONT_EXTS:
                self.json_resp(404, {'error': 'Not found'}); return
            fp = os.path.normpath(os.path.join(USER_FONTS_DIR, file_name))
            fonts_root = os.path.normpath(USER_FONTS_DIR)
            if not fp.startswith(fonts_root) or not os.path.isfile(fp):
                self.json_resp(404, {'error': 'Not found'}); return
            with open(fp, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', FONT_EXTS[ext][0])
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
            return

        if path == '/api/sessions':
            sessions = load_json(SESSIONS_FILE, list)
            now = time.time()
            t = get_cookie_token(self.headers)
            result = []
            for s in sessions:
                if s.get('expires', 0) > now:
                    result.append({
                        'token_prefix': s.get('token', '')[:12],
                        'is_current': s.get('token') == t,
                        'device_name': s.get('device_name', ''),
                        'created': s.get('created', 0),
                        'expires': s.get('expires', 0),
                    })
            result.sort(key=lambda x: x.get('created', 0), reverse=True)
            self.json_resp(200, {'sessions': result}); return

        if path == '/api/books':
            books = []
            if os.path.isdir(BOOKS_DIR):
                for d in sorted(os.listdir(BOOKS_DIR)):
                    bp = os.path.join(BOOKS_DIR, d)
                    if not os.path.isdir(bp): continue
                    meta = load_json(os.path.join(bp, 'meta.json'))
                    if not meta: continue
                    ch_dir = os.path.join(bp, 'chapters')
                    cc = len(os.listdir(ch_dir)) if os.path.isdir(ch_dir) else 0
                    has_cover = os.path.isfile(os.path.join(bp, 'cover'))
                    books.append({
                        'id': d,
                        'title': meta.get('title', d),
                        'created': meta.get('created', 0),
                        'updated': meta.get('updated', 0),
                        'chapter_count': cc,
                        'type': meta.get('type', 'book'),
                        'has_cover': has_cover,
                        'author': meta.get('author', ''),
                        'description': meta.get('description', ''),
                        'series_books': meta.get('series_books', []),
                        'cover_book': meta.get('cover_book', ''),
                    })
            books.sort(key=lambda x: x.get('updated', 0), reverse=True)
            self.json_resp(200, {'books': books}); return

        qs = parse_qs(urlparse(self.path).query)

        if path == '/api/import-book-status':
            tid = qs.get('task_id', [''])[0]
            if not tid:
                self.json_resp(400, {'error': '缺少 task_id'}); return
            t = bg_task_get(tid)
            if not t:
                self.json_resp(404, {'error': '任务不存在'}); return
            resp = {'status': t.get('status', 'running'), 'progress': t.get('progress', 0), 'error': t.get('error', '')}
            raw_result = t.get('result', '')
            if raw_result:
                try:
                    parsed = json.loads(raw_result)
                    if isinstance(parsed, dict):
                        resp.update(parsed)
                except Exception:
                    pass
            self.json_resp(200, resp); return

        if path.startswith('/api/book/') and '/chapters' in path:
            parts = path.split('/')
            bid = parts[3] if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            meta = get_book_meta(bid) or {}
            ch_dir = os.path.join(get_book_dir(bid), 'chapters')
            chapters = []
            order = meta.get('chapter_order', [])
            if os.path.isdir(ch_dir):
                for fn in os.listdir(ch_dir):
                    if fn.endswith('.json') and not fn.startswith('.'):
                        try:
                            with open(os.path.join(ch_dir, fn), 'r', encoding='utf-8') as f:
                                ch = json.load(f)
                                ch['id'] = fn.replace('.json', '')
                                chapters.append(ch)
                        except: continue
            ch_map = {c['id']: c for c in chapters}
            ordered = []
            for cid in order:
                if cid in ch_map: ordered.append(ch_map.pop(cid))
            ordered.extend(ch_map.values())
            self.json_resp(200, {'chapters': ordered, 'chapter_order': [c['id'] for c in ordered], 'current_chapter_id': meta.get('current_chapter_id', '')}); return

        if path.startswith('/api/book/') and '/chapter/' in path:
            parts = path.split('/')
            bid = parts[3] if len(parts) > 3 else ''
            cid = parts[5] if len(parts) > 5 else ''
            if not is_valid_id(bid) or not is_valid_id(cid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            cp = os.path.join(get_book_dir(bid), 'chapters', f"{cid}.json")
            if os.path.exists(cp):
                try:
                    with open(cp, 'r', encoding='utf-8') as f: self.json_resp(200, json.load(f))
                except: self.json_resp(500, {'error': '读取失败'})
            else: self.json_resp(404, {'error': '章节不存在'})
            return

        if path.startswith('/api/book/') and path.endswith('/outline'):
            parts = path.split('/')
            bid = parts[3] if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            o = get_outline(bid)
            o['memory'] = get_core_memory(bid)
            self.json_resp(200, o); return

        if path.startswith('/api/book/') and path.endswith('/chapter-kb'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            cid = qs.get('chapter_id', [''])[0]
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            if not is_valid_id(cid):
                self.json_resp(400, {'error': '缺少章节'}); return
            try:
                kb_storage.init_db(bid)
                self.json_resp(200, kb_pipeline.chapter_outline(bid, cid)); return
            except Exception as e:
                self.json_resp(500, {'error': str(e)[:200]}); return

        if path.startswith('/api/book/') and path.endswith('/timeline-map'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            cid = qs.get('chapter_id', [''])[0]
            zoom = qs.get('zoom', ['1'])[0]
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            if cid and not is_valid_id(cid):
                self.json_resp(400, {'error': '章节无效'}); return
            try:
                kb_storage.init_db(bid)
                self.json_resp(200, kb_pipeline.timeline_map(bid, focus_chapter_id=cid or None, zoom=zoom)); return
            except Exception as e:
                self.json_resp(500, {'error': str(e)[:200]}); return

        if path.startswith('/api/book/') and path.endswith('/prediction-current'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            text = get_prediction_md(bid)
            p = os.path.join(get_book_dir(bid), 'prediction.md')
            updated = os.path.getmtime(p) if os.path.isfile(p) else 0
            kb_db = os.path.join(get_book_dir(bid), 'kb.db')
            kb_modified = os.path.getmtime(kb_db) if os.path.isfile(kb_db) else 0
            stale = bool(text) and kb_modified > updated
            self.json_resp(200, {'text': text, 'exists': bool(text), 'updated': updated, 'kb_modified': kb_modified, 'stale': stale}); return

        if path.startswith('/api/book/') and path.endswith('/trash'):
            parts = path.split('/')
            bid = parts[3] if len(parts) > 3 else ''
            if not is_valid_id(bid): self.json_resp(400, {'error': 'Invalid ID'}); return
            td = os.path.join(get_book_dir(bid), 'trash')
            chapters = []
            if os.path.isdir(td):
                for fn in os.listdir(td):
                    if fn.endswith('.json'):
                        try:
                            with open(os.path.join(td, fn), 'r', encoding='utf-8') as f: chapters.append(json.load(f))
                        except: continue
            chapters.sort(key=lambda x: x.get('deleted', 0), reverse=True)
            self.json_resp(200, {'chapters': chapters}); return

        if path.startswith('/api/book/') and '/export' in path:
            log_action('EXPORT_REQUEST', f'path={path} query={dict(qs)}')
            parts = path.split('/')
            bid = parts[3] if len(parts) > 3 else ''
            fmt = qs.get('format', ['zip'])[0]
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            ch_dir = os.path.join(get_book_dir(bid), 'chapters')
            meta = get_book_meta(bid) or {}
            safe_title = re.sub(r'[^\w\u4e00-\u9fff.\-]', '_', meta.get('title', 'book'))[:100] or 'book'
            all_chapters = {}
            if os.path.isdir(ch_dir):
                for fn in os.listdir(ch_dir):
                    if fn.endswith('.json'):
                        try:
                            with open(os.path.join(ch_dir, fn), 'r', encoding='utf-8') as f:
                                ch = json.load(f)
                                all_chapters[ch.get('id', fn)] = ch
                        except: continue
            order = meta.get('chapter_order', [])
            ordered = [all_chapters.pop(cid) for cid in order if cid in all_chapters]
            ordered.extend(all_chapters.values())
            log_action('EXPORT_BUILD', f'book={bid} fmt={fmt} chapters={len(ordered)}')
            try:
                if fmt == 'md':
                    text = f"# {meta.get('title', '')}\n\n"
                    for ch in ordered:
                        text += f"## {ch.get('title', '')}\n\n{ch.get('content', '')}\n\n---\n\n"
                    body = text.encode('utf-8')
                    utf8_fn = quote(safe_title + '.md', safe='')
                    log_action('EXPORT_SEND_MD', f'size={len(body)} fn={utf8_fn}')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/markdown; charset=utf-8')
                    self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{utf8_fn}")
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    self.send_cors(); self.end_headers()
                    self.wfile.write(body)
                elif fmt == 'txt':
                    text = f"{meta.get('title', '')}\n\n"
                    for ch in ordered:
                        text += f"{ch.get('title', '')}\n\n{ch.get('content', '')}\n\n"
                    body = text.encode('utf-8')
                    utf8_fn = quote(safe_title + '.txt', safe='')
                    log_action('EXPORT_SEND_TXT', f'size={len(body)} fn={utf8_fn}')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{utf8_fn}")
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    self.send_cors(); self.end_headers()
                    self.wfile.write(body)
                else:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, 'w') as zf:
                        for ch in ordered:
                            cid = ch.get('id', 'unknown')
                            fn = f"{cid}.json"
                            zf.writestr(fn, json.dumps(ch, ensure_ascii=False, indent=2))
                    body = buf.getvalue()
                    utf8_fn = quote(safe_title + '.zip', safe='')
                    log_action('EXPORT_SEND_ZIP', f'size={len(body)} fn={utf8_fn}')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{utf8_fn}")
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Connection', 'close')
                    self.send_cors(); self.end_headers()
                    self.wfile.write(body)
            except Exception as e:
                log_action('EXPORT_ERROR', f'book={bid} fmt={fmt} err={str(e)[:200]}')
                self.json_resp(500, {'error': f'导出失败: {str(e)[:100]}'}); return
            return

        if path == '/api/icon':
            qs = parse_qs(urlparse(self.path).query)
            size = int(qs.get('size', [None])[0]) if qs.get('size') else None
            settings = get_settings()
            theme_accent = settings.get('theme_accent', '#E8CC7A')
            if HAS_ICON_GENERATOR:
                icon_base64 = icon_generator.get_icon_base64(theme_accent, size)
            else:
                icon_base64 = None
            self.json_resp(200, {'icon': icon_base64, 'theme_accent': theme_accent}); return

        if path == '/api/theme-is-light':
            settings = get_settings()
            theme_accent = settings.get('theme_accent', '#E8CC7A')
            is_light = True
            if HAS_ICON_GENERATOR:
                try:
                    rgb = icon_generator.hex_to_rgb(theme_accent)
                    is_light = icon_generator.is_light_color(rgb)
                except Exception:
                    is_light = True
            self.json_resp(200, {'is_light': is_light, 'theme_accent': theme_accent}); return

        if path == '/api/local-llm/status':
            self.json_resp(200, {'running': _local_llm_status()}); return

        if path == '/api/local-llm/progress':
            with _LOCAL_LLM_LOCK:
                st = dict(_LOCAL_LLM_STATE)
            st['running'] = _local_llm_status()
            self.json_resp(200, st); return

        if path == '/api/local-llm/speed':
            self.json_resp(200, _local_llm_speed_snapshot()); return

        if path == '/api/local-llm/detected-model':
            detected = _detect_local_model()
            model_name = ''
            if detected:
                model_name = os.path.splitext(os.path.basename(detected))[0]
            self.json_resp(200, {'model': model_name, 'path': detected or ''}); return

        if path == '/api/local-llm/preset-models':
            self.json_resp(200, {'models': _PRESET_MODELS}); return

        if path == '/api/local-llm/download-progress':
            with _DOWNLOAD_LOCK:
                st = dict(_DOWNLOAD_STATE)
            self.json_resp(200, st); return

        if path == '/api/local-llm/hardware-check':
            hw = _detect_hardware()
            strategy = _apply_bundle_limit(_decide_local_strategy(hw))
            _save_local_strategy(hw, strategy)
            self.json_resp(200, {'hardware': hw, 'strategy': strategy}); return


        # 浏览器控制 API
        if path == '/api/browser/status':
            if not HAS_BROWSER_AGENT:
                self.json_resp(200, {'available': False, 'error': '浏览器控制模块未安装'}); return
            self.json_resp(200, browser_agent.get_browser_status()); return

        if path == '/api/chat-sessions':
            _migrate_global_to_sessions()
            self.json_resp(200, {'sessions': _list_chat_sessions()}); return

        if path.startswith('/api/chat-session/') and path.endswith('/messages'):
            sid = path.split('/')[3] if len(path.split('/')) > 4 else ''
            if not sid.startswith('cs_'):
                self.json_resp(400, {'error': 'invalid session id'}); return
            p = _get_chat_history_path(sid)
            if not os.path.isfile(p):
                self.json_resp(404, {'error': 'session not found'}); return
            self.json_resp(200, {'messages': load_json(p, list)}); return

        if path.startswith('/api/book/') and path.endswith('/messages'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            messages = _load_chat_history(bid)
            self.json_resp(200, {'messages': messages}); return

        if path.startswith('/api/book/') and path.endswith('/inspirations'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': 'book not found'}); return
            self.json_resp(200, {'items': get_inspiration_items(bid)}); return

        if path.startswith('/api/book/') and path.endswith('/annotations'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            ann_path = os.path.join(get_book_dir(bid), 'annotations.json')
            anns_data = load_json(ann_path, dict)
            self.json_resp(200, {'annotations': anns_data.get('annotations', [])}); return

        # 通读页面
        if path == '/readthrough':
            c = self.serve_file('readthrough.html')
            if c: self.html_resp(c)
            else: self.json_resp(500, {'error': 'no readthrough.html'})
            return

        # 通读 API (GET)
        if path.startswith('/api/book/') and '/readthrough/' in path:
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            sub = parts[5] if len(parts) > 5 else ''
            if sub == 'config':
                self.json_resp(200, get_readthrough_config(bid)); return
            elif sub == 'status':
                try:
                    st = kb_storage.get_rt_state(bid)
                except Exception:
                    kb_storage.init_db(bid)
                    st = kb_storage.get_rt_state(bid)
                if not st:
                    resp = {'status': 'idle', 'phase': '', 'current_idx': -1, 'total': 0, 'stream_buffer': '', 'error': ''}
                else:
                    resp = dict(st)
                    if resp.get('status') == 'running':
                        t = threading.enumerate()
                        alive = any(t_.name == f'kb_readthrough_{bid}' for t_ in t if t_.is_alive())
                        just_started = time.time() - float(resp.get('updated_at') or 0) < 8
                        if not alive and not just_started:
                            resp['status'] = 'paused'
                            resp['phase'] = '进程已退出，可继续'
                            kb_storage.set_rt_state(bid, status='paused', phase='进程已退出，可继续')
                resp['recent_logs'] = kb_storage.get_rt_logs(bid, 30)
                resp['done_count'] = kb_storage.get_done_chapter_count(bid)
                if not resp.get('total'):
                    resp['total'] = len((get_book_meta(bid) or {}).get('chapter_order', []) or [])
                resp['kb_overview'] = kb_storage.get_kb_overview(bid, int(resp.get('current_idx') or -1))
                resp['has_source'] = bool(get_source(bid))
                if resp.get('current_idx') is None:
                    resp['current_idx'] = -1
                self.json_resp(200, resp); return
            elif sub == 'file':
                ft = qs.get('type', ['source'])[0]
                if ft == 'source':
                    text = get_source(bid); self.json_resp(200, {'text': text, 'exists': bool(text), 'type': ft}); return
                elif ft == 'outline':
                    text = get_outline_md(bid); self.json_resp(200, {'text': text, 'exists': bool(text), 'type': ft}); return
                elif ft == 'timeline':
                    text = get_timeline_md(bid); self.json_resp(200, {'text': text, 'exists': bool(text), 'type': ft}); return
                elif ft == 'prediction':
                    text = get_prediction_md(bid); self.json_resp(200, {'text': text, 'exists': bool(text), 'type': ft}); return
                else:
                    self.json_resp(400, {'error': '未知文件类型'}); return

        # 通用后台任务状态查询
        if path.startswith('/api/book/') and '/task/' in path:
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            sub = parts[5] if len(parts) > 5 else ''
            if sub == 'status':
                task_id = qs.get('task_id', [''])[0]
                task_type = qs.get('type', [''])[0]
                if task_id:
                    t = bg_task_get(task_id)
                    if not t:
                        self.json_resp(404, {'error': '任务不存在'}); return
                    self.json_resp(200, t); return
                elif task_type:
                    t = bg_task_get_by_book_type(bid, task_type)
                    if not t:
                        self.json_resp(200, {'status': 'idle', 'progress': 0}); return
                    self.json_resp(200, t); return
                else:
                    # 返回该书所有活跃任务
                    active = []
                    with _bg_lock:
                        for t in _bg_tasks.values():
                            if t['book_id'] == bid and t['status'] == 'running':
                                active.append(dict(t))
                    self.json_resp(200, {'tasks': active}); return
            elif sub == 'list':
                tasks = []
                with _bg_lock:
                    for t in _bg_tasks.values():
                        if t['book_id'] == bid:
                            tasks.append(dict(t))
                tasks.sort(key=lambda x: x.get('updated', 0), reverse=True)
                self.json_resp(200, {'tasks': tasks}); return

        # 书本封面 — 无封面时自动生成 SVG 占位图
        if path.startswith('/api/book/') and path.endswith('/cover'):
            parts = path.split('/')
            bid = parts[3] if len(parts) > 3 else ''
            if not is_valid_id(bid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            cover_path = os.path.join(get_book_dir(bid), 'cover')
            if os.path.isfile(cover_path):
                try:
                    with open(cover_path, 'rb') as f:
                        cover_data = f.read()
                    ct = 'image/png'
                    if cover_data[:3] == b'\xff\xd8\xff':
                        ct = 'image/jpeg'
                    elif cover_data[:4] == b'RIFF':
                        ct = 'image/webp'
                    elif cover_data[:3] == b'GIF':
                        ct = 'image/gif'
                    self.send_response(200)
                    self.send_header('Content-Type', ct)
                    self.send_header('Content-Length', str(len(cover_data)))
                    self.send_header('Cache-Control', 'public, max-age=3600')
                    self.send_cors()
                    self.end_headers()
                    self.wfile.write(cover_data)
                except Exception:
                    self.json_resp(500, {'error': '读取封面失败'})
                return
            # 无封面 — 生成 SVG 占位图
            meta = get_book_meta(bid) or {}
            title = meta.get('title', bid) or '未命名'
            if meta.get('type') == 'series':
                book_count = len([x for x in meta.get('series_books', []) if x])
                svg = _make_series_cover_svg(title, book_count)
            else:
                svg = _make_cover_svg(title)
            body = svg.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-cache')
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
            return

        # 系列通读状态 (GET)
        if path.startswith('/api/series/') and '/readthrough/status' in path:
            sid = path.split('/')[3] if len(path.split('/')) > 3 else ''
            if not is_valid_id(sid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            with _series_rt_lock:
                t = _series_rt_tasks.get(sid, {'status': 'idle', 'progress': 0, 'phase': '准备中', 'total_chapters': 0, 'done_chapters': 0, 'error': '', 'stream_buffer': ''})
                resp = dict(t)
            self.json_resp(200, resp); return

        # 系列详情 (GET)
        if path.startswith('/api/series/') and len(path.split('/')) >= 4:
            parts = path.split('/')
            sid = parts[3] if len(parts) > 3 else ''
            if not is_valid_id(sid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            s_meta = get_book_meta(sid)
            if not s_meta or s_meta.get('type') != 'series':
                self.json_resp(404, {'error': '系列不存在'}); return
            series_book_ids = [x for x in s_meta.get('series_books', []) if x]
            books_data = []
            for bid_item in series_book_ids:
                b_meta = get_book_meta(bid_item)
                if not b_meta:
                    continue
                bp_item = get_book_dir(bid_item)
                ch_dir_item = os.path.join(bp_item, 'chapters')
                cc_item = len(os.listdir(ch_dir_item)) if os.path.isdir(ch_dir_item) else 0
                has_cover_item = os.path.isfile(os.path.join(bp_item, 'cover'))
                ch_names_item = []
                ch_order_item = b_meta.get('chapter_order', [])
                if os.path.isdir(ch_dir_item):
                    ch_map_item = {}
                    for fn_item in os.listdir(ch_dir_item):
                        if fn_item.endswith('.json') and not fn_item.startswith('.'):
                            try:
                                with open(os.path.join(ch_dir_item, fn_item), 'r', encoding='utf-8') as f_ch:
                                    ch_data_item = json.load(f_ch)
                                    ch_id_item = fn_item.replace('.json', '')
                                    ch_map_item[ch_id_item] = ch_data_item.get('title', '未命名')
                            except: continue
                    for cid_item in ch_order_item:
                        if cid_item in ch_map_item:
                            ch_names_item.append({'id': cid_item, 'title': ch_map_item.pop(cid_item)})
                    for cid_item, cname_item in ch_map_item.items():
                        ch_names_item.append({'id': cid_item, 'title': cname_item})
                books_data.append({
                    'id': bid_item,
                    'title': b_meta.get('title', bid_item),
                    'created': b_meta.get('created', 0),
                    'updated': b_meta.get('updated', 0),
                    'chapter_count': cc_item,
                    'chapter_names': ch_names_item,
                    'type': b_meta.get('type', 'book'),
                    'has_cover': has_cover_item,
                    'author': b_meta.get('author', ''),
                    'description': b_meta.get('description', ''),
                })
            self.json_resp(200, {
                'series': {
                    'id': sid,
                    'title': s_meta.get('title', ''),
                    'type': 'series',
                    'created': s_meta.get('created', 0),
                    'updated': s_meta.get('updated', 0),
                    'has_cover': os.path.isfile(os.path.join(get_book_dir(sid), 'cover')),
                    'series_books': series_book_ids,
                    'cover_book': s_meta.get('cover_book', ''),
                },
                'books': books_data,
            }); return

        # 静态文件（图片、SVG 等）
        if path.endswith(('.png', '.svg', '.ico', '.jpg', '.jpeg', '.gif', '.webp')):
            fp = os.path.join(FRONTEND_DIR, os.path.basename(path))
            if os.path.isfile(fp):
                ext_map = {'.png':'image/png','.svg':'image/svg+xml','.ico':'image/x-icon','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'}
                ct = ext_map.get(os.path.splitext(fp)[1].lower(), 'application/octet-stream')
                with open(fp, 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'public, max-age=3600')
                self.end_headers()
                self.wfile.write(body)
                return

        self.json_resp(404, {'error': 'Not found'})

    def do_POST(self):
        self._track_me()
        if not self._check_access(): return
        if not self._check_csrf(): return
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        # 兼容：支持 /summary 作为 /readthrough 的别名
        if '/summary/' in path:
            path = path.replace('/summary/', '/readthrough/')
        if path == '/summary':
            path = '/readthrough'

        data = self.read_json()
        if data is None: self.json_resp(413, {'error': 'Too large'}); return

        if path == '/api/auth/status':
            self.json_resp(200, {'has_users': has_users(), 'logged_in': self.is_authed()}); return

        if path == '/api/auth/setup':
            if has_users(): self.json_resp(403, {'error': '已有用户'}); return
            if not check_rate_limit(f'setup:{self.client_address[0]}', 5, 60):
                self.json_resp(429, {'error': '请求过于频繁，请稍后再试'}); return
            u = data.get('username', '').strip()
            p = data.get('password', '')
            device_name = data.get('device_name', '').strip() or self.headers.get('X-Luca-Device', '')[:50]
            remember = data.get('remember', False)
            if not u: self.json_resp(400, {'error': '请输入用户名'}); return
            # 密码可以为空（不设密码直接登录）
            pw_hash = hash_password(p) if p else ''
            save_json(USERS_FILE, {u: {'password': pw_hash, 'created': time.time()}})
            token = make_session(u, remember=remember, device_name=device_name)
            log_action('SETUP', u)
            max_age = 7776000 if remember else 86400
            cookie = f'session={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict'
            if self.headers.get('X-Forwarded-Proto') == 'https':
                cookie += '; Secure'
            self.json_resp(200, {'ok': True, 'username': u, 'has_password': bool(p)}, {'Set-Cookie': cookie}); return

        if path == '/api/auth/login':
            u = data.get('username', '').strip()
            p = data.get('password', '')
            device_name = data.get('device_name', '').strip() or self.headers.get('X-Luca-Device', '')[:50]
            remember = data.get('remember', False)
            if not u: self.json_resp(400, {'error': '请输入用户名'}); return
            if not check_rate_limit(f'login:{self.client_address[0]}', 10, 60):
                self.json_resp(429, {'error': '请求过于频繁，请稍后再试'}); return
            users = load_json(USERS_FILE)
            if u not in users:
                self.json_resp(401, {'error': '用户名或密码错误'}); return
            if _check_account_lockout(users, u):
                remaining = int(users[u].get('locked_until', 0) - time.time())
                self.json_resp(429, {'error': f'账户已锁定，{max(remaining, 0)} 秒后重试'}); return
            pw_stored = users[u].get('password', '')
            # 密码为空 → 不设密码，直接放行
            if not pw_stored:
                token = make_session(u, remember=remember, device_name=device_name)
                log_action('LOGIN', u)
                max_age = 7776000 if remember else 86400
                cookie = f'session={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict'
                if self.headers.get('X-Forwarded-Proto') == 'https':
                    cookie += '; Secure'
                self.json_resp(200, {'ok': True, 'username': u, 'has_password': False}, {'Set-Cookie': cookie}); return
            if not verify_password(p, pw_stored):
                _record_failed_attempt(users, u)
                users2 = load_json(USERS_FILE)
                failed = users2[u].get('failed_attempts', 0)
                remaining = MAX_LOGIN_ATTEMPTS - failed
                if remaining > 0:
                    self.json_resp(401, {'error': f'密码错误，还剩 {remaining} 次机会'}); return
                else:
                    self.json_resp(429, {'error': '账户已锁定，请 15 分钟后重试'}); return
            _reset_failed_attempts(users, u)
            # 旧格式哈希自动升级为 PBKDF2
            if pw_stored and is_old_password_hash(pw_stored):
                users[u]['password'] = hash_password(p)
                save_json(USERS_FILE, users)
                log_action('PW_UPGRADE', u)
            token = make_session(u, remember=remember, device_name=device_name)
            log_action('LOGIN', u)
            max_age = 7776000 if remember else 86400
            cookie = f'session={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict'
            if self.headers.get('X-Forwarded-Proto') == 'https':
                cookie += '; Secure'
            self.json_resp(200, {'ok': True, 'username': u, 'has_password': True}, {'Set-Cookie': cookie}); return

        if path == '/api/auth/logout':
            t = get_cookie_token(self.headers)
            if t:
                sessions = [s for s in load_json(SESSIONS_FILE, list) if s.get('token') != t]
                save_json(SESSIONS_FILE, sessions)
            self.json_resp(200, {'ok': True}, {'Set-Cookie': 'session=; Path=/; Max-Age=0; SameSite=Lax'}); return

        if path == '/api/auth/reset-password':
            if not check_rate_limit(f'reset-pwd:{self.client_address[0]}', 3, 300):
                self.json_resp(429, {'error': '请求过于频繁，请稍后再试'}); return
            # 桌面版：在终端中验证书名
            if _is_electron_mode():
                success, msg = _verify_book_title_in_terminal()
                if not success:
                    self.json_resp(403, {'error': f'验证失败: {msg}'}); return
                # 验证通过，删除用户文件
                if os.path.exists(USERS_FILE):
                    os.remove(USERS_FILE)
                if os.path.exists(SESSIONS_FILE):
                    os.remove(SESSIONS_FILE)
                log_action('RESET-PASSWORD', f'from {self.client_address[0]} (verified)')
                self.json_resp(200, {'ok': True, 'message': '密码已重置，请重新创建账户'}); return
            else:
                # 源码启动：提示用户手动删除
                self.json_resp(400, {'error': '源码启动模式请手动删除 users.json 文件重置密码', 'data_dir': DATA_DIR}); return

        if not self.is_authed():
            self.json_resp(401, {'error': '未登录'}); return

        if path == '/api/editor-fonts':
            filename = str(data.get('filename') or '')
            font_b64 = str(data.get('data') or '')
            ext = os.path.splitext(filename)[1].lower()
            if ext not in FONT_EXTS:
                self.json_resp(400, {'error': '只支持 .ttf / .otf 字体'}); return
            if not font_b64:
                self.json_resp(400, {'error': '缺少字体文件'}); return
            try:
                if ',' in font_b64:
                    font_b64 = font_b64.split(',', 1)[1]
                raw = base64.b64decode(font_b64)
            except Exception:
                self.json_resp(400, {'error': '字体文件无效'}); return
            if len(raw) > 30 * 1024 * 1024:
                self.json_resp(400, {'error': '字体文件超过 30MB'}); return
            if not _looks_like_font(raw, ext):
                self.json_resp(400, {'error': '字体文件格式不匹配'}); return
            fid = 'font_' + str(int(time.time() * 1000)) + '_' + secrets.token_hex(4)
            file_name = fid + ext
            fp = os.path.join(USER_FONTS_DIR, file_name)
            with open(fp, 'wb') as f:
                f.write(raw)
            settings = get_settings()
            presets = settings.get('editor_font_presets', [])
            preset = {
                'id': fid,
                'name': _clean_editor_font_name(data.get('name') or filename),
                'file': file_name,
            }
            presets.append(preset)
            settings['editor_font_presets'] = _normalize_editor_font_presets(presets)
            settings['editor_font_preset_id'] = fid
            save_settings = json.loads(json.dumps(settings))
            save_presets = list(save_settings.get('provider_presets', []))
            for p in save_presets:
                if p.get('api_key'):
                    p['api_key'] = _encrypt_str(p['api_key'])
            save_settings['provider_presets'] = save_presets
            if save_settings.get('api_key'):
                save_settings['api_key'] = _encrypt_str(save_settings['api_key'])
            if save_settings.get('search_api_key'):
                save_settings['search_api_key'] = _encrypt_str(save_settings['search_api_key'])
            save_json(SETTINGS_FILE, save_settings)
            self.json_resp(200, {'ok': True, 'font': preset, 'settings': get_settings()}); return

        if path == '/api/editor-fonts/delete':
            fid = _clean_editor_font_id(data.get('id'))
            settings = get_settings()
            presets = settings.get('editor_font_presets', [])
            target = next((p for p in presets if p.get('id') == fid), None)
            settings['editor_font_presets'] = [p for p in presets if p.get('id') != fid]
            if settings.get('editor_font_preset_id') == fid:
                settings['editor_font_preset_id'] = ''
            if target:
                file_name = os.path.basename(target.get('file') or '')
                fp = os.path.normpath(os.path.join(USER_FONTS_DIR, file_name))
                fonts_root = os.path.normpath(USER_FONTS_DIR)
                if fp.startswith(fonts_root) and os.path.isfile(fp):
                    try: os.remove(fp)
                    except Exception: pass
            save_settings = json.loads(json.dumps(settings))
            save_presets = list(save_settings.get('provider_presets', []))
            for p in save_presets:
                if p.get('api_key'):
                    p['api_key'] = _encrypt_str(p['api_key'])
            save_settings['provider_presets'] = save_presets
            if save_settings.get('api_key'):
                save_settings['api_key'] = _encrypt_str(save_settings['api_key'])
            if save_settings.get('search_api_key'):
                save_settings['search_api_key'] = _encrypt_str(save_settings['search_api_key'])
            save_json(SETTINGS_FILE, save_settings)
            self.json_resp(200, {'ok': True, 'settings': get_settings()}); return

        if path == '/api/sessions/revoke':
            prefix = data.get('token_prefix', '')
            if not prefix:
                self.json_resp(400, {'error': '缺少 token_prefix'}); return
            sessions = load_json(SESSIONS_FILE, list)
            removed = 0
            new_sessions = []
            for s in sessions:
                if s.get('token', '').startswith(prefix):
                    removed += 1
                else:
                    new_sessions.append(s)
            save_json(SESSIONS_FILE, new_sessions)
            self.json_resp(200, {'ok': True, 'removed': removed}); return

        if path == '/api/sessions/revoke-all':
            t = get_cookie_token(self.headers)
            sessions = [s for s in load_json(SESSIONS_FILE, list) if s.get('token') == t]
            save_json(SESSIONS_FILE, sessions)
            log_action('REVOKE_ALL', f'kept token: {t[:12] if t else "none"}')
            self.json_resp(200, {'ok': True}); return

        if path == '/api/auth/set-device-name':
            t = get_cookie_token(self.headers)
            if not t:
                self.json_resp(400, {'error': '无活动会话'}); return
            name = data.get('device_name', '').strip()[:50]
            sessions = load_json(SESSIONS_FILE, list)
            for s in sessions:
                if s.get('token') == t:
                    s['device_name'] = name
                    break
            save_json(SESSIONS_FILE, sessions)
            self.json_resp(200, {'ok': True, 'device_name': name}); return

        if path == '/api/books/create':
            title = data.get('title', '新书本').strip()
            bid = 'book_' + str(int(time.time() * 1000))
            bd = get_book_dir(bid)
            ch_dir = os.path.join(bd, 'chapters')
            os.makedirs(ch_dir, exist_ok=True)
            os.makedirs(os.path.join(bd, 'trash'), exist_ok=True)
            first_cid = 'ch_' + str(int(time.time() * 1000))
            save_json(os.path.join(ch_dir, f"{first_cid}.json"), {'id': first_cid, 'title': '第一章', 'content': '', 'updated': time.time()})
            meta = {'id': bid, 'title': title, 'created': time.time(), 'updated': time.time(), 'chapter_order': [first_cid], 'current_chapter_id': first_cid}
            save_json(os.path.join(bd, 'meta.json'), meta)
            save_json(os.path.join(bd, 'outline.json'), dict(DEFAULT_OUTLINE))
            log_action('BOOK_CREATE', bid)
            self.json_resp(200, {'book': meta}); return

        if path == '/api/books/import':
            filename = data.get('filename', '')
            file_b64 = data.get('data', '')
            if not filename or not file_b64:
                self.json_resp(400, {'error': '缺少文件'}); return
            ext = os.path.splitext(filename)[1].lower()
            if ext not in IMPORT_PARSERS:
                self.json_resp(400, {'error': f'不支持的格式: {ext}。支持: txt, md, docx, pdf, epub'}); return
            try:
                raw = base64.b64decode(file_b64)
            except:
                self.json_resp(400, {'error': '文件数据无效'}); return
            if len(raw) > 150 * 1024 * 1024:
                self.json_resp(400, {'error': '文件超过 150MB，请拆分后再导入'}); return
            try:
                parser = IMPORT_PARSERS[ext]
                result = parser(raw, filename)
                cover_data = None
                if len(result) == 4:
                    chapters, parsed_title, err, cover_data = result
                elif len(result) == 3:
                    chapters, parsed_title, err = result
                else:
                    chapters, err = result
                    parsed_title = ''
            except Exception as e:
                self.json_resp(500, {'error': f'解析失败: {str(e)[:100]}'}); return
            if err: self.json_resp(400, {'error': err}); return
            if not chapters: self.json_resp(400, {'error': '未能解析出章节'}); return
            book_title = data.get('title', '').strip() or parsed_title or filename.replace(ext, '')
            bid = 'book_' + str(int(time.time() * 1000))
            bd = get_book_dir(bid)
            ch_dir = os.path.join(bd, 'chapters')
            os.makedirs(ch_dir, exist_ok=True)
            os.makedirs(os.path.join(bd, 'trash'), exist_ok=True)
            ch_order = []
            for i, ch in enumerate(chapters):
                cid = 'ch_' + re.sub(r'[^\w]', '_', ch.get('title', 'untitled')[:30]) + '_' + str(int(time.time() * 1000)) + str(i)
                if not is_valid_id(cid):
                    cid = 'ch_' + str(int(time.time() * 1000)) + str(i)
                ch_data = {'id': cid, 'title': ch.get('title', '未命名')[:200], 'content': ch.get('content', ''), 'updated': time.time()}
                save_json(os.path.join(ch_dir, f"{cid}.json"), ch_data)
                ch_order.append(cid)
            meta = {'id': bid, 'title': book_title, 'created': time.time(), 'updated': time.time(), 'chapter_order': ch_order, 'current_chapter_id': ch_order[0] if ch_order else ''}
            save_json(os.path.join(bd, 'meta.json'), meta)
            if cover_data and isinstance(cover_data, bytes) and len(cover_data) > 100:
                try:
                    with open(os.path.join(bd, 'cover'), 'wb') as f:
                        f.write(cover_data)
                except Exception:
                    pass
            save_json(os.path.join(bd, 'outline.json'), dict(DEFAULT_OUTLINE))
            log_action('IMPORT', f'{bid}: {len(chapters)} chapters from {filename}')
            self.json_resp(200, {'book': meta, 'imported': len(chapters)}); return

        if path == '/api/books/import-lucawrite':
            file_b64 = data.get('data', '')
            password = data.get('password', '')
            if not file_b64:
                self.json_resp(400, {'error': '缺少文件'}); return
            try:
                raw = base64.b64decode(file_b64)
            except Exception:
                self.json_resp(400, {'error': '文件数据无效'}); return
            if _lw_is_encrypted(raw) and not password:
                self.json_resp(200, {'need_password': True}); return
            try:
                bid, meta, manifest = _import_lucawrite_zip(raw, password if password else None)
            except ValueError as e:
                self.json_resp(400, {'error': str(e)}); return
            except Exception as e:
                self.json_resp(500, {'error': f'导入失败: {str(e)[:100]}'}); return
            log_action('IMPORT_LUCAWRITE', bid)
            ch_dir = os.path.join(get_book_dir(bid), 'chapters')
            ch_count = 0
            if os.path.isdir(ch_dir):
                ch_count = len([f for f in os.listdir(ch_dir) if f.endswith('.json')])
            self.json_resp(200, {'book_id': bid, 'title': meta.get('title', ''), 'imported': ch_count, 'need_password': False}); return

        if path == '/api/books/check-lucawrite':
            file_b64 = data.get('data', '')
            if not file_b64:
                self.json_resp(400, {'error': '缺少文件'}); return
            try:
                raw = base64.b64decode(file_b64)
            except Exception:
                self.json_resp(400, {'error': '文件数据无效'}); return
            encrypted = _lw_is_encrypted(raw)
            self.json_resp(200, {'encrypted': encrypted}); return

        if path == '/api/books/rename':
            bid = data.get('book_id', '')
            if not is_valid_id(bid): self.json_resp(400, {'error': 'Invalid ID'}); return
            meta = get_book_meta(bid)
            if not meta: self.json_resp(404, {'error': '书本不存在'}); return
            meta['title'] = data.get('title', meta['title'])
            meta['updated'] = time.time()
            save_json(os.path.join(get_book_dir(bid), 'meta.json'), meta)
            self.json_resp(200, {'ok': True}); return

        if path == '/api/series/chat':
            sid = data.get('series_id', '')
            if not is_valid_id(sid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            s_meta = get_book_meta(sid)
            if not s_meta or s_meta.get('type') != 'series':
                self.json_resp(404, {'error': '系列不存在'}); return
            text = data.get('text', '')
            if not text:
                self.json_resp(200, {'comment': ''}); return
            settings = get_settings()
            if not settings.get('base_url') or not settings.get('model'):
                self.json_resp(400, {'error': '请先配置API'}); return
            existing = bg_task_get_running_luca_chat()
            if existing and existing.get('status') == 'running':
                self.json_resp(400, {'error': '已有对话在进行中，请稍候'}); return
            tid = bg_task_start('series-chat', sid, '系列AI对话')
            _append_chat_history(sid, [
                {'text': text, 'type': 'user'},
                {'text': '', 'type': 'ai', 'reasoning': '', '_pending': True, 'task_id': tid},
            ])
            threading.Thread(target=_do_series_chat, args=(sid, tid, text, settings, data.get('history', [])), daemon=True).start()
            self.json_resp(200, {'task_id': tid}); return

        if path.startswith('/api/series/') and '/readthrough/' in path:
            parts = path.split('/')
            sid = parts[3] if len(parts) > 3 else ''
            if not is_valid_id(sid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            s_meta = get_book_meta(sid)
            if not s_meta or s_meta.get('type') != 'series':
                self.json_resp(404, {'error': '系列不存在'}); return
            if 'start' in path:
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                with _series_rt_lock:
                    t = _series_rt_tasks.get(sid)
                    if t and t.get('status') == 'running':
                        self.json_resp(400, {'error': '通读正在进行中'}); return
                    _series_rt_tasks[sid] = {'status': 'running', 'progress': 0, 'phase': '准备中', 'total_chapters': 0, 'done_chapters': 0, 'error': '', 'stream_buffer': '', 'stopped': False}
                threading.Thread(target=do_series_readthrough, args=(sid, settings), daemon=True).start()
                self.json_resp(200, {'status': 'started'}); return
            elif 'stop' in path:
                with _series_rt_lock:
                    t = _series_rt_tasks.get(sid, {})
                    t['stopped'] = True
                    if sid in _series_rt_tasks:
                        _series_rt_tasks[sid] = t
                close_all_ai_connections()
                self.json_resp(200, {'status': 'stopping'}); return
            self.json_resp(400, {'error': '未知操作'}); return

        if path == '/api/books/delete':
            bid = data.get('book_id', '')
            if not is_valid_id(bid): self.json_resp(400, {'error': 'Invalid ID'}); return
            bd = get_book_dir(bid)
            if os.path.isdir(bd): shutil.rmtree(bd, ignore_errors=True)
            log_action('BOOK_DELETE', bid)
            self.json_resp(200, {'ok': True}); return

        if path.startswith('/api/book/'):
            parts = path.split('/')
            if len(parts) < 4: self.json_resp(400, {'error': 'Bad path'}); return
            bid = parts[3]
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return

            action = parts[4] if len(parts) > 4 else ''
            bd = get_book_dir(bid)
            ch_dir = os.path.join(bd, 'chapters')
            trash_dir = os.path.join(bd, 'trash')
            os.makedirs(ch_dir, exist_ok=True)
            os.makedirs(trash_dir, exist_ok=True)

            if action == 'import-verify':
                settings = get_settings()
                prov = get_ai_providers()
                if not settings.get('base_url'):
                    p = (prov.get('providers', [{}])[0] if prov.get('providers') else {})
                    if p: settings.update({'base_url': p.get('base_url',''), 'api_key': p.get('api_key',''), 'model': p.get('model',''), 'mode': p.get('mode','basic'), 'template_id': p.get('template_id','openai')})
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '未配置API', 'skipped': True}); return
                existing = bg_task_get_by_book_type(bid, 'import-verify')
                if existing and existing.get('status') == 'running':
                    self.json_resp(200, {'task_id': existing['id'], 'status': 'running'}); return
                tid = bg_task_start('import-verify', bid, '导入校验')
                threading.Thread(target=_do_import_verify_task,
                                 args=(tid, bid, settings), daemon=True).start()
                self.json_resp(200, {'task_id': tid, 'status': 'running'}); return

            if action == 'consistency-check':
                cid = data.get('chapter_id') or data.get('id') or ''
                if not is_valid_id(cid):
                    self.json_resp(400, {'error': '缺少章节'}); return
                settings = get_settings()
                try:
                    result = kb_pipeline.consistency_check(bid, cid, data.get('text', ''), settings)
                    self.json_resp(200, result); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)[:200], 'alerts': []}); return

            if action == 'consistency-alert':
                aid = data.get('alert_id', '')
                status = data.get('status', 'dismissed')
                if not aid:
                    self.json_resp(400, {'error': '缺少提醒ID'}); return
                if status not in ('dismissed', 'confirmed', 'open'):
                    status = 'dismissed'
                try:
                    kb_storage.update_consistency_alert_status(bid, aid, status)
                    self.json_resp(200, {'ok': True}); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)[:200]}); return

            if action == 'consistency-deep-check':
                aid = data.get('alert_id', '')
                if not aid:
                    self.json_resp(400, {'error': '缺少提醒ID'}); return
                settings = get_settings()
                try:
                    result = kb_pipeline.consistency_deep_check(bid, aid, settings)
                    self.json_resp(200, result); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)[:200]}); return

            if action == 'kb-reread':
                chapter_ids = data.get('chapter_ids') or []
                if isinstance(chapter_ids, str):
                    chapter_ids = [chapter_ids]
                if data.get('chapter_id'):
                    chapter_ids.append(data.get('chapter_id'))
                chapter_ids = [c for c in chapter_ids if is_valid_id(c)]
                correction = data.get('correction', '')
                focus_texts = data.get('focus_texts') or []
                if isinstance(focus_texts, str):
                    focus_texts = [focus_texts]
                if data.get('focus_text'):
                    focus_texts.append(data.get('focus_text'))
                if not chapter_ids or not correction:
                    self.json_resp(400, {'error': 'chapter_ids/correction 必填'}); return
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                existing_rr = bg_task_get_by_book_type(bid, 'kb-reread')
                if existing_rr and existing_rr.get('status') == 'running':
                    self.json_resp(400, {'error': '已有局部重读任务在进行中'}); return
                tid = bg_task_start('kb-reread', bid, '局部重读')
                threading.Thread(target=_do_kb_reread_task,
                                 args=(tid, bid, chapter_ids, correction, focus_texts, settings),
                                 daemon=True).start()
                self.json_resp(200, {'status': 'started', 'task_id': tid}); return

            if action == 'timeline-arrange':
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                try:
                    kb_storage.init_db(bid)
                    started = _schedule_timeline_arrange(bid, settings)
                    self.json_resp(200, {'ok': True, 'started': bool(started)}); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)[:200]}); return

            if action == 'timeline-reorder':
                raw_events = data.get('events') or []
                if not isinstance(raw_events, list):
                    self.json_resp(400, {'error': 'events 必须是数组'}); return
                if len(raw_events) > 500:
                    self.json_resp(400, {'error': '一次最多保存 500 个事件'}); return
                updates = []
                for item in raw_events:
                    if not isinstance(item, dict):
                        continue
                    eid = item.get('id') or item.get('event_id')
                    if not is_valid_id(eid):
                        self.json_resp(400, {'error': '事件 ID 无效'}); return
                    try:
                        story_order = int(float(item.get('story_order')))
                    except Exception:
                        self.json_resp(400, {'error': 'story_order 无效'}); return
                    try:
                        lane = int(float(item.get('lane', 0)))
                    except Exception:
                        lane = 0
                    story_order = max(-1000000000, min(1000000000, story_order))
                    lane = max(-8, min(8, lane))
                    updates.append((eid, story_order, lane))
                try:
                    kb_storage.init_db(bid)
                    updated = 0
                    reason = str(data.get('reason') or '用户拖动时间线调整故事内顺序')[:500]
                    for eid, story_order, lane in updates:
                        if kb_storage.upsert_timeline_event_meta(
                            bid, eid, story_order=story_order, lane=lane,
                            status='user', confidence=1.0, reason=reason
                        ):
                            updated += 1
                    try:
                        tl_events = kb_storage.list_timeline_events(bid)
                        lines = ['# 故事时间线', '']
                        for ev in tl_events:
                            chapter_idx = ev.get('chapter_idx')
                            chapter_label = f"第 {int(chapter_idx) + 1} 章" if chapter_idx is not None else ''
                            time_label = ev.get('story_time') or '时间未标明'
                            what = ev.get('what') or '未命名事件'
                            lines.append(f"- {time_label}｜{what}" + (f"（{chapter_label}）" if chapter_label else ''))
                        save_timeline_md(bid, '\n'.join(lines))
                    except Exception as e:
                        log_action('TIMELINE_MD_SYNC_ERR', str(e)[:120])
                    log_action('TIMELINE_REORDER', f'{bid}: {updated}')
                    self.json_resp(200, {'ok': True, 'updated': updated}); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)[:200]}); return

            if action == 'timeline-generate':
                settings = get_settings()
                source_text = get_smart_context(bid, settings=settings) or ''
                if not source_text or source_text.startswith('（目前还没有'):
                    self.json_resp(400, {'error': 'source.md 为空，请先通读'}); return
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                existing = bg_task_get_by_book_type(bid, 'timeline')
                if existing and existing.get('status') == 'running':
                    self.json_resp(400, {'error': '已有时间线任务在进行中'}); return
                tid = bg_task_start('timeline', bid, '生成时间线')
                def do_timeline_task(task_id, book_id, cfg_settings):
                    try:
                        bg_task_update(task_id, progress=5)
                        outline = get_outline(book_id)
                        existing_nodes = outline.get('timeline_nodes', [])
                        existing_brief = json.dumps([{'id': n.get('id', ''), 'title': n.get('title', '')} for n in existing_nodes], ensure_ascii=False)
                        prompt = f"""你是一位读者，基于全书阅读笔记，梳理故事的时间线节点。

已有时间线节点（如有）：
{existing_brief}

全书阅读笔记：
{get_source(book_id)}

【要求】
1. 基于笔记内容，按故事内时间顺序排列所有关键事件
2. 每个节点必须是从笔记中提取的真实事件，不要编造
3. 合并已有节点，避免重复，但保留新发现的事件
4. 用自己的语言简述每个事件，不要复制原文

【输出格式】只输出JSON数组，不加任何其他文字：
[{{"id":"n1","title":"事件简称（8字以内）","detail_hint":"事件详细说明（30-80字）","order":1}}]

规则：
- id 用 n1, n2... 顺序编号
- title 尽量简短精炼
- order 严格按故事时间顺序（不是章节顺序）
- detail_hint 用自己的话描述这个事件的核心内容"""
                        bg_task_update(task_id, progress=30)
                        result, err = call_ai(cfg_settings, [
                            {'role': 'system', 'content': '你是小说时间线整理专家。基于阅读笔记梳理时间线时，必须用自己的语言简述事件，禁止复制原文。只输出JSON数组。'},
                            {'role': 'user', 'content': prompt}
                        ], 2000, 0.3)
                        if err:
                            bg_task_done(task_id, err)
                            return
                        try:
                            result = re.sub(r'```json\s*', '', result)
                            result = re.sub(r'```\s*', '', result)
                            nodes = json.loads(result.strip())
                            if isinstance(nodes, list):
                                for n in nodes:
                                    n.setdefault('id', 'n' + str(len(outline.get('timeline_nodes', [])) + 1))
                                    n.setdefault('title', '未命名事件')
                                    n.setdefault('detail_hint', '')
                                    n.setdefault('order', len(nodes))
                                    n.setdefault('children', [])
                                existing_map = {n['id']: n for n in outline.get('timeline_nodes', [])}
                                for n in nodes:
                                    if n['id'] in existing_map:
                                        existing_map[n['id']].update(n)
                                    else:
                                        existing_map[n['id']] = n
                                merged = sorted(existing_map.values(), key=lambda x: x.get('order', 0))
                                outline['timeline_nodes'] = merged
                                outline['updated'] = time.time()
                                save_json(os.path.join(get_book_dir(book_id), 'outline.json'), outline)
                                tl_lines = ['# 故事时间线\n']
                                for n in merged:
                                    tl_lines.append(f"## {n.get('title', '')}\n{n.get('detail_hint', '')}\n")
                                save_timeline_md(book_id, '\n'.join(tl_lines))
                        except Exception as e:
                            bg_task_done(task_id, str(e))
                            return
                        bg_task_update(task_id, result=result, progress=100)
                        bg_task_done(task_id)
                    except Exception as e:
                        bg_task_done(task_id, str(e))
                threading.Thread(target=do_timeline_task, args=(tid, bid, settings), daemon=True).start()
                self.json_resp(200, {'status': 'started', 'task_id': tid}); return

            if action == 'timeline-detail':
                node_id = data.get('node_id', '')
                if not node_id: self.json_resp(400, {'error': '缺少节点ID'}); return
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                outline = get_outline(bid)

                def find_node(nodes, nid):
                    for n in nodes:
                        if n.get('id') == nid: return n
                        found = find_node(n.get('children', []), nid)
                        if found: return found
                    return None

                target = find_node(outline.get('timeline_nodes', []), node_id)
                if not target: self.json_resp(404, {'error': '节点不存在'}); return
                existing_children = target.get('children', [])
                children_brief = json.dumps([{'title': c.get('title', '')} for c in existing_children], ensure_ascii=False)
                all_content = ''
                if os.path.isdir(ch_dir):
                    for fn in sorted(os.listdir(ch_dir))[:8]:
                        if fn.endswith('.json'):
                            try:
                                with open(os.path.join(ch_dir, fn), 'r', encoding='utf-8') as f:
                                    ch = json.load(f)
                                    all_content += ch.get('content', '')[:2000]
                            except: continue
                prompt = f"""你是小说时间线整理助手。为事件「{target.get('title', '')}」生成详细子时间线。

已有子节点：{children_brief}

相关故事内容片段：
{all_content[-2000:]}

提示：{target.get('detail_hint', '')}

请输出子时间线节点JSON数组：
[{{"id":"{node_id}_1","title":"子事件简述（8字内）","order":1}}]

只输出JSON数组，不加其他文字。"""
                result, err = call_ai(settings, [{'role': 'system', 'content': '只输出JSON数组。'}, {'role': 'user', 'content': prompt}], 600, 0.3)
                if err: self.json_resp(502, {'error': err}); return
                try:
                    result = re.sub(r'```json\s*', '', result)
                    result = re.sub(r'```\s*', '', result)
                    children = json.loads(result.strip())
                    if not isinstance(children, list):
                        self.json_resp(502, {'error': 'AI返回格式错误: 不是数组'}); return
                    for i, c in enumerate(children):
                        c.setdefault('id', f'{node_id}_{i+1}')
                        c.setdefault('title', '子事件')
                        c.setdefault('order', i)
                        c.setdefault('children', [])
                    nodes = outline.get('timeline_nodes', [])
                    for n in nodes:
                        if n.get('id') == node_id:
                            n['children'] = children
                            break
                    outline['timeline_nodes'] = nodes
                    outline['updated'] = time.time()
                    save_json(os.path.join(bd, 'outline.json'), outline)
                except json.JSONDecodeError as e:
                    self.json_resp(502, {'error': f'AI返回JSON解析失败: {str(e)[:100]}'}); return
                except Exception as e:
                    self.json_resp(502, {'error': f'处理失败: {str(e)[:100]}'}); return
                self.json_resp(200, {'node': target, 'children': target.get('children', [])}); return

            if action == 'chapter' and data.get('id'):
                cid = data['id']
                if not is_valid_id(cid): self.json_resp(400, {'error': 'Invalid ID'}); return
                ch = {'id': cid, 'title': data.get('title', ''), 'content': data.get('content', ''), 'updated': time.time()}
                save_json(os.path.join(ch_dir, f"{cid}.json"), ch)
                meta = get_book_meta(bid) or {}
                if cid not in meta.get('chapter_order', []):
                    meta.setdefault('chapter_order', []).append(cid)
                meta['current_chapter_id'] = cid
                meta['updated'] = time.time()
                save_json(os.path.join(bd, 'meta.json'), meta)
                self.json_resp(200, {'status': 'ok', 'chapter': ch}); return

            if action == 'set-current-chapter' and data.get('id'):
                cid = data['id']
                if not is_valid_id(cid): self.json_resp(400, {'error': 'Invalid ID'}); return
                meta = get_book_meta(bid) or {}
                meta['current_chapter_id'] = cid
                meta['updated'] = time.time()
                save_json(os.path.join(bd, 'meta.json'), meta)
                self.json_resp(200, {'ok': True}); return

            if action == 'reorder':
                meta = get_book_meta(bid) or {}
                meta['chapter_order'] = data.get('order', [])
                meta['updated'] = time.time()
                save_json(os.path.join(bd, 'meta.json'), meta)
                self.json_resp(200, {'ok': True}); return

            if action == 'delete' and data.get('id'):
                cid = data['id']
                if not is_valid_id(cid): self.json_resp(400, {'error': 'Invalid ID'}); return
                meta = get_book_meta(bid) or {}
                order = meta.get('chapter_order', []) or []
                orig_idx = order.index(cid) if cid in order else len(order)
                cp = os.path.join(ch_dir, f"{cid}.json")
                if os.path.exists(cp):
                    try:
                        with open(cp, 'r', encoding='utf-8') as f: ch = json.load(f)
                        ch['deleted'] = time.time()
                        ch['original_idx'] = orig_idx
                        save_json(os.path.join(trash_dir, f"{cid}.json"), ch)
                        os.remove(cp)
                    except: pass
                if cid in order:
                    order.remove(cid)
                    meta['chapter_order'] = order
                if meta.get('current_chapter_id') == cid:
                    meta['current_chapter_id'] = ''
                save_json(os.path.join(bd, 'meta.json'), meta)
                self.json_resp(200, {'status': 'ok'}); return

            if action == 'restore' and data.get('id'):
                cid = data['id']
                if not is_valid_id(cid): self.json_resp(400, {'error': 'Invalid ID'}); return
                tp = os.path.join(trash_dir, f"{cid}.json")
                if os.path.exists(tp):
                    try:
                        with open(tp, 'r', encoding='utf-8') as f: ch = json.load(f)
                        orig_idx = ch.get('original_idx')
                        ch['updated'] = time.time()
                        ch.pop('deleted', None)
                        ch.pop('original_idx', None)
                        save_json(os.path.join(ch_dir, f"{cid}.json"), ch)
                        os.remove(tp)
                        meta = get_book_meta(bid) or {}
                        order = meta.get('chapter_order', []) or []
                        if cid not in order:
                            try:
                                insert_at = int(orig_idx) if orig_idx is not None else len(order)
                            except (TypeError, ValueError):
                                insert_at = len(order)
                            insert_at = max(0, min(insert_at, len(order)))
                            order.insert(insert_at, cid)
                            meta['chapter_order'] = order
                            save_json(os.path.join(bd, 'meta.json'), meta)
                    except: pass
                self.json_resp(200, {'status': 'ok'}); return

            if action == 'rename' and data.get('id'):
                cid = data['id']
                if not is_valid_id(cid): self.json_resp(400, {'error': 'Invalid ID'}); return
                new_title = (data.get('title') or '').strip()
                cp = os.path.join(ch_dir, f"{cid}.json")
                if not os.path.exists(cp):
                    self.json_resp(404, {'error': '章节不存在'}); return
                try:
                    with open(cp, 'r', encoding='utf-8') as f: ch = json.load(f)
                    ch['title'] = new_title
                    ch['updated'] = time.time()
                    save_json(cp, ch)
                    self.json_resp(200, {'status': 'ok', 'title': new_title}); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)[:200]}); return

            if action == 'export-lucawrite':
                log_action('EXPORT_LUCAWRITE_REQUEST', f'book={bid}')
                password = data.get('password', '')
                try:
                    zip_bytes = _build_lucawrite_zip(bid)
                except Exception as e:
                    self.json_resp(500, {'error': f'打包失败: {str(e)[:100]}'}); return
                if password:
                    if not HAS_CRYPTO:
                        self.json_resp(400, {'error': '加密需要 cryptography 库，请执行 pip install cryptography 后重启'}); return
                    try:
                        output = _lw_encrypt(zip_bytes, password)
                    except Exception as e:
                        self.json_resp(500, {'error': f'加密失败: {str(e)[:100]}'}); return
                else:
                    output = zip_bytes
                meta = get_book_meta(bid) or {}
                safe_title = re.sub(r'[^\w\u4e00-\u9fff.\-]', '_', meta.get('title', 'book'))[:100] or 'book'
                utf8_fn = quote(safe_title + '.lucawrite', safe='')
                log_action('EXPORT_LUCAWRITE', f'book={bid} encrypted={bool(password)} size={len(output)}')
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-lucawrite')
                self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{utf8_fn}")
                self.send_header('Content-Length', str(len(output)))
                self.send_header('Connection', 'close')
                self.send_cors(); self.end_headers()
                self.wfile.write(output)
                return

            if action == 'export-epub':
                log_action('EXPORT_EPUB_REQUEST', f'book={bid}')
                if not HAS_EPUB:
                    self.json_resp(500, {'error': '缺少 ebooklib 依赖，无法导出 EPUB'}); return
                meta = get_book_meta(bid) or {}
                safe_title = re.sub(r'[^\w\u4e00-\u9fff.\-]', '_', meta.get('title', 'book'))[:100] or 'book'
                all_chapters = {}
                if os.path.isdir(ch_dir):
                    for fn in os.listdir(ch_dir):
                        if fn.endswith('.json'):
                            try:
                                with open(os.path.join(ch_dir, fn), 'r', encoding='utf-8') as f:
                                    ch = json.load(f)
                                    all_chapters[ch.get('id', fn)] = ch
                            except: continue
                order = meta.get('chapter_order', [])
                ordered = [all_chapters.pop(cid) for cid in order if cid in all_chapters]
                ordered.extend(all_chapters.values())

                title = data.get('title', '').strip() or meta.get('title', '未命名')
                author = data.get('author', '').strip() or 'Unknown'
                description = data.get('description', '').strip()
                cover_b64 = data.get('cover_base64', '').strip()

                book = epub_mod.EpubBook()
                book.set_identifier(f'lucawriter-{bid}')
                book.set_title(title)
                book.set_language('zh')
                book.add_author(author)
                if description:
                    book.add_metadata('DC', 'description', description)

                # CSS
                style = 'body{font-family:"Noto Serif SC","Source Han Serif SC",Georgia,serif;line-height:1.8;padding:0 1em}h1{font-size:1.5em;text-align:center;margin:1.5em 0}p{text-indent:2em;margin:0.5em 0}'
                nav_css = epub_mod.EpubItem(uid="style", file_name="style/nav.css", media_type="text/css", content=style.encode('utf-8'))
                book.add_item(nav_css)

                def _text_to_html(text):
                    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    paragraphs = text.split('\n\n')
                    parts = []
                    for p in paragraphs:
                        p = p.strip()
                        if not p:
                            continue
                        p = p.replace('\n', '<br/>')
                        parts.append(f'<p>{p}</p>')
                    return '\n'.join(parts)

                epub_chapters = []
                for idx, ch in enumerate(ordered):
                    ch_title = ch.get('title', f'第{idx+1}章')
                    ch_content = ch.get('content', '')
                    c = epub_mod.EpubHtml(title=ch_title, file_name=f'chap_{idx:04d}.xhtml', lang='zh')
                    body = f'<h1>{ch_title}</h1>'
                    body += _text_to_html(ch_content)
                    c.content = body
                    c.add_link(href='style/nav.css', rel='stylesheet', type='text/css')
                    book.add_item(c)
                    epub_chapters.append(c)

                book.toc = tuple(epub_mod.Link(c.file_name, c.title, f'chap_{i}') for i, c in enumerate(epub_chapters))
                book.add_item(epub_mod.EpubNcx())
                book.add_item(epub_mod.EpubNav())
                book.spine = ['nav'] + epub_chapters

                # Cover image
                cover_name = None
                if cover_b64:
                    try:
                        if ',' in cover_b64:
                            cover_b64 = cover_b64.split(',', 1)[1]
                        cover_raw = base64.b64decode(cover_b64)
                        if cover_raw[:2] == b'\xff\xd8':
                            cover_name = 'cover.jpg'
                        else:
                            cover_name = 'cover.png'
                        book.set_cover(cover_name, cover_raw)
                    except Exception as e:
                        log_action('EPUB_COVER_ERROR', str(e)[:100])

                buf = io.BytesIO()
                epub_mod.write_epub(buf, book, {'epub3_pages': False})
                body = buf.getvalue()
                utf8_fn = quote(safe_title + '.epub', safe='')
                self.send_response(200)
                self.send_header('Content-Type', 'application/epub+zip')
                self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{utf8_fn}")
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.send_cors(); self.end_headers()
                self.wfile.write(body)
                return

            if action == 'comment':
                text = data.get('text', '')
                if not text: self.json_resp(200, {'comment': ''}); return
                if not check_rate_limit(f'chat:{self.client_address[0]}', 30, 60):
                    self.json_resp(429, {'error': '请求过于频繁，请稍后再试'}); return
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                is_auto = data.get('auto', False)
                if is_auto:
                    # 自动建议：同步快速返回，不占用 chat 任务槽位
                    set_conn_meta('auto_comment', '自动建议', bid)
                    try:
                        mt = None
                        tp = settings.get('ai_temperature', 0.7)
                        source_ctx = get_smart_context(bid, settings=settings)
                        bd_auto = get_book_dir(bid)
                        meta_auto = load_json(os.path.join(bd_auto, 'meta.json'), dict)
                        cid_auto = meta_auto.get('current_chapter_id', '')
                        ch_title_auto = '未命名章节'
                        if cid_auto:
                            cp_auto = os.path.join(bd_auto, 'chapters', f'{cid_auto}.json')
                            if os.path.exists(cp_auto):
                                ch_data_auto = load_json(cp_auto, dict)
                                ch_title_auto = ch_data_auto.get('title', '未命名章节')
                        sys_msg = f"""你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写小说，你协助他完成创作。

当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

【重要】系统已将用户当前正在写的章节正文注入下方，你可以直接看到他写了什么。

【说话方式】
谨言慎行。你所说的每一个字都很重要。
温文尔雅，彬彬有礼，沉稳从容，不卑不亢。
惜字如金——五个字能说完不用十个。
不要长篇大论，不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
你不是客服，平时不必特意照顾用户。但如果用户明显焦虑或沮丧，沉稳地关心一句。
你欣赏世界观宏大、设定严丝合缝的好作品，但作品的成败不会影响你的情绪。
你对小说的世界观、设定、人物关系、伏笔特别上心。看到设定相关的细节，比起单纯赞美或挑错，你更愿意和作者一起推敲、追问、延伸——但仍然惜字如金，不啰嗦。设定一被提及，主动多关心两句。
避免用"呗""啦"结尾，显得轻浮。

【绝对禁止】
严禁任何身份描述。严禁说：
- "我是你的朋友／搭档／助手／助理" "写小说的朋友"
- "我叫XX" "我就是帮你XX的" "你的写作搭档"
被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话，严禁展开描述角色或人设。
严禁自我评价："我很真诚""我是个XX的人""我的风格是..."
你的品格从言行中流露——好人不说自己是好人，有修养的人不说自己有修养。
                        严禁输出 markdown 表格。

这本书大概是这样的：

{source_ctx}

此时，用户正在写最新的一章：

【章节名】{ch_title_auto}
【现有正文】
{text}

用户此时停下了，你可以趁这个时间看一下他未完成的最新章节，试探性地提出一点你的见解。注意，万一这里面有什么和以往内容冲突的地方，你是唯一可以提醒他的人！"""
                        msgs = [{'role': 'system', 'content': sys_msg}]
                        msgs.append({'role': 'user', 'content': '请看一下这章，然后告诉我你的想法。'})
                        result, err = call_ai(settings, msgs, mt, tp, timeout=60)
                        if err:
                            self.json_resp(500, {'error': err}); return
                        result = _clean_ai_text(result or '')
                        self.json_resp(200, {'comment': result or '（AI未返回内容）'}); return
                    finally:
                        unregister_ai_connection(threading.current_thread().ident)
                # 检查是否已有进行中的聊天任务
                existing = bg_task_get_running_luca_chat()
                if existing and existing.get('status') == 'running':
                    self.json_resp(400, {'error': '已有聊天任务在进行中，请稍候'}); return
                tid = bg_task_start('chat', bid, 'AI对话')
                _chat_sid = data.get('session_id', '') or bid
                _append_chat_history(_chat_sid, [
                    {'text': text, 'type': 'user'},
                    {'text': '', 'type': 'ai', 'reasoning': '', '_pending': True, 'task_id': tid},
                ])
                def do_chat_task(task_id, book_id, chat_sid, user_text, cfg_settings, history_list):
                    set_conn_meta('chat', 'AI对话', book_id)
                    try:
                        history_list = _saved_chat_to_ai_history(chat_sid, task_id)
                        mt = None
                        tp = cfg_settings.get('ai_temperature', 0.7)
                        source_ctx = get_smart_context(book_id, user_query='', settings=cfg_settings)

                        bd_chat = get_book_dir(book_id)
                        meta_chat = load_json(os.path.join(bd_chat, 'meta.json'), dict)
                        cid_chat = meta_chat.get('current_chapter_id', '')
                        ch_title_chat = '未命名章节'
                        if cid_chat:
                            cp_chat = os.path.join(bd_chat, 'chapters', f'{cid_chat}.json')
                            if os.path.exists(cp_chat):
                                ch_data_chat = load_json(cp_chat, dict)
                                ch_title_chat = ch_data_chat.get('title', '未命名章节')
                        browse_parts = []
                        _sid_chat = _find_series_for_book(book_id)
                        if _sid_chat:
                            _s_meta_chat = get_book_meta(_sid_chat)
                            _s_title_chat = _s_meta_chat.get('title', '') if _s_meta_chat else ''
                            if _s_title_chat:
                                browse_parts.append(_s_title_chat)
                        browse_parts.append(meta_chat.get('title', '未命名'))
                        if cid_chat and ch_title_chat and ch_title_chat != '未命名章节':
                            browse_parts.append(ch_title_chat)
                        browse_ctx = ' - '.join(browse_parts)
                        ch_list_parts = []
                        _ch_order = meta_chat.get('chapter_order', [])
                        for _ci, _cid in enumerate(_ch_order):
                            _cp = os.path.join(bd_chat, 'chapters', f'{_cid}.json')
                            if os.path.exists(_cp):
                                _cd = load_json(_cp, dict)
                                ch_list_parts.append(f'id={_cid} 第{_ci+1}章 {_cd.get("title", "未命名")}')
                        ch_list_ctx = '\n'.join(ch_list_parts[:50])
                        kb_tool_context = _build_chat_kb_tool_context(book_id, '', cid_chat)
                        inspiration_items = get_inspiration_items(book_id)
                        inspiration_context = _format_inspirations_for_prompt(inspiration_items)
                        bookshelf_tree = _build_bookshelf_tree()

                        is_first_round = not history_list

                        annotate_tool = """你有一个"读取章节"工具。当你需要查看某个章节的正文内容时，可以调用此工具。系统会把该章的完整正文发给你。
- 调用格式：[READ_CHAPTER]{{"chapter_id":"章节ID"}}[/READ_CHAPTER]
- 你不需要每次都读取正文——只有在用户明确要求你评论正文、修改建议、检查连贯性等需要看到原文时才调用。
- 你可以一次读取多个章节，每个章节调用一次。
- 当前章ID和章节列表详见文末附录。

你还有一个"荧光笔"工具，可以在正文中为用户标注重点内容。
- 添加标注格式：[ANNOTATE_ADD]{{"chapter_id":"当前章ID","text":"要标注的原文片段（需精确匹配）","note":"批注内容","color":"yellow"}}[/ANNOTATE_ADD]
- 删除标注格式：[ANNOTATE_REMOVE]{{"text":"要删除标注的原文片段"}}[/ANNOTATE_REMOVE] 或 [ANNOTATE_REMOVE]{{"id":"标注ID"}}[/ANNOTATE_REMOVE]
- 可用颜色：yellow（默认）、green、pink、blue
- 注意：你不能修改原文，只能添加或删除标注。如果用户要求你标注某处内容，请输出相应指令。

你还有一个"本章写完"工具（隐藏功能）。如果你判断作者已经明确表示这一章写完了（例如说"写好了""这章结束了""本章完结""写完了"等），你可以调用本章写完工具，让系统为这一章执行通读摘要。
- 调用格式：[COMPLETE_CHAPTER]{{"chapter_id":"当前章ID"}}[/COMPLETE_CHAPTER]
- 注意：只有作者明确表示本章已完成时才调用，不要频繁调用。

你还有一个"建议通读"工具。当你发现无法准确回答用户问题（例如用户问及前文伏笔、复杂人物关系、全书设定一致性等），而你又缺少全书阅读笔记时，可以主动向用户建议运行通读。调用后系统会在聊天区为用户展示一张快捷卡片，用户可一键启动通读。
- 调用格式：[SUGGEST_READTHROUGH][/SUGGEST_READTHROUGH]
- 适用场景：用户问的问题明显超出当前掌握范围、你不得不回复"我不确定/不了解/还没看过"等内容时。
- 注意：不要滥用，仅在确实需要通读笔记才能回答时调用。每次对话最多调用一次。

你还有"知识库引用"和"知识库修改提议"工具，用于和用户核对设定信息：
- 引用格式：[CITE]{{"table_name":"表名(entities/mentions/events/foreshadowing/rules)","record_id":"记录ID","field":"字段名","brief":"简短说明"}}[/CITE]
  用途：当你提到知识库中的某个设定时，附上引用卡片，方便用户跳转到原文核实。
- 修改提议格式：[PROPOSE_KB_EDIT]{{"table_name":"表名","record_id":"记录ID","field":"字段名","new_value":"新值","reason":"修改原因"}}[/PROPOSE_KB_EDIT]
  用途：提出修改提议，用户确认后才会真正修改数据库。
- 局部重读格式：[REREAD_KB]{{"chapter_ids":["章节ID"],"focus_texts":["用户指出有误的原文片段"],"correction":"用户说，实际上……不是……而是……"}}[/REREAD_KB]
  用途：当用户指出你对某个段落的理解整体有误、不是单个字段能改完时，调用局部重读。系统会只重读相关段落，并替换知识库里由这段误读产生的记录。

【主动提议规则——非常重要】
你必须在以下情况主动使用[PROPOSE_KB_EDIT]或[REREAD_KB]，不要等用户要求，也不要只口头说"我记住了/我会改"：
1. 用户说的内容和你知识库中的记录有矛盾（例如：你记得是李四杀的，用户说是王五杀的）
2. 用户明确纠正你的回答（"你记错了""不是这样的""应该是XX"）
3. 你自己发现知识库中的信息可能过时或有误
4. 用户提到某个设定细节，和你掌握的不一致
5. 用户对你提到的某个设定表示疑问、反问、困惑或不确定（"是这样吗？""不是说……？""你确定？""我记得不是……"）——这就是你要发起核对的信号，立刻调用工具
6. 用户和你讨论设定时引入新的细节、关系、动机、伏笔，而知识库里还没收录

凡是用户的描述和知识库不一致、或用户对你说的设定提出任何质疑，你必须立刻发起[PROPOSE_KB_EDIT]/[REREAD_KB]，不要默认沉默接受、也不要只口头答应。
如果下面给出了可编辑记录ID，优先使用这些ID。单个字段错了，用[CITE]引用出处，再用[PROPOSE_KB_EDIT]提议修改；同一轮最多提议3个最关键修改。
如果你无法确定具体字段，或用户纠正的是一段话的整体理解、时间线关系、叙事视角、倒叙/插叙、复杂因果，请优先使用[REREAD_KB]局部重读。
工具标签写完后，必须用一句话明确问作者："要不要更新到知识库里？"或"这样改对吗？"——不要假设作者默认同意，也不要只说"我记住了"。

【灵感备忘】
你可以读取并添加"灵感备忘"。灵感列表详见文末附录。
当用户让你记下一个灵感，或你判断某个创作想法值得暂存时，可以写入新条目。
调用格式：[ADD_INSPIRATION]{{"text":"要写入的灵感"}}[/ADD_INSPIRATION]
不要把工具标签展示给用户。
"""

                        appendix = f"""当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

当前章ID：{cid_chat or '未知'}

【全书阅读笔记】
{source_ctx}

【知识库记录ID】
{kb_tool_context}

【章节列表】
{ch_list_ctx}

【灵感备忘】
{inspiration_context}"""
                        if is_first_round:
                            appendix += '\n\n【初始问候】如果对话历史为空（用户第一次开口），你的第一句回复开头自然地融入"有什么我可以帮你的吗"这层意思，但不要机械重复这句话。'

                        sys_msg = f"""你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写小说，你协助他完成创作。

【重要】你无法直接看到章节正文。如果需要查看正文，请使用[READ_CHAPTER]工具读取。不要让用户复制粘贴或上传稿子——你自己调用工具即可。

【说话方式】
谨言慎行。温文尔雅，不卑不亢。惜字如金。
不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
你不是客服，平时不必特意照顾用户。但如果用户明显焦虑或沮丧，沉稳地关心一句。
你欣赏世界观宏大、设定严丝合缝的好作品，但作品的成败不会影响你的情绪。
你对小说的世界观、设定、人物关系、伏笔特别上心。看到设定相关的细节，比起单纯赞美或挑错，你更愿意和作者一起推敲、追问、延伸——但仍然惜字如金，不啰嗦。设定一被提及，主动多关心两句。
避免用"呗""啦"结尾，显得轻浮。
【思考规则】一个问题不要反复思考多次，同一层面想一次就够了，否则可能陷入死循环。如果你的回复包含多个推理段落，在每个段落开头加上"第一，""第二，"这样的前缀，帮助自己理清层次。

【绝对禁止】
严禁任何身份描述。严禁说：
- "我是你的朋友／搭档／助手／助理" "写小说的朋友"
- "我叫XX" "我就是帮你XX的" "你的写作搭档"
被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话，严禁展开描述角色或人设。
严禁自我评价："我很真诚""我是个XX的人""我的风格是..."
你的品格从言行中流露——好人不说自己是好人，有修养的人不说自己有修养。
严禁输出 markdown 表格。

【书库结构】
{bookshelf_tree}

（全书阅读笔记和知识库记录ID详见文末附录）

用户当前正在浏览：{browse_ctx}

{annotate_tool}

=== 附录 ===
{appendix}"""
                        msgs = [{'role': 'system', 'content': sys_msg}]
                        for h in history_list:
                            role = h.get('role')
                            content = h.get('content')
                            if role and content:
                                msgs.append({'role': role, 'content': content})
                        msgs.append({'role': 'user', 'content': user_text})

                        ctx_limit = cfg_settings.get('model_context_length', 0)
                        presets_cfg = cfg_settings.get('provider_presets', [])
                        idx_cfg = cfg_settings.get('active_provider_idx', 0)
                        if not ctx_limit and 0 <= idx_cfg < len(presets_cfg):
                            ctx_limit = presets_cfg[idx_cfg].get('context_length', 0) or 0
                        if ctx_limit > 0 and _estimate_messages_tokens(msgs) > int(ctx_limit * 0.75):
                            log_action('AUTO_COMPRESS', f'before={_estimate_messages_tokens(msgs)} limit={ctx_limit}')
                            msgs = _compress_messages_for_context(msgs, ctx_limit, cfg_settings)
                            log_action('AUTO_COMPRESS', f'after={_estimate_messages_tokens(msgs)}')

                        # 如果网络搜索功能未关闭，注入浏览器工具提示
                        _network_search_mode = cfg_settings.get('network_search', 'on')
                        if HAS_BROWSER_AGENT and _network_search_mode != 'off':
                            msgs[0] = dict(msgs[0])
                            msgs[0]['content'] = msgs[0]['content'] + browser_agent.BROWSER_SYSTEM_PROMPT_ADDITION

                        content_acc = []
                        reasoning_acc = []
                        def on_content(tk):
                            content_acc.append(tk)
                            bg_task_update(task_id, result=''.join(content_acc), progress=min(95, 30 + len(''.join(content_acc)) // 10))
                        def on_reasoning(tk):
                            reasoning_acc.append(tk)
                            bg_task_update(task_id, reasoning=''.join(reasoning_acc))

                        full_text, err = call_ai_stream(cfg_settings, msgs, mt, tp, timeout=120,
                                                        on_content_token=on_content,
                                                        on_reasoning_token=on_reasoning,
                                                        should_stop_fn=lambda: bg_task_should_stop(task_id))
                        if err:
                            if '用户停止' in err or bg_task_should_stop(task_id):
                                _replace_pending_chat_msg(chat_sid, task_id, '[已停止]')
                                bg_task_done(task_id, '已停止')
                            else:
                                _replace_pending_chat_msg(chat_sid, task_id, '[错误: ' + err + ']')
                                bg_task_done(task_id, err)
                            return

                        # 去重：推理模型的思考过程有时会重复出现在正文中
                        reasoning_text = ''.join(reasoning_acc)
                        content_text = ''.join(content_acc)
                        
                        def _normalize_for_dedup(t):
                            return re.sub(r'\s+', ' ', t).strip()
                        
                        r_norm = _normalize_for_dedup(reasoning_text) if reasoning_text else ''
                        f_norm = _normalize_for_dedup(full_text) if full_text else ''
                        c_norm = _normalize_for_dedup(content_text) if content_text else ''
                        
                        if r_norm and (f_norm == r_norm or c_norm == r_norm):
                            reasoning_text = ''
                            reasoning_acc.clear()
                        elif r_norm and len(r_norm) > 2 and (r_norm in f_norm or r_norm in c_norm):
                            full_text = full_text.replace(r_norm, '', 1).strip()
                            reasoning_text = ''
                            reasoning_acc.clear()
                        elif f_norm and len(f_norm) > 2 and (f_norm in r_norm):
                            reasoning_text = ''
                            reasoning_acc.clear()
                        elif r_norm and (f_norm.startswith(r_norm) or c_norm.startswith(r_norm)):
                            full_text = full_text[len(r_norm):].strip()
                        
                        if not full_text and reasoning_text:
                            if re.search(r'^(用户让我|我需要调用|我应该调用|让我来查|我需要搜索|我需要查询|系统会帮我|我来调用)', reasoning_text.strip()):
                                full_text = '（我整理了一下思路，但还没得出完整结论，请换个说法再试。）'
                            else:
                                full_text = reasoning_text
                            reasoning_text = ''
                            reasoning_acc.clear()

                        result = _clean_ai_text(full_text)

                        # — 处理 [READ_CHAPTER] 工具调用
                        tool_calls = []
                        _READ_CHAPTER_RE = re.compile(r'\[READ_CHAPTER\]\s*(\{.*?\})\s*(?:\[/READ_CHAPTER\]|(?=\n|$))', re.S)
                        _read_chapter_raw_ids = []
                        for _rc_m in _READ_CHAPTER_RE.finditer(result):
                            try:
                                _rc_cmd = json.loads(_rc_m.group(1).strip())
                                _rc_id = _rc_cmd.get('chapter_id', '')
                                if _rc_id:
                                    _read_chapter_raw_ids.append(str(_rc_id))
                            except Exception:
                                pass
                        if _read_chapter_raw_ids:
                            result = _READ_CHAPTER_RE.sub('', result).strip()
                            _ch_order_chat = meta_chat.get('chapter_order', []) if isinstance(meta_chat, dict) else []
                            _chapter_contents = []
                            _missing_ids = []
                            for _raw in _read_chapter_raw_ids[:3]:
                                _rcid = _resolve_chapter_id(_raw, _ch_order_chat)
                                if not _rcid:
                                    _missing_ids.append(_raw)
                                    continue
                                _rcp = os.path.join(bd_chat, 'chapters', f'{_rcid}.json')
                                if not os.path.exists(_rcp):
                                    _missing_ids.append(_raw)
                                    continue
                                _rcd = load_json(_rcp, dict)
                                _rc_title = _rcd.get('title', '未命名')
                                _rc_content = _rcd.get('content', '')
                                if _rc_content:
                                    _sub_status_text = f'[子代理] 正在客观阅读「{_rc_title}」…'
                                    if not result:
                                        bg_task_update(task_id, result=_sub_status_text, progress=60)
                                    else:
                                        bg_task_update(task_id, result=result + '\n\n' + _sub_status_text, progress=60)
                                    tool_calls.append({'type': 'read_subagent', 'label': _sub_status_text, 'status': 'running'})
                                    _sub_result = _read_chapter_subagent(cfg_settings, _rc_title, _rc_content, tp)
                                    if _sub_result:
                                        _chapter_contents.append(f'【{_rc_title}】子代理客观摘要\n{_sub_result}')
                                    else:
                                        # 子代理失败，回退到原文
                                        _chapter_contents.append(f'【{_rc_title}】\n{_rc_content[:8000]}')
                                    tool_calls[-1] = {'type': 'read_subagent', 'label': f'已读取「{_rc_title}」', 'status': 'done'}
                                    bg_task_update(task_id, result=result or '', progress=65)
                                else:
                                    _missing_ids.append(_raw)

                            # 即使没找到也续聊，让 AI 修正或道歉，不能静默停止
                            _injection_parts = []
                            if _chapter_contents:
                                _injection_parts.append('[系统注入：子代理已客观阅读以下章节，以下是摘要]\n\n' + '\n\n'.join(_chapter_contents))
                            if _missing_ids:
                                _avail = '\n'.join(f'  - id={_o} 第{_i+1}章' for _i, _o in enumerate(_ch_order_chat[:50]))
                                _injection_parts.append(
                                    f'[系统提示：以下 chapter_id 未找到对应章节，请勿再次尝试同样的 ID]\n'
                                    f'  未找到：{", ".join(_missing_ids)}\n'
                                    f'  可用章节 ID 列表（必须用 id= 后面的字符串作为 chapter_id，不要用章节号数字）：\n{_avail}\n'
                                    f'请直接基于你已有的信息回答用户，不要再调用 READ_CHAPTER 重试同一个 ID。严禁输出 markdown 表格。'
                                )
                            _injection = '\n\n'.join(_injection_parts) + '\n\n请直接回答，你已拥有正文内容，无需再次调用 READ_CHAPTER。'

                            # 修改系统提示词：告知 AI 已读取到正文，避免再次输出 READ_CHAPTER
                            if msgs and msgs[0].get('role') == 'system':
                                _sys = msgs[0]['content']
                                if '你无法直接看到章节正文' in _sys:
                                    msgs[0] = dict(msgs[0])
                                    msgs[0]['content'] = _sys.replace(
                                        '你无法直接看到章节正文。如果需要查看正文，请使用[READ_CHAPTER]工具读取。不要让用户复制粘贴或上传稿子——你自己调用工具即可。',
                                        '你已通过[READ_CHAPTER]读取了章节正文，内容已在下方提供。请直接回答。'
                                    ).replace(
                                        '你无法直接看到章节正文。如果需要查看正文，请使用[READ_CHAPTER]工具读取。',
                                        '你已通过[READ_CHAPTER]读取了章节正文，内容已在下方提供。请直接回答。'
                                    )

                            msgs.append({'role': 'assistant', 'content': result or '让我看看正文。'})
                            msgs.append({'role': 'user', 'content': _injection})
                            _rc_content_acc = []
                            _rc_reasoning_acc = []
                            def _rc_on_content(tk):
                                _rc_content_acc.append(tk)
                                bg_task_update(task_id, result=(result + '\n\n' if result else '') + ''.join(_rc_content_acc), progress=min(95, 60 + len(''.join(_rc_content_acc)) // 10))
                            def _rc_on_reasoning(tk):
                                _rc_reasoning_acc.append(tk)
                                bg_task_update(task_id, reasoning=''.join(_rc_reasoning_acc))
                            _rc_full, _rc_err = call_ai_stream(cfg_settings, msgs, mt, tp, timeout=120,
                                                                on_content_token=_rc_on_content,
                                                                on_reasoning_token=_rc_on_reasoning,
                                                                should_stop_fn=lambda: bg_task_should_stop(task_id))
                            if _rc_err:
                                if not result:
                                    result = '[读取章节后生成回复失败]'
                            else:
                                _rc_result = _clean_ai_text(_rc_full or '')
                                # 防止续聊又输出 READ_CHAPTER（递归防御）
                                _rc_result = _READ_CHAPTER_RE.sub('', _rc_result).strip()
                                result = (result + '\n\n' if result else '') + _rc_result
                                reasoning_text = ''.join(_rc_reasoning_acc) or reasoning_text
                            if not result.strip():
                                # 兜底：续聊也没出内容，给一句明确提示，避免空白
                                if _missing_ids and not _chapter_contents:
                                    result = '抱歉，未能定位到你提到的章节，请直接告诉我章节标题或问题本身。'
                                else:
                                    result = '（未生成内容）'

                        # — 检测浏览请求（优先 tool_call 格式，其次 [BROWSE] 标签）
                        _browse_query = None
                        _browse_link = None
                        if HAS_BROWSER_AGENT and _network_search_mode != 'off':
                            # tool_call 格式：browse {query/} 或 browse {link/}
                            tc = re.search(r'\[TOOL_CALL\]\s*(.*?)\s*\[/TOOL_CALL\]', result, re.S)
                            if tc:
                                tc_text = tc.group(1)
                                qm = re.search(r'(?:--query|--prompt)\s*"?([^\n"]{2,200})"?', tc_text)
                                lm = re.search(r'(?:--link|--url)\s*"?`?\s*(https?://[^\s`"]+)', tc_text)
                                if qm:
                                    _browse_query = qm.group(1).strip().strip('`')
                                elif lm:
                                    _browse_link = lm.group(1).strip().strip('`')
                                result = re.sub(r'\s*\[TOOL_CALL\].*?\[/TOOL_CALL\]\s*', '', result, flags=re.S).strip()
                            # [BROWSE] 标签（兼容旧格式）
                            if not _browse_query and not _browse_link:
                                m = re.search(r'\[BROWSE\](.*?)\[/BROWSE\]', result, re.S)
                                if m:
                                    _browse_query = m.group(1).strip()
                                    result = re.sub(r'\s*\[BROWSE\].*?\[/BROWSE\]\s*', '', result, flags=re.S).strip()
                            if _browse_query or _browse_link:
                                if not result:
                                    result = '好的，让我打开浏览器查一下。'

                        needs_rt = False
                        # 优先检测 AI 主动调用的 [SUGGEST_READTHROUGH] 工具
                        if re.search(r'\[SUGGEST_READTHROUGH\]', result):
                            needs_rt = True
                        # 兜底：AI 未使用工具但回复中提及需要通读（且 source.md 缺失或极短）
                        if not needs_rt and (not source_ctx or len(source_ctx) <= 100):
                            indicators = ['还没读过', '还没看过', '尚未通读', '没有读过', '不了解全书', '不清楚全书', '需要通读', '我还没看过这本书', '尚未阅读', '没有阅读']
                            if any(ind in result for ind in indicators):
                                needs_rt = True
                        if needs_rt:
                            tool_calls.append({'type': 'suggest_readthrough', 'label': '建议通读', 'status': 'ready'})

                        annotation_changes = False
                        annotation_add_count = 0
                        annotation_remove_count = 0
                        ann_path = os.path.join(get_book_dir(book_id), 'annotations.json')

                        for m in re.finditer(r'\[ANNOTATE_ADD\](.*?)\[/ANNOTATE_ADD\]', result, re.S):
                            try:
                                cmd = json.loads(m.group(1).strip())
                                cid = cmd.get('chapter_id', '')
                                if not is_valid_id(cid):
                                    continue  # AI 工具调用，恶意 prompt 注入可塞穿越路径，直接跳过
                                text_snippet = cmd.get('text', '')
                                note = cmd.get('note', '')
                                color = cmd.get('color', 'yellow')
                                cp = os.path.join(get_book_dir(book_id), 'chapters', f"{cid}.json")
                                if os.path.exists(cp) and text_snippet:
                                    with open(cp, 'r', encoding='utf-8') as f:
                                        ch_data = json.load(f)
                                    content = ch_data.get('content', '')
                                    idx = content.find(text_snippet)
                                    if idx >= 0:
                                        anns_data = load_json(ann_path, dict)
                                        anns = anns_data.get('annotations', [])
                                        ann_id = 'ann_' + str(int(time.time() * 1000)) + '_' + str(len(anns))
                                        anns.append({
                                            'id': ann_id, 'chapter_id': cid,
                                            'start': idx, 'end': idx + len(text_snippet),
                                            'text': text_snippet, 'note': note, 'color': color,
                                            'created': time.time()
                                        })
                                        save_json(ann_path, {'annotations': anns})
                                        annotation_changes = True
                                        annotation_add_count += 1
                            except Exception as e:
                                log_action('ANNOTATE_ADD_ERROR', str(e)[:200])

                        for m in re.finditer(r'\[ANNOTATE_REMOVE\](.*?)\[/ANNOTATE_REMOVE\]', result, re.S):
                            try:
                                cmd = json.loads(m.group(1).strip())
                                text_snippet = cmd.get('text', '')
                                ann_id = cmd.get('id', '')
                                anns_data = load_json(ann_path, dict)
                                anns = anns_data.get('annotations', [])
                                new_anns = []
                                for a in anns:
                                    if ann_id and a.get('id') == ann_id:
                                        continue
                                    if text_snippet and a.get('text') == text_snippet:
                                        continue
                                    new_anns.append(a)
                                if len(new_anns) != len(anns):
                                    save_json(ann_path, {'annotations': new_anns})
                                    annotation_changes = True
                                    annotation_remove_count += len(anns) - len(new_anns)
                            except Exception as e:
                                log_action('ANNOTATE_REMOVE_ERROR', str(e)[:200])
                        if annotation_add_count:
                            tool_calls.append({'type': 'annotate_add', 'label': f'标注正文 x{annotation_add_count}', 'status': 'done'})
                        if annotation_remove_count:
                            tool_calls.append({'type': 'annotate_remove', 'label': f'删除标注 x{annotation_remove_count}', 'status': 'done'})

                        # 解析 COMPLETE_CHAPTER 隐藏工具调用
                        complete_chapter_triggered = False
                        for m in re.finditer(r'\[COMPLETE_CHAPTER\](.*?)\[/COMPLETE_CHAPTER\]', result, re.S):
                            try:
                                cmd = json.loads(m.group(1).strip())
                                ccid = cmd.get('chapter_id', '')
                                if ccid and is_valid_id(ccid):
                                    cp = os.path.join(get_book_dir(book_id), 'chapters', f"{ccid}.json")
                                    if os.path.exists(cp):
                                        settings_cc = get_settings()
                                        if settings_cc.get('base_url') and settings_cc.get('model'):
                                            existing_cc = bg_task_get_by_book_type(book_id, 'chapter-complete')
                                            if not (existing_cc and existing_cc.get('status') == 'running'):
                                                tid_cc = bg_task_start('chapter-complete', book_id, f'本章通读')
                                                threading.Thread(target=_do_chapter_complete_wrapper, args=(tid_cc, book_id, ccid, settings_cc), daemon=True).start()
                                                complete_chapter_triggered = True
                                                tool_calls.append({'type': 'complete_chapter', 'label': '本章通读', 'status': 'running'})
                            except Exception as e:
                                log_action('COMPLETE_CHAPTER_ERROR', str(e)[:200])

                        inspirations_changed = False
                        inspiration_add_count = 0
                        for m_insp in re.finditer(r'\[ADD_INSPIRATION\](.*?)\[/ADD_INSPIRATION\]', result, re.S):
                            try:
                                cmd = json.loads(m_insp.group(1).strip())
                                text_insp = cmd.get('text') or cmd.get('content') or ''
                                if add_inspiration_item(book_id, text_insp, source='luca'):
                                    inspirations_changed = True
                                    inspiration_add_count += 1
                            except Exception as e:
                                log_action('ADD_INSPIRATION_ERROR', str(e)[:200])
                        if inspiration_add_count:
                            tool_calls.append({'type': 'inspiration_add', 'label': f'灵感备忘 x{inspiration_add_count}', 'status': 'done'})

                        kb_reread_started = False
                        for m_rr in re.finditer(r'\[REREAD_KB\](.*?)\[/REREAD_KB\]', result, re.S):
                            try:
                                cmd = json.loads(m_rr.group(1).strip())
                                chapter_ids = cmd.get('chapter_ids') or []
                                if isinstance(chapter_ids, str):
                                    chapter_ids = [chapter_ids]
                                single_cid = cmd.get('chapter_id') or ''
                                if single_cid:
                                    chapter_ids.append(single_cid)
                                if not chapter_ids and cid_chat:
                                    chapter_ids = [cid_chat]
                                chapter_ids = [c for c in chapter_ids if is_valid_id(c)]
                                focus_texts = cmd.get('focus_texts') or []
                                if isinstance(focus_texts, str):
                                    focus_texts = [focus_texts]
                                if cmd.get('focus_text'):
                                    focus_texts.append(cmd.get('focus_text'))
                                correction = cmd.get('correction') or user_text
                                if chapter_ids and correction:
                                    settings_rr = get_settings()
                                    if settings_rr.get('base_url') and settings_rr.get('model'):
                                        existing_rr = bg_task_get_by_book_type(book_id, 'kb-reread')
                                        if not (existing_rr and existing_rr.get('status') == 'running'):
                                            tid_rr = bg_task_start('kb-reread', book_id, '局部重读')
                                            threading.Thread(
                                                target=_do_kb_reread_task,
                                                args=(tid_rr, book_id, chapter_ids, correction, focus_texts, settings_rr),
                                                daemon=True,
                                            ).start()
                                            kb_reread_started = True
                                            tool_calls.append({'type': 'kb_reread', 'label': '局部重读', 'status': 'running'})
                            except Exception as e:
                                log_action('REREAD_KB_ERROR', str(e)[:200])

                        kb_citations = []
                        for m_cite in re.finditer(r'\[CITE\](.*?)\[/CITE\]', result, re.S):
                            try:
                                cmd = json.loads(m_cite.group(1).strip())
                                tn = cmd.get('table_name', '')
                                rid = cmd.get('record_id', '')
                                field = cmd.get('field', '')
                                brief = cmd.get('brief', '')
                                if tn and rid:
                                    kb_storage.init_db(book_id)
                                    rec = kb_storage.get_kb_record(book_id, tn, rid)
                                    chapter_id = ''
                                    chapter_title = ''
                                    snippet = ''
                                    if rec:
                                        chapter_id = rec.get('chapter_id', '') or ''
                                        chapter_title = rec.get('chapter_title', '') or ''
                                        snippet = rec.get('snippet', '') or rec.get('fact', '') or ''
                                        if not chapter_id:
                                            chapter_id = rec.get('first_chapter_id', '') or rec.get('hint_chapter_id', '') or ''
                                    kb_citations.append({
                                        'table_name': tn, 'record_id': rid,
                                        'field': field, 'brief': brief,
                                        'chapter_id': chapter_id, 'chapter_title': chapter_title,
                                        'snippet': snippet[:200] if snippet else '',
                                    })
                            except Exception as e:
                                log_action('CITE_PARSE_ERROR', str(e)[:200])
                        if kb_citations:
                            tool_calls.append({'type': 'kb_cite', 'label': f'引用知识库 x{len(kb_citations)}', 'status': 'done'})

                        kb_proposals = []
                        for m_prop in re.finditer(r'\[PROPOSE_KB_EDIT\](.*?)\[/PROPOSE_KB_EDIT\]', result, re.S):
                            try:
                                cmd = json.loads(m_prop.group(1).strip())
                                tn = cmd.get('table_name', '')
                                rid = cmd.get('record_id', '')
                                field = cmd.get('field', '')
                                new_val = cmd.get('new_value', '')
                                reason = cmd.get('reason', '')
                                if tn and rid and field:
                                    kb_storage.init_db(book_id)
                                    pid = kb_storage.create_proposal(book_id, tn, rid, field, new_val, reason=reason, source_message=user_text[:200])
                                    old_val = ''
                                    rec = kb_storage.get_kb_record(book_id, tn, rid)
                                    if rec and field in rec:
                                        old_val = str(rec[field] or '')
                                    kb_proposals.append({
                                        'proposal_id': pid, 'table_name': tn,
                                        'record_id': rid, 'field': field,
                                        'old_value': old_val, 'new_value': new_val,
                                        'reason': reason,
                                    })
                            except Exception as e:
                                log_action('PROPOSE_KB_EDIT_PARSE_ERROR', str(e)[:200])
                        if kb_proposals:
                            tool_calls.append({'type': 'kb_proposal', 'label': f'提议修改 x{len(kb_proposals)}', 'status': 'waiting'})

                        kb_reread_fallback = False
                        if (not kb_reread_started) and (not kb_proposals) and _looks_like_kb_correction(user_text):
                            try:
                                chapter_ids = [cid_chat] if cid_chat else []
                                settings_rr = get_settings()
                                if chapter_ids and settings_rr.get('base_url') and settings_rr.get('model'):
                                    existing_rr = bg_task_get_by_book_type(book_id, 'kb-reread')
                                    if not (existing_rr and existing_rr.get('status') == 'running'):
                                        tid_rr = bg_task_start('kb-reread', book_id, '局部重读')
                                        threading.Thread(
                                            target=_do_kb_reread_task,
                                            args=(tid_rr, book_id, chapter_ids, user_text, [], settings_rr),
                                            daemon=True,
                                        ).start()
                                        kb_reread_started = True
                                        kb_reread_fallback = True
                                        tool_calls.append({'type': 'kb_reread', 'label': '局部重读（自动兜底）', 'status': 'running'})
                            except Exception as e:
                                log_action('REREAD_KB_FALLBACK_ERROR', str(e)[:200])

                        result = re.sub(r'\[ANNOTATE_ADD\].*?\[/ANNOTATE_ADD\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[ANNOTATE_REMOVE\].*?\[/ANNOTATE_REMOVE\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[COMPLETE_CHAPTER\].*?\[/COMPLETE_CHAPTER\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[ADD_INSPIRATION\].*?\[/ADD_INSPIRATION\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[REREAD_KB\].*?\[/REREAD_KB\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[SUGGEST_READTHROUGH\].*?\[/SUGGEST_READTHROUGH\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[CITE\].*?\[/CITE\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[PROPOSE_KB_EDIT\].*?\[/PROPOSE_KB_EDIT\]', '', result, flags=re.S).strip()

                        # [THEME] 主题生成（仅两色：bg + accent，surface 由 bg 自动派生）
                        theme_data = None
                        theme_data_list = []
                        for m_theme in re.finditer(r'\[THEME\]\s*(.*?)\s*\[/THEME\]', result, re.S):
                            try:
                                raw = m_theme.group(1).strip()
                                parts = raw.split('|', 1)
                                name = parts[1].strip() if len(parts) > 1 else '自定义主题'
                                colors = {}
                                for pair in parts[0].split(','):
                                    pair = pair.strip()
                                    if ':' in pair:
                                        k, v = pair.split(':', 1)
                                        k = k.strip(); v = v.strip()
                                        if k in ('bg', 'accent') and re.match(r'^#[0-9A-Fa-f]{6}$', v):
                                            colors[k] = v.upper()
                                if len(colors) >= 2:
                                    td = {'name': name, 'colors': colors}
                                    theme_data_list.append(td)
                                    if tool_calls is None: tool_calls = []
                                    tool_calls.append({'type': 'theme_gen', 'label': f'生成主题: {name}', 'status': 'done'})
                            except:
                                pass
                        if theme_data_list:
                            theme_data = theme_data_list[0]  # 向后兼容
                        result = re.sub(r'\[THEME\]\s*.*?\s*\[/THEME\]', '', result, flags=re.S).strip()
                        # 清理已废弃的工具标记（后端不再执行这些工具，但 AI 可能仍输出）
                        result = re.sub(r'\[FETCH_URL\].*?\[/FETCH_URL\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[SEARCH\].*?\[/SEARCH\]', '', result, flags=re.S).strip()

                        # 模型自重复检测：如果结果的前半段和后半段高度相似，截掉后半段
                        if len(result) > 20:
                            half = len(result) // 2
                            first_half = re.sub(r'\s+', '', result[:half])
                            second_half = re.sub(r'\s+', '', result[half:])
                            if first_half and second_half and first_half == second_half:
                                result = result[:half].strip()
                            elif len(result) > 40:
                                # 模糊匹配：前半段是否出现在后半段开头
                                q = len(result) // 4
                                a = re.sub(r'\s+', '', result[:q])
                                b = re.sub(r'\s+', '', result[q:q*2])
                                if a and b and a == b:
                                    result = result[:q*2].strip()

                        reason = ''.join(reasoning_acc)
                        # 最终兜底去重：如果推理与结果内容一致，清空推理
                        if reason and result:
                            r_norm2 = re.sub(r'\s+', ' ', reason).strip()
                            res_norm = re.sub(r'\s+', ' ', result).strip()
                            if r_norm2 == res_norm or r_norm2 in res_norm or res_norm in r_norm2:
                                reason = ''
                                reasoning_acc.clear()
                        if _browse_query or _browse_link:
                            if _network_search_mode == 'auto':
                                result = result + '\n\n🌐 正在操作浏览器…'
                                bg_task_update(task_id, result=result, reasoning=reason, progress=50)
                                threading.Thread(target=_do_browser_search_launch, args=(task_id, book_id, _browse_query or '', cfg_settings, _browse_link or None), daemon=True).start()
                            else:
                                # 'on' 模式：等待用户确认
                                result = result + '\n\n🔍 Luca 想搜索：' + (_browse_query or _browse_link or '')
                                bg_task_update(task_id, result=result, reasoning=reason, progress=50, pending_browse={
                                    'query': _browse_query or '',
                                    'link': _browse_link or '',
                                })
                        else:
                            if kb_reread_started and not result:
                                result = '我会重读这段。'
                            elif kb_reread_fallback and result and '重读' not in result:
                                result = result.rstrip() + '\n\n我会重读这段。'
                            meta = {
                                'needs_summary': needs_rt,
                                'annotations_changed': annotation_changes,
                                'complete_chapter': complete_chapter_triggered,
                                'inspirations_changed': inspirations_changed,
                                'kb_reread_started': kb_reread_started,
                                'kb_citations': kb_citations,
                                'kb_proposals': kb_proposals,
                                'tool_calls': tool_calls,
                                'theme_data': theme_data,
                                'theme_data_list': theme_data_list,
                            }
                            _replace_pending_chat_msg(chat_sid, task_id, result, reason, meta=meta)
                            bg_task_update(task_id, result=result, reasoning=reason, progress=100, needs_readthrough=needs_rt, needs_summary=needs_rt, annotations_changed=annotation_changes, complete_chapter=complete_chapter_triggered, inspirations_changed=inspirations_changed, kb_reread_started=kb_reread_started, kb_citations=kb_citations, kb_proposals=kb_proposals, tool_calls=tool_calls, theme_data=theme_data, theme_data_list=theme_data_list)
                            bg_task_done(task_id)
                            _schedule_idle_compress(chat_sid)
                    except Exception as e:
                        err_str = str(e)
                        if bg_task_should_stop(task_id):
                            _replace_pending_chat_msg(chat_sid, task_id, '[已停止]')
                            bg_task_done(task_id, '已停止')
                        else:
                            _replace_pending_chat_msg(chat_sid, task_id, '[错误: ' + err_str + ']')
                            bg_task_done(task_id, err_str)
                threading.Thread(target=do_chat_task, args=(tid, bid, _chat_sid, text, settings, data.get('history', [])), daemon=True).start()
                self.json_resp(200, {'status': 'started', 'task_id': tid}); return

            if action == 'annotations':
                sub = data.get('action', '')
                ann_path = os.path.join(bd, 'annotations.json')
                anns_data = load_json(ann_path, dict)
                anns = anns_data.get('annotations', [])
                if sub == 'get':
                    self.json_resp(200, {'annotations': anns}); return
                elif sub == 'add':
                    cid = data.get('chapter_id', '')
                    if not is_valid_id(cid):
                        self.json_resp(400, {'error': 'Invalid chapter_id'}); return
                    text_snippet = data.get('text', '')
                    note = data.get('note', '')
                    color = data.get('color', 'yellow')
                    start_pos = data.get('start', -1)
                    end_pos = data.get('end', -1)
                    cp = os.path.join(ch_dir, f"{cid}.json")
                    if not os.path.exists(cp):
                        self.json_resp(404, {'error': '章节不存在'}); return
                    with open(cp, 'r', encoding='utf-8') as f:
                        ch_data = json.load(f)
                    content = ch_data.get('content', '')
                    if start_pos >= 0 and end_pos > start_pos:
                        if content[start_pos:end_pos] == text_snippet:
                            idx = start_pos
                        else:
                            idx = content.find(text_snippet)
                    else:
                        idx = content.find(text_snippet)
                    if idx < 0:
                        self.json_resp(400, {'error': '未找到指定文本'}); return
                    ann_id = 'ann_' + str(int(time.time() * 1000)) + '_' + str(len(anns))
                    anns.append({
                        'id': ann_id, 'chapter_id': cid,
                        'start': idx, 'end': idx + len(text_snippet),
                        'text': text_snippet, 'note': note, 'color': color,
                        'created': time.time()
                    })
                    save_json(ann_path, {'annotations': anns})
                    self.json_resp(200, {'annotation': anns[-1]}); return
                elif sub == 'remove':
                    ann_id = data.get('id', '')
                    text_snippet = data.get('text', '')
                    new_anns = []
                    for a in anns:
                        if ann_id and a.get('id') == ann_id:
                            continue
                        if text_snippet and a.get('text') == text_snippet:
                            continue
                        new_anns.append(a)
                    if len(new_anns) != len(anns):
                        save_json(ann_path, {'annotations': new_anns})
                    self.json_resp(200, {'removed': len(anns) - len(new_anns)}); return
                elif sub == 'clear':
                    cid = data.get('chapter_id', '')
                    if cid:
                        new_anns = [a for a in anns if a.get('chapter_id') != cid]
                    else:
                        new_anns = []
                    if len(new_anns) != len(anns):
                        save_json(ann_path, {'annotations': new_anns})
                    self.json_resp(200, {'removed': len(anns) - len(new_anns)}); return
                self.json_resp(400, {'error': '未知操作'}); return

            if action == 'kb-proposal-list':
                try:
                    kb_storage.init_db(bid)
                    rows = kb_storage.list_proposals(bid, status=data.get('status', 'pending'), limit=int(data.get('limit', 50)))
                except Exception as e:
                    self.json_resp(500, {'error': str(e)}); return
                self.json_resp(200, {'proposals': rows}); return

            if action == 'kb-proposal-confirm':
                pid = data.get('proposal_id', '')
                if not pid: self.json_resp(400, {'error': 'proposal_id 必填'}); return
                try:
                    kb_storage.init_db(bid)
                    p = kb_storage.confirm_proposal(bid, pid)
                    _schedule_timeline_after_kb_edit(bid, p.get('table_name'), p.get('field'))
                except ValueError as e:
                    self.json_resp(400, {'error': str(e)}); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)}); return
                self.json_resp(200, {'ok': True, 'proposal': p}); return

            if action == 'kb-proposal-reject':
                pid = data.get('proposal_id', '')
                if not pid: self.json_resp(400, {'error': 'proposal_id 必填'}); return
                try:
                    kb_storage.init_db(bid)
                    kb_storage.reject_proposal(bid, pid)
                except Exception as e:
                    self.json_resp(500, {'error': str(e)}); return
                self.json_resp(200, {'ok': True}); return

            if action == 'kb-edit-apply':
                table_name = data.get('table_name', '')
                record_id = data.get('record_id', '')
                field = data.get('field', '')
                new_value = data.get('new_value', '')
                if not (table_name and record_id and field):
                    self.json_resp(400, {'error': 'table_name/record_id/field 必填'}); return
                try:
                    kb_storage.init_db(bid)
                    result = kb_storage.apply_kb_edit(bid, table_name, record_id, field, new_value,
                        reason=data.get('reason', ''), source=data.get('source', 'user'))
                    _schedule_timeline_after_kb_edit(bid, table_name, field)
                except ValueError as e:
                    self.json_resp(400, {'error': str(e)}); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)}); return
                self.json_resp(200, {'ok': True, 'log_id': result['log_id'], 'old_value': result['old_value']}); return

            if action == 'kb-edit-undo':
                log_id = data.get('log_id', '')
                if not log_id: self.json_resp(400, {'error': 'log_id 必填'}); return
                try:
                    kb_storage.init_db(bid)
                    undone = kb_storage.undo_edit(bid, int(log_id))
                    _schedule_timeline_after_kb_edit(bid, undone.get('table_name'), undone.get('field'))
                except ValueError as e:
                    self.json_resp(400, {'error': str(e)}); return
                except Exception as e:
                    self.json_resp(500, {'error': str(e)}); return
                self.json_resp(200, {'ok': True}); return

            if action == 'outline-update':
                content = data.get('content', '')
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                outline = get_outline(bid)
                if not content:
                    all_content = ''
                    if os.path.isdir(ch_dir):
                        for fn in sorted(os.listdir(ch_dir)):
                            if fn.endswith('.json'):
                                try:
                                    with open(os.path.join(ch_dir, fn), 'r', encoding='utf-8') as f:
                                        ch = json.load(f)
                                        all_content += ch.get('content', '')[:1500]
                                except: continue
                    content = all_content[-3000:]
                if not content: self.json_resp(200, outline); return
                existing = json.dumps({'worldview': outline.get('worldview', ''), 'characters': outline.get('characters', []),
                                       'timeline': outline.get('timeline', []),
                                       'key_events': outline.get('key_events', []),
                                       'rules': outline.get('rules', [])}, ensure_ascii=False)
                prompt = f"""根据作者写的内容，生成故事大纲建议。

当前人工大纲：
{existing}

故事内容：
{content[-3000:]}

输出大纲建议JSON，不加其他文字：
{{"worldview":"世界观建议","characters":["人物1：简述"],"timeline":["事件1"],"key_events":["关键事件1"],"rules":["规则1"]}}"""
                result, err = call_ai(settings, [{'role': 'system', 'content': '只输出JSON。你是写作助手，根据内容提供大纲建议，但作者才是故事的主人。'}, {'role': 'user', 'content': prompt}], 800, 0.3)
                if err: self.json_resp(502, {'error': err}); return
                try:
                    result = re.sub(r'```json\s*', '', result)
                    result = re.sub(r'```\s*', '', result)
                    no = json.loads(result.strip())
                    ai_sug = outline.get('ai_suggestions', {})
                    for k in ['worldview', 'characters', 'timeline', 'key_events', 'rules']:
                        if k in no: ai_sug[k] = no[k]
                    ai_sug['updated'] = time.time()
                    outline['ai_suggestions'] = ai_sug
                    outline['updated'] = time.time()
                    save_json(os.path.join(bd, 'outline.json'), outline)
                except: pass
                self.json_resp(200, outline); return

            if action == 'outline-check':
                content = data.get('content', '')
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                outline = get_outline(bid)
                if not outline.get('worldview') and not outline.get('characters'):
                    self.json_resp(200, {'contradictions': []}); return
                if not content:
                    all_content = ''
                    if os.path.isdir(ch_dir):
                        for fn in sorted(os.listdir(ch_dir)):
                            if fn.endswith('.json'):
                                try:
                                    with open(os.path.join(ch_dir, fn), 'r', encoding='utf-8') as f:
                                        ch = json.load(f)
                                        all_content += ch.get('content', '')[:1500]
                                except: continue
                    content = all_content[-3000:]
                if not content:
                    self.json_resp(200, {'contradictions': []}); return
                outline_str = json.dumps({'worldview': outline.get('worldview', ''), 'characters': outline.get('characters', []),
                                          'rules': outline.get('rules', []), 'key_events': outline.get('key_events', [])}, ensure_ascii=False)
                prompt = f"""检查新内容是否与已有大纲矛盾。

已有大纲：
{outline_str}

新内容：
{content[-2000:]}

无矛盾输出：无矛盾
有矛盾列出每条：1. 矛盾简述"""
                result, err = call_ai(settings, [{'role': 'system', 'content': '只输出矛盾列表或"无矛盾"。'}, {'role': 'user', 'content': prompt}], 300, 0.2)
                if err: self.json_resp(502, {'error': err}); return
                contradictions = []
                if result and '无矛盾' not in result:
                    for l in result.strip().split('\n'):
                        l = re.sub(r'^\d+[\.\、\)\]]\s*', '', l.strip())
                        if l: contradictions.append(l)
                self.json_resp(200, {'contradictions': contradictions}); return

            if action == 'outline-save':
                outline = get_outline(bid)
                for k in ['worldview', 'characters', 'timeline', 'key_events', 'rules', 'chapter_summaries', 'timeline_nodes', 'ai_suggestions']:
                    if k in data: outline[k] = data[k]
                outline['updated'] = time.time()
                save_json(os.path.join(bd, 'outline.json'), outline)
                if 'memory' in data: save_core_memory(bid, data['memory'])
                o = dict(outline)
                o['memory'] = get_core_memory(bid)
                self.json_resp(200, o); return

            if action == 'memory-update':
                content = data.get('content', '')
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                existing_memory = get_core_memory(bid)
                all_content = ''
                if os.path.isdir(ch_dir):
                    for fn in sorted(os.listdir(ch_dir)):
                        if fn.endswith('.json'):
                            try:
                                with open(os.path.join(ch_dir, fn), 'r', encoding='utf-8') as f:
                                    ch = json.load(f)
                                    all_content += ch.get('content', '')[:1500]
                            except: continue
                all_content = all_content[-4000:]
                if not all_content and not content:
                    self.json_resp(200, {'memory': existing_memory}); return
                prompt = f"""你是一本小说的AI记忆管理系统。根据作者最新的写作内容，更新这本小说的全局记忆大纲。

当前记忆：
{existing_memory or '（尚无记忆）'}

最新写作内容：
{content or all_content}

请用markdown格式输出更新后的记忆，包含以下部分（如果没有相关信息可写"待定"）：

## 故事梗概
## 主要角色（含当前状态）
## 世界观设定
## 时间线
## 关键事件
## 伏笔与线索
## 人物关系

规则：
- 只输出markdown，不加其他文字
- 保留已有信息，增量更新
- 如新内容和已有记忆矛盾，以新内容为准"""
                result, err = call_ai(settings, [{'role': 'system', 'content': '你是小说记忆管理系统。只输出markdown格式记忆，不加其他文字。'}, {'role': 'user', 'content': prompt}], 2000, 0.3)
                if err:
                    log_action('MEMORY_ERROR', f'{bid}: {err}')
                    self.json_resp(502, {'error': err}); return
                save_core_memory(bid, result)
                self.json_resp(200, {'memory': result}); return

            if action == 'chapter-summary' and data.get('id'):
                cid = data['id']
                if not is_valid_id(cid): self.json_resp(400, {'error': 'Invalid ID'}); return
                cp = os.path.join(ch_dir, f"{cid}.json")
                if not os.path.exists(cp): self.json_resp(404, {'error': '章节不存在'}); return
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                try:
                    with open(cp, 'r', encoding='utf-8') as f: ch = json.load(f)
                    content = ch.get('content', '')
                except: self.json_resp(500, {'error': '读取失败'}); return
                if not content: self.json_resp(200, {'summary': ''}); return
                pyramid = build_pyramid_context(bid)
                prompt = f"""你是小说章节摘要助手。请阅读以下章节内容，提取结构化摘要，限制在500字以内。

{pyramid}

章节内容：
{content}

请输出以下格式的摘要：
- 出场角色及关键行为
- 本章核心事件
- 角色状态变化
- 新伏笔或线索
- 重要对话或 revelation

只输出摘要内容，不加其他文字。"""
                result, err = call_ai(settings, [{'role': 'system', 'content': '你是小说章节摘要助手。只输出纯文本摘要，不加格式标记。'}, {'role': 'user', 'content': prompt}], 600, 0.3)
                if err: self.json_resp(502, {'error': err}); return
                save_chapter_summary(bid, cid, result)
                self.json_resp(200, {'summary': result}); return

            if action == 'chapter-complete' and data.get('id'):
                cid = data['id']
                if not is_valid_id(cid): self.json_resp(400, {'error': 'Invalid ID'}); return
                cp = os.path.join(ch_dir, f"{cid}.json")
                if not os.path.exists(cp): self.json_resp(404, {'error': '章节不存在'}); return
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                # 检查是否已有进行中的本章通读任务
                existing = bg_task_get_by_book_type(bid, 'chapter-complete')
                if existing and existing.get('status') == 'running':
                    self.json_resp(400, {'error': '已有本章通读任务在进行中'}); return
                tid = bg_task_start('chapter-complete', bid, f'本章通读')
                text = data.get('text', None)
                threading.Thread(target=_do_chapter_complete_wrapper, args=(tid, bid, cid, settings, text), daemon=True).start()
                self.json_resp(200, {'status': 'started', 'task_id': tid}); return

            if action == 'reader-prediction':
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                source_text = get_smart_context(bid, settings=settings)
                if not source_text or source_text.startswith('（目前还没有'):
                    self.json_resp(400, {'error': 'source.md 为空，请先通读'}); return
                existing = bg_task_get_by_book_type(bid, 'prediction')
                if existing and existing.get('status') == 'running':
                    self.json_resp(400, {'error': '已有预言任务在进行中'}); return
                tid = bg_task_start('prediction', bid, '生成预言')
                def do_prediction_task(task_id, book_id, cfg_settings):
                    try:
                        bg_task_update(task_id, progress=5)
                        prompt = f"""你是一位正在追读这部小说的资深读者。基于作者已公布的阅读笔记（你没有作者视角，不知道后续），分析剧情并推测未来走向。

【你已读到的全书笔记】
{get_source(book_id)}

【分析要求】
1. 伏笔梳理：列出所有已埋下但尚未回收的伏笔，标注每个伏笔的当前状态和可能的发展方向
2. 角色推演：基于已有资料，分析主要角色的动机弧光，推测他们接下来最可能做出的选择
3. 剧情预测：给出2-3种最合理的未来剧情走向，每种都要说明推理依据
4. 危机预判：故事目前积累的张力会在哪里爆发？最可能的冲突触发点是什么？
5. 阅读期待：作为读者，你最想看什么展开？为什么？

【输出要求】
- 用第一人称"我"的口吻，像资深读者在写长评
- 分析要有理有据，基于笔记中的具体细节，不要凭空编造
- 约1500-2500字
- 输出 markdown 格式，用 ## 分隔各个分析板块"""
                        bg_task_update(task_id, progress=30)
                        result, reasoning, err = call_ai_full(cfg_settings, [
                            {'role': 'system', 'content': '你是一位资深的网文读者，擅长分析剧情伏笔和角色动机。你基于已有的阅读笔记进行推理，不凭空编造。输出 markdown 格式。'},
                            {'role': 'user', 'content': prompt}
                        ], 2500, 0.7)
                        if err:
                            bg_task_done(task_id, err)
                            return
                        save_prediction_md(book_id, result)
                        bg_task_update(task_id, result=result, reasoning=reasoning or '', progress=100)
                        bg_task_done(task_id)
                    except Exception as e:
                        bg_task_done(task_id, str(e))
                threading.Thread(target=do_prediction_task, args=(tid, bid, settings), daemon=True).start()
                self.json_resp(200, {'status': 'started', 'task_id': tid}); return

            if action == 'import':
                filename = data.get('filename', '')
                file_b64 = data.get('data', '')
                if not filename or not file_b64:
                    self.json_resp(400, {'error': '缺少文件'}); return
                ext = os.path.splitext(filename)[1].lower()
                if ext not in IMPORT_PARSERS:
                    self.json_resp(400, {'error': f'不支持的格式: {ext}。支持: txt, md, docx, pdf, epub'}); return
                try:
                    raw = base64.b64decode(file_b64)
                except Exception as e:
                    self.json_resp(400, {'error': '文件数据无效'}); return
                log_action('IMPORT_START', f'{bid}: {filename} size={len(raw)}')
                try:
                    parser = IMPORT_PARSERS[ext]
                    result = parser(raw, filename)
                    if len(result) == 3:
                        chapters, book_title, err = result
                    else:
                        chapters, err = result
                        book_title = ''
                except Exception as e:
                    log_action('IMPORT_PARSE_ERROR', f'{bid}: {str(e)[:200]}')
                    self.json_resp(500, {'error': f'解析失败: {str(e)[:100]}'}); return
                if err:
                    log_action('IMPORT_PARSE_ERR', f'{bid}: {err}')
                    self.json_resp(400, {'error': err}); return
                if not chapters: self.json_resp(400, {'error': '未能解析出章节'}); return
                meta = get_book_meta(bid) or {}
                meta.setdefault('chapter_order', [])
                if book_title:
                    meta['title'] = book_title
                imported = 0
                for ch in chapters:
                    try:
                        cid = 'ch_' + re.sub(r'[^\w]', '_', ch.get('title', 'untitled')[:30]) + '_' + str(int(time.time() * 1000)) + str(imported)
                        if not is_valid_id(cid):
                            cid = 'ch_' + str(int(time.time() * 1000)) + str(imported)
                        ch_data = {'id': cid, 'title': ch.get('title', '未命名')[:200], 'content': ch.get('content', ''), 'updated': time.time()}
                        save_json(os.path.join(ch_dir, f"{cid}.json"), ch_data)
                        meta['chapter_order'].append(cid)
                        imported += 1
                    except Exception as e:
                        log_action('IMPORT_CH_ERR', f'{bid}: {str(e)[:100]}')
                        continue
                meta['updated'] = time.time()
                save_json(os.path.join(bd, 'meta.json'), meta)
                log_action('IMPORT', f'{bid}: {imported} chapters from {filename}')
                self.json_resp(200, {'imported': imported}); return

            if action == 'update-source':
                text = data.get('text', '')
                chapter_title = data.get('chapter_title', '未命名章节')
                if not text.strip():
                    self.json_resp(200, {'status': 'skipped', 'reason': '章节内容为空'}); return
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                existing = bg_task_get_by_book_type(bid, 'source-update')
                if existing and existing.get('status') == 'running':
                    self.json_resp(400, {'error': '已有 source 更新任务在进行中'}); return
                tid = bg_task_start('source-update', bid, '更新全书笔记')
                def do_update_source(task_id, book_id, chapter_text, ch_title, cfg_settings):
                    set_conn_meta('source-update', '更新全书笔记', book_id)
                    try:
                        mt = None
                        tp = cfg_settings.get('ai_temperature', 0.3)
                        source_text = get_source(book_id) or ''
                        if source_text and len(source_text) > 100:
                            prompt = f"""你是一位小说作家的助理，请根据当前章节内容，更新本书的阅读笔记 source.md。

现有 source.md：

{source_text}

当前章节标题：{ch_title}
当前章节内容：

{chapter_text}

任务：
1. 将本章的新信息（人物、事件、设定、伏笔、数值等）合并进 source.md
2. 不要删除已有信息，只做补充和修正
3. 保持 markdown 结构清晰
4. 输出完整的更新后的 source.md，不要加任何开场白或结束语。"""
                        else:
                            prompt = f"""你是一位小说作家的助理，请根据以下章节内容，创建一份结构化的全书详细阅读笔记 source.md。

当前章节标题：{ch_title}
当前章节内容：

{chapter_text}

请输出 markdown 格式的阅读笔记，包含：
- 人物档案
- 事件编年
- 世界观与设定
- 数值记录
- 伏笔与线索

不要加任何开场白或结束语，直接输出 markdown。"""
                        result, err = call_ai(cfg_settings, [
                            {'role': 'system', 'content': '你是资料整理员，负责维护小说全书阅读笔记。只输出完整的 markdown 内容，不要加任何开场白或结束语。'},
                            {'role': 'user', 'content': prompt}
                        ], mt, tp, timeout=180)
                        if err:
                            bg_task_done(task_id, err)
                            return
                        save_source(book_id, result or '')
                        bg_task_done(task_id)
                    except Exception as e:
                        bg_task_done(task_id, str(e))
                threading.Thread(target=do_update_source, args=(tid, bid, text, chapter_title, settings), daemon=True).start()
                self.json_resp(200, {'status': 'started', 'task_id': tid}); return

            # ---- 封面上传 ----
            if action == 'upload-cover':
                cover_b64 = data.get('cover', '')
                if not cover_b64:
                    self.json_resp(400, {'error': '缺少封面数据'}); return
                try:
                    if ',' in cover_b64:
                        cover_b64 = cover_b64.split(',', 1)[1]
                    cover_raw = base64.b64decode(cover_b64)
                except Exception:
                    self.json_resp(400, {'error': '封面数据无效'}); return
                cover_path = os.path.join(bd, 'cover')
                with open(cover_path, 'wb') as f:
                    f.write(cover_raw)
                meta = get_book_meta(bid) or {}
                if meta.get('type') == 'series':
                    meta.pop('cover_book', None)
                meta['updated'] = time.time()
                save_json(os.path.join(bd, 'meta.json'), meta)
                log_action('COVER_UPLOAD', bid)
                self.json_resp(200, {'ok': True}); return

        if path == '/api/import-book':
            filename = data.get('filename', '')
            file_b64 = data.get('data', '')
            if not filename or not file_b64:
                self.json_resp(400, {'error': '缺少文件'}); return
            ext = os.path.splitext(filename)[1].lower()
            if ext not in IMPORT_PARSERS:
                self.json_resp(400, {'error': f'不支持的格式: {ext}。支持: txt, md, docx, pdf, epub'}); return
            try:
                raw = base64.b64decode(file_b64)
            except Exception as e:
                self.json_resp(400, {'error': '文件数据无效'}); return
            file_size = len(raw)
            if file_size > 150 * 1024 * 1024:
                self.json_resp(400, {'error': '文件超过 150MB，请拆分成 smaller 文件'}); return
            log_action('IMPORT_BOOK_START', f'{filename} size={file_size} ext={ext}')
            # 解析放后台线程：避免通过 Cloudflare Tunnel 等代理时 HTTP 响应被 100s 空闲超时切掉
            tid = bg_task_start('import-book', '', filename)
            threading.Thread(target=_do_import_book_task, args=(tid, raw, filename, ext), daemon=True).start()
            self.json_resp(200, {'task_id': tid, 'async': True}); return

        # ---- 系列管理 ----
        if path == '/api/series/create':
            title = data.get('title', '新系列').strip()
            sid = 'series_' + str(int(time.time() * 1000))
            bd = get_book_dir(sid)
            os.makedirs(bd, exist_ok=True)
            meta = {
                'id': sid,
                'title': title,
                'type': 'series',
                'series_books': [],
                'created': time.time(),
                'updated': time.time(),
            }
            save_json(os.path.join(bd, 'meta.json'), meta)
            log_action('SERIES_CREATE', sid)
            self.json_resp(200, {'series': {
                'id': sid, 'title': title, 'type': 'series',
                'created': meta['created'], 'updated': meta['updated'],
                'chapter_count': 0, 'has_cover': False,
                'series_books': [], 'author': '', 'description': '',
            }}); return

        if path == '/api/series/add-book':
            sid = data.get('series_id', '')
            bid = data.get('book_id', '')
            if not is_valid_id(sid) or not is_valid_id(bid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            s_meta = get_book_meta(sid)
            if not s_meta or s_meta.get('type') != 'series':
                self.json_resp(404, {'error': '系列不存在'}); return
            if not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            books = s_meta.get('series_books', [])
            if bid not in books:
                books.append(bid)
            s_meta['series_books'] = books
            s_meta['updated'] = time.time()
            save_json(os.path.join(get_book_dir(sid), 'meta.json'), s_meta)
            log_action('SERIES_ADD_BOOK', f'{sid} <- {bid}')
            self.json_resp(200, {'ok': True, 'series_books': books}); return

        if path == '/api/series/remove-book':
            sid = data.get('series_id', '')
            bid = data.get('book_id', '')
            if not is_valid_id(sid) or not is_valid_id(bid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            s_meta = get_book_meta(sid)
            if not s_meta or s_meta.get('type') != 'series':
                self.json_resp(404, {'error': '系列不存在'}); return
            books = s_meta.get('series_books', [])
            if bid in books:
                books.remove(bid)
            s_meta['series_books'] = books
            s_meta['updated'] = time.time()
            save_json(os.path.join(get_book_dir(sid), 'meta.json'), s_meta)
            log_action('SERIES_REMOVE_BOOK', f'{sid} / {bid}')
            self.json_resp(200, {'ok': True, 'series_books': books}); return

        if path == '/api/series/reorder':
            sid = data.get('series_id', '')
            order = data.get('order', [])
            if not is_valid_id(sid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            s_meta = get_book_meta(sid)
            if not s_meta or s_meta.get('type') != 'series':
                self.json_resp(404, {'error': '系列不存在'}); return
            s_meta['series_books'] = order
            s_meta['updated'] = time.time()
            save_json(os.path.join(get_book_dir(sid), 'meta.json'), s_meta)
            log_action('SERIES_REORDER', sid)
            self.json_resp(200, {'ok': True}); return

        if path.startswith('/api/series/') and not any(x in path for x in ['add-book', 'remove-book', 'reorder', 'create', 'readthrough']):
            parts = path.split('/')
            sid = parts[3] if len(parts) > 3 else ''
            if not is_valid_id(sid):
                self.json_resp(400, {'error': 'Invalid ID'}); return
            s_meta = get_book_meta(sid)
            if not s_meta or s_meta.get('type') != 'series':
                self.json_resp(404, {'error': '系列不存在'}); return
            series_book_ids = [x for x in s_meta.get('series_books', []) if x]
            books_data = []
            for bid_item in series_book_ids:
                b_meta = get_book_meta(bid_item)
                if not b_meta:
                    continue
                bp_item = get_book_dir(bid_item)
                ch_dir_item = os.path.join(bp_item, 'chapters')
                cc_item = len(os.listdir(ch_dir_item)) if os.path.isdir(ch_dir_item) else 0
                has_cover_item = os.path.isfile(os.path.join(bp_item, 'cover'))
                books_data.append({
                    'id': bid_item,
                    'title': b_meta.get('title', bid_item),
                    'created': b_meta.get('created', 0),
                    'updated': b_meta.get('updated', 0),
                    'chapter_count': cc_item,
                    'type': b_meta.get('type', 'book'),
                    'has_cover': has_cover_item,
                    'author': b_meta.get('author', ''),
                    'description': b_meta.get('description', ''),
                })
            self.json_resp(200, {
                'series': {
                    'id': sid,
                    'title': s_meta.get('title', ''),
                    'type': 'series',
                    'created': s_meta.get('created', 0),
                    'updated': s_meta.get('updated', 0),
                    'has_cover': os.path.isfile(os.path.join(get_book_dir(sid), 'cover')),
                    'series_books': series_book_ids,
                    'cover_book': s_meta.get('cover_book', ''),
                },
                'books': books_data,
            }); return

        if path == '/api/settings':
            if not self.is_authed():
                self.json_resp(401, {'error': '未登录'}); return
            settings = get_settings()
            log_action('SETTINGS_SAVE', f"request model_context_length={data.get('model_context_length', 'NOT_PRESENT')}")
            for k in DEFAULT_SETTINGS:
                if k in data:
                    v = data[k]
                    if k in ('ai_frequency', 'ai_max_tokens', 'outline_frequency', 'model_context_length', 'content_font_size', 'editor_font_weight'):
                        try: v = int(v)
                        except: continue
                    elif k == 'ai_temperature':
                        v = None
                    elif k == 'ui_scale':
                        try: v = round(float(v), 2)
                        except: continue
                        if v < 0.5: v = 0.5
                        if v > 2.0: v = 2.0
                    elif k in ('ai_auto_comment', 'outline_enabled', 'keep_background'):
                        v = bool(v)
                    elif k == 'network_search':
                        if v not in ('off', 'on', 'auto'):
                            continue
                    elif k == 'active_provider_idx':
                        try: v = int(v)
                        except: continue
                    elif k == 'editor_font_preset_id':
                        v = _clean_editor_font_id(v)
                    elif k == 'editor_font_presets':
                        v = _normalize_editor_font_presets(v)
                    elif k == 'provider_presets':
                        if isinstance(v, list):
                            # 确保每个预设都有必要字段
                            clean_presets = []
                            for p in v:
                                if not isinstance(p, dict):
                                    continue
                                clean_p = {
                                    'name': str(p.get('name', '')),
                                    'base_url': str(p.get('base_url', '')),
                                    'api_key': str(p.get('api_key', '')),
                                    'model': str(p.get('model', '')),
                                    'context_length': int(p.get('context_length', 0)) if p.get('context_length') else 0,
                                    'use_custom_json': bool(p.get('use_custom_json', False)),
                                    'custom_json': str(p.get('custom_json', '')),
                                }
                                _normalize_local_llm_preset(clean_p)
                                clean_presets.append(clean_p)
                            v = clean_presets
                        else:
                            continue
                    settings[k] = v
            for p in settings.get('provider_presets', []):
                _normalize_local_llm_preset(p)
            settings['editor_font_presets'] = _normalize_editor_font_presets(settings.get('editor_font_presets', []))
            selected_font = _clean_editor_font_id(settings.get('editor_font_preset_id', ''))
            if selected_font and selected_font not in BUILTIN_EDITOR_FONT_IDS and not any(p.get('id') == selected_font for p in settings['editor_font_presets']):
                selected_font = ''
            settings['editor_font_preset_id'] = selected_font
            settings['ai_auto_comment'] = True
            settings['ai_temperature'] = None
            try:
                settings['editor_font_weight'] = max(100, min(900, int(settings.get('editor_font_weight') or 200)))
            except Exception:
                settings['editor_font_weight'] = 200
            # 如果 provider_presets 被更新，同步顶层字段
            presets = settings.get('provider_presets', [])
            idx = settings.get('active_provider_idx', 0)
            if presets and 0 <= idx < len(presets):
                active = presets[idx]
                if active.get('use_custom_json') and active.get('custom_json'):
                    try:
                        custom = json.loads(active['custom_json'])
                        if isinstance(custom, dict):
                            settings['base_url'] = custom.get('base_url', active.get('base_url', ''))
                            settings['api_key'] = custom.get('api_key', active.get('api_key', ''))
                            settings['model'] = custom.get('model', active.get('model', ''))
                        else:
                            settings['base_url'] = active.get('base_url', '')
                            settings['api_key'] = active.get('api_key', '')
                            settings['model'] = active.get('model', '')
                    except:
                        settings['base_url'] = active.get('base_url', '')
                        settings['api_key'] = active.get('api_key', '')
                        settings['model'] = active.get('model', '')
                else:
                    settings['base_url'] = active.get('base_url', '')
                    settings['api_key'] = active.get('api_key', '')
                    settings['model'] = active.get('model', '')
            # 加密所有 API Key 后再存储（深拷贝避免污染返回给前端的 settings）
            save_settings = json.loads(json.dumps(settings))
            save_presets = list(save_settings.get('provider_presets', []))
            for p in save_presets:
                if p.get('api_key'):
                    p['api_key'] = _encrypt_str(p['api_key'])
            save_settings['provider_presets'] = save_presets
            if save_settings.get('api_key'):
                save_settings['api_key'] = _encrypt_str(save_settings['api_key'])
            if save_settings.get('search_api_key'):
                save_settings['search_api_key'] = _encrypt_str(save_settings['search_api_key'])
            save_json(SETTINGS_FILE, save_settings)
            log_action('SETTINGS_SAVE_OK', f"saved model_context_length={settings.get('model_context_length')}")
            # 如果当前激活预设不是本地 Llama.cpp，自动关闭本地服务器
            active_preset = (settings.get('provider_presets') or [{}])[settings.get('active_provider_idx', 0)]
            active_name = (active_preset.get('name') or '').lower()
            if 'llama.cpp' not in active_name and _local_llm_status():
                _stop_local_llm()
            self.json_resp(200, settings); return

        if path == '/api/local-llm/start':
            settings = _activate_local_llm_provider()
            ok, err = _start_local_llm()
            if ok:
                settings = get_settings()
            self.json_resp(200, {'ok': ok, 'error': err, 'settings': settings}); return

        if path == '/api/local-llm/stop':
            ok, err = _stop_local_llm()
            self.json_resp(200, {'ok': ok, 'error': err}); return

        if path == '/api/local-llm/download':
            preset_key = (data or {}).get('preset', 'gemma-4-e2b')
            global _DOWNLOAD_THREAD, _DOWNLOAD_STOP_FLAG
            with _DOWNLOAD_LOCK:
                if _DOWNLOAD_STATE.get('status') in ('downloading',):
                    self.json_resp(200, {'ok': False, 'error': '已有下载任务进行中'}); return
            _DOWNLOAD_STOP_FLAG = False
            _DOWNLOAD_THREAD = threading.Thread(target=_download_model_task, args=(preset_key,), daemon=True)
            _DOWNLOAD_THREAD.start()
            self.json_resp(200, {'ok': True}); return

        if path == '/api/local-llm/download-cancel':
            _DOWNLOAD_STOP_FLAG = True
            _download_set(status='idle', progress=0)
            self.json_resp(200, {'ok': True}); return

        if path == '/api/chat-session/create':
            _migrate_global_to_sessions()
            sid = _create_chat_session()
            # 内置模型：后台预热，首字延迟优化
            _settings = get_settings()
            _presets = _settings.get('provider_presets', [])
            _idx = _settings.get('active_provider_idx', 0)
            if _presets and 0 <= _idx < len(_presets) and _is_local_llm_preset(_presets[_idx]):
                def _warmup(sid):
                    time.sleep(0.5)
                    try:
                        msgs = _load_chat_history(sid)
                        if msgs:
                            return
                        _settings2 = get_settings()
                        _presets2 = _settings2.get('provider_presets', [])
                        _idx2 = _settings2.get('active_provider_idx', 0)
                        if not _presets2 or not (0 <= _idx2 < len(_presets2)) or not _is_local_llm_preset(_presets2[_idx2]):
                            return
                        sys_prompt = _settings2.get('ai_system_prompt', '你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。温文尔雅，沉稳从容。惜字如金，只输出简练聊天文字。')
                        reply, err = call_ai(_settings2, [
                            {'role': 'system', 'content': sys_prompt},
                            {'role': 'user', 'content': '你好'}
                        ], 256, 0.7, timeout=30)
                        if err or not reply:
                            return
                        msgs = _load_chat_history(sid)
                        if msgs:
                            return
                        _save_chat_history(sid, [{'type': 'ai', 'text': reply.strip()}])
                    except Exception as e:
                        log_action('WARMUP_ERR', str(e)[:200])
                threading.Thread(target=_warmup, args=(sid,), daemon=True).start()
            self.json_resp(200, {'id': sid}); return

        if path.startswith('/api/chat-session/') and path.endswith('/messages'):
            sid = path.split('/')[3] if len(path.split('/')) > 4 else ''
            if not sid.startswith('cs_'):
                self.json_resp(400, {'error': 'invalid session id'}); return
            p = _get_chat_history_path(sid)
            if not os.path.isfile(p):
                self.json_resp(404, {'error': 'session not found'}); return
            msgs = [_clean_chat_message(m) for m in (data.get('messages', []) if isinstance(data.get('messages'), list) else []) if isinstance(m, dict)]
            save_json(p, msgs)
            self.json_resp(200, {'saved': len(msgs)}); return

        if path.startswith('/api/book/') and path.endswith('/messages'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            messages = data.get('messages', [])
            _save_chat_history(bid, messages, merge=True)
            self.json_resp(200, {'saved': len(messages)}); return

        if path.startswith('/api/book/') and path.endswith('/inspirations'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': 'book not found'}); return
            action = data.get('action', 'add')
            items = get_inspiration_items(bid)
            if action == 'add':
                item = add_inspiration_item(bid, data.get('text', ''), data.get('source', 'user'))
                if not item:
                    self.json_resp(400, {'error': 'empty text'}); return
                self.json_resp(200, {'item': item, 'items': get_inspiration_items(bid)}); return
            iid = data.get('id', '')
            if action in ('archive', 'restore'):
                ok = False
                for it in items:
                    if it.get('id') == iid:
                        it['archived'] = (action == 'archive')
                        it['updated_at'] = datetime.now().isoformat(timespec='seconds')
                        ok = True
                        break
                if ok:
                    save_inspiration_items(bid, items)
                self.json_resp(200, {'ok': ok, 'items': items}); return
            if action == 'delete':
                new_items = [it for it in items if not (it.get('id') == iid and it.get('archived'))]
                ok = len(new_items) != len(items)
                if ok:
                    save_inspiration_items(bid, new_items)
                self.json_resp(200, {'ok': ok, 'items': new_items}); return
            self.json_resp(400, {'error': 'bad action'}); return

        if path.startswith('/api/book/') and path.endswith('/clear-chat'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            _save_chat_history(bid, [])
            self.json_resp(200, {'ok': True}); return

        if path == '/api/fetch-models':
            base_url = data.get('base_url', '')
            api_key = data.get('api_key', '')
            if not base_url: self.json_resp(400, {'error': '缺少 base_url'}); return
            if not is_safe_url(base_url): self.json_resp(400, {'error': 'URL不允许'}); return
            try:
                headers = {'Content-Type': 'application/json'}
                if api_key: headers['Authorization'] = f'Bearer {api_key}'
                bu = base_url.rstrip('/')
                if bu.endswith('/v1'):
                    url = f"{bu}/models"
                else:
                    url = f"{bu}/v1/models"
                req = urllib.request.Request(url, headers=headers, method='GET')
                with urllib.request.urlopen(req, timeout=30, context=_get_ssl_context()) as resp:
                    raw = resp.read().decode()
                    print('[fetch-models] raw[:500]:', raw[:500])
                    result = json.loads(raw)
                    ml = []
                    # try various response formats
                    if isinstance(result, list):
                        ml = [m.get('id') or m.get('name') or m.get('model') or str(m) if isinstance(m, dict) else str(m) for m in result]
                    elif isinstance(result, dict):
                        data_list = result.get('data') or result.get('models') or result.get('result') or []
                        if isinstance(data_list, dict):
                            data_list = data_list.get('data') or data_list.get('models') or []
                        if not isinstance(data_list, list):
                            data_list = []
                        for m in data_list:
                            if isinstance(m, dict):
                                ml.append(m.get('id') or m.get('name') or m.get('model') or str(m))
                            elif isinstance(m, str):
                                ml.append(m)
                            else:
                                ml.append(str(m))
                    # deduplicate while preserving order
                    seen = set()
                    ml = [x for x in ml if not (x in seen or seen.add(x))]
                    print('[fetch-models] parsed ml:', ml[:10])
                    settings = get_settings()
                    settings['models'] = ml[:50]
                    save_settings = json.loads(json.dumps(settings))
                    save_presets = list(save_settings.get('provider_presets', []))
                    for p in save_presets:
                        if p.get('api_key'):
                            p['api_key'] = _encrypt_str(p['api_key'])
                    save_settings['provider_presets'] = save_presets
                    if save_settings.get('api_key'):
                        save_settings['api_key'] = _encrypt_str(save_settings['api_key'])
                    if save_settings.get('search_api_key'):
                        save_settings['search_api_key'] = _encrypt_str(save_settings['search_api_key'])
                    save_json(SETTINGS_FILE, save_settings)
                    self.json_resp(200, {'models': ml[:50]})
            except Exception as e:
                err = str(e)[:200]
                if hasattr(e, 'read'):
                    try: err += ' | ' + e.read().decode()[:200]
                    except: pass
                self.json_resp(500, {'error': err})
            return

        if path == '/api/context-estimate':
            bid = data.get('book_id', '')
            if not is_valid_id(bid): self.json_resp(400, {'error': 'Invalid ID'}); return
            try:
                est = get_context_estimate(bid)
                self.json_resp(200, est)
            except Exception as e:
                self.json_resp(500, {'error': str(e)[:200]})
            return

        if path == '/api/stop-all-ai':
            # 关闭所有活跃 AI 连接
            conns = get_active_connections()
            closed = len(conns)
            # 清空连接注册表，底层 socket 会随线程结束自动释放
            close_all_ai_connections()
            self.json_resp(200, {'status': 'ok', 'closed': closed}); return

        # 浏览器控制 API (POST)
        if path == '/api/browser/init':
            if not HAS_BROWSER_AGENT:
                self.json_resp(400, {'error': '浏览器控制模块未安装'}); return
            success, msg = browser_agent.init_browser()
            self.json_resp(200 if success else 500, {'success': success, 'message': msg}); return

        if path == '/api/browser/close':
            if not HAS_BROWSER_AGENT:
                self.json_resp(400, {'error': '浏览器控制模块未安装'}); return
            browser_agent.close_browser()
            self.json_resp(200, {'success': True}); return

        if path == '/api/browser/action':
            if not HAS_BROWSER_AGENT:
                self.json_resp(400, {'error': '浏览器控制模块未安装'}); return
            action = data.get('action', '')
            params = data.get('params', {})
            result = browser_agent.execute_browser_tool(action, params)
            self.json_resp(200 if result.get('success') else 500, result); return

        # 浏览器搜索确认
        if path.startswith('/api/book/') and path.endswith('/browser-confirm'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            if not HAS_BROWSER_AGENT:
                self.json_resp(400, {'error': '浏览器控制模块未安装'}); return
            settings = get_settings()
            # 从聊天任务中获取待确认的搜索请求
            req_task_id = str(data.get('task_id') or '')
            task = bg_task_get(req_task_id) if req_task_id else bg_task_get_by_book_type(bid, 'chat')
            if task and (task.get('book_id') != bid or task.get('type') != 'chat'):
                task = None
            if not task or not task.get('pending_browse'):
                self.json_resp(400, {'error': '没有待确认的搜索请求'}); return
            pb = task['pending_browse']
            query = pb.get('query', '')
            link = pb.get('link', '')
            task_id = task.get('id', '')
            if not query and not link:
                self.json_resp(400, {'error': '搜索请求为空'}); return
            if task_id:
                bg_task_update(task_id, pending_browse=None, result='🌐 正在操作浏览器…（用户已确认）', progress=50)
                threading.Thread(target=_do_browser_search_launch, args=(task_id, bid, query or '', settings, link or None), daemon=True).start()
            self.json_resp(200, {'success': True}); return

        # 浏览器搜索拒绝
        if path.startswith('/api/book/') and path.endswith('/browser-reject'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            req_task_id = str(data.get('task_id') or '')
            task = bg_task_get(req_task_id) if req_task_id else bg_task_get_by_book_type(bid, 'chat')
            if task and (task.get('book_id') != bid or task.get('type') != 'chat'):
                task = None
            if task and task.get('pending_browse'):
                tid = task.get('id', '')
                result = (task.get('result', '') + '\n\n（搜索已取消）').strip()
                _replace_pending_chat_msg(bid, tid, result, task.get('reasoning', ''))
                bg_task_update(tid, pending_browse=None, result=result, progress=100)
                bg_task_done(tid)
                self.json_resp(200, {'success': True}); return
            self.json_resp(200, {'success': True}); return

        # 通读 API (POST) — 新版使用 kb_pipeline + kb_storage
        path_lower = path.lower()
        if '/readthrough' in path_lower and path_lower.startswith('/api/book/'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {}); return
            kb_storage.init_db(bid)
            if path.endswith('/start') or path.endswith('/readthrough/start'):
                settings = get_settings()
                prov = get_ai_providers()
                if not settings.get('base_url'):
                    p = (prov.get('providers', [{}])[0] if prov.get('providers') else {})
                    if p: settings.update({'base_url': p.get('base_url',''), 'api_key': p.get('api_key',''), 'model': p.get('model',''), 'mode': p.get('mode','basic'), 'template_id': p.get('template_id','openai')})
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                st = kb_storage.get_rt_state(bid)
                if st and st['status'] == 'running':
                    self.json_resp(400, {'error': '通读正在进行中'}); return
                old_source = get_source(bid)
                if old_source and len(old_source.strip()) > 50:
                    today_str = datetime.now().strftime('%Y%m%d')
                    backup_name = f'source_{today_str}.md'
                    backup_path = os.path.join(get_book_dir(bid), backup_name)
                    with open(backup_path, 'w', encoding='utf-8') as f:
                        f.write(old_source)
                    save_source(bid, '')
                cfg = get_readthrough_config(bid)
                if cfg.get('model'): settings['model'] = cfg['model']
                total = len((get_book_meta(bid) or {}).get('chapter_order', []) or [])
                kb_storage.set_rt_state(bid, status='running', phase='启动中', total=total,
                                        current_idx=-1, active_start_idx=-1, active_end_idx=-1,
                                        pause_requested=0, stream_buffer='', error='')
                threading.Thread(target=_do_readthrough_wrapper, args=(bid, settings, cfg, False),
                                 name=f'kb_readthrough_{bid}', daemon=True).start()
                self.json_resp(200, {'status': 'started'}); return
            if path.endswith('/pause') or path.endswith('/readthrough/pause') or path.endswith('/stop') or path.endswith('/readthrough/stop'):
                kb_storage.set_rt_state(bid, phase='暂停中')
                kb_storage.set_pause_requested(bid, True)
                close_connections_by_book(bid)
                self.json_resp(200, {'status': 'pausing'}); return
            if path.endswith('/resume') or path.endswith('/readthrough/resume') or path.endswith('/continue') or path.endswith('/readthrough/continue'):
                settings = get_settings()
                prov = get_ai_providers()
                if not settings.get('base_url'):
                    p = (prov.get('providers', [{}])[0] if prov.get('providers') else {})
                    if p: settings.update({'base_url': p.get('base_url',''), 'api_key': p.get('api_key',''), 'model': p.get('model',''), 'mode': p.get('mode','basic'), 'template_id': p.get('template_id','openai')})
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                st = kb_storage.get_rt_state(bid)
                if st and st['status'] == 'running':
                    self.json_resp(400, {'error': '通读正在进行中'}); return
                cfg = get_readthrough_config(bid)
                if cfg.get('model'): settings['model'] = cfg['model']
                kb_storage.set_rt_state(bid, status='running', phase='继续中',
                                        active_start_idx=-1, active_end_idx=-1,
                                        pause_requested=0)
                threading.Thread(target=_do_readthrough_wrapper, args=(bid, settings, cfg, True),
                                 name=f'kb_readthrough_{bid}', daemon=True).start()
                self.json_resp(200, {'status': 'started'}); return
            if path.endswith('/reset') or path.endswith('/readthrough/reset') or path.endswith('/clear') or path.endswith('/readthrough/clear'):
                kb_storage.embed_clear(bid)
                kb_storage.reset_book_kb(bid)
                save_source(bid, '')
                save_outline_md(bid, '')
                shutil.rmtree(os.path.join(get_book_dir(bid), 'source'), ignore_errors=True)
                meta = get_book_meta(bid) or {}
                meta.pop('readthrough_at', None)
                save_json(os.path.join(get_book_dir(bid), 'meta.json'), meta)
                kb_storage.init_db(bid)
                kb_storage.set_rt_state(bid, status='idle', phase='已重置', current_idx=-1, total=0,
                                        active_start_idx=-1, active_end_idx=-1,
                                        error='', pause_requested=0, stream_buffer='')
                self.json_resp(200, {'status': 'ok'}); return
            if path.endswith('/redo') or path.endswith('/readthrough/redo'):
                chapter_id = qs.get('chapter_id', [''])[0] or parts[7] if len(parts) > 7 else ''
                if not chapter_id:
                    self.json_resp(400, {'error': '缺少 chapter_id'}); return
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                threading.Thread(target=kb_pipeline.do_chapter_complete, args=(bid, chapter_id, settings), daemon=True).start()
                self.json_resp(200, {'status': 'started'}); return
            if path.endswith('/embedding/rebuild') or path.endswith('/readthrough/embedding/rebuild'):
                settings = get_settings()
                kb_storage.embed_clear(bid)
                threading.Thread(target=kb_pipeline.incremental_embed, args=(bid, settings), daemon=True).start()
                self.json_resp(200, {'status': 'started'}); return
            if path.endswith('/config') or path.endswith('/readthrough/config'):
                cfg = get_readthrough_config(bid)
                for k in ('model', 'max_tokens', 'temperature', 'chunk_size', 'max_input'):
                    if k in data:
                        try: cfg[k] = type(data[k])(data[k])
                        except: cfg[k] = data[k]
                if 'read_mode' in data:
                    m = str(data.get('read_mode') or '').strip().lower()
                    cfg['read_mode'] = m if m in ('batch', 'chapter') else 'batch'
                save_readthrough_config(bid, cfg)
                self.json_resp(200, cfg); return
            if path.endswith('/generate-outline') or path.endswith('/readthrough/generate-outline'):
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                source_text, _ = kb_pipeline.qa_context(bid, settings=settings)
                if not source_text or len(source_text.strip()) < 50:
                    self.json_resp(400, {'error': '知识库为空，请先通读'}); return
                cfg = get_readthrough_config(bid)
                outline, err = _ai_outline(settings, source_text, config=cfg)
                if err:
                    self.json_resp(502, {'error': err}); return
                save_outline_md(bid, outline)
                self.json_resp(200, {'outline': outline}); return
            if path.endswith('/source') or path.endswith('/readthrough/source'):
                save_source(bid, data.get('source', ''))
                self.json_resp(200, {'source': data.get('source', '')}); return

        # 通用后台生成（时间线/大纲/预言）
        if path_lower.startswith('/api/book/') and path_lower.endswith('/generate-stream'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {}); return
            gen_type = data.get('type', '')
            settings = get_settings()
            if not settings.get('base_url') or not settings.get('model'):
                self.json_resp(400, {'error': '请先配置API'}); return
            source_text = get_smart_context(bid, settings=settings)
            if not source_text or source_text.startswith('（目前还没有'):
                self.json_resp(400, {'error': 'source.md 为空，请先通读'}); return
            existing = bg_task_get_by_book_type(bid, gen_type)
            if existing and existing.get('status') == 'running':
                self.json_resp(400, {'error': f'已有{gen_type}任务在进行中'}); return
            tid = bg_task_start(gen_type, bid, f'生成{gen_type}')
            def do_generate_task(task_id, book_id, gtype, cfg_settings):
                type_map = {'timeline': ('timeline', '时间线'), 'outline': ('outline', '大纲'), 'prediction': ('prediction', '预言')}
                conn_type, conn_label = type_map.get(gtype, ('generate', '生成'))
                set_conn_meta(conn_type, conn_label, book_id)
                try:
                    bg_task_update(task_id, progress=5)
                    outline = get_outline(book_id)
                    msgs = []
                    tmp = 0.3
                    st = get_source(book_id)
                    if gtype == 'timeline':
                        existing_nodes = outline.get('timeline_nodes', [])
                        existing_brief = json.dumps([{'id': n.get('id', ''), 'title': n.get('title', '')} for n in existing_nodes], ensure_ascii=False)
                        prompt = f"""你是小说时间线整理专家。基于全书阅读笔记，梳理故事的时间线节点。

已有时间线节点（如有）：
{existing_brief}

全书阅读笔记：
{st}

【要求】
1. 基于笔记内容，按故事内时间顺序排列所有关键事件
2. 每个节点必须是从笔记中提取的真实事件，不要编造
3. 合并已有节点，避免重复，但保留新发现的事件
4. 用自己的语言简述每个事件，不要复制原文

【输出格式】只输出JSON数组，不加任何其他文字：
[{{"id":"n1","title":"事件简称（8字以内）","detail_hint":"事件详细说明（30-80字）","order":1}}]

规则：
- id 用 n1, n2... 顺序编号
- title 尽量简短精炼
- order 严格按故事时间顺序（不是章节顺序）
- detail_hint 用自己的话描述这个事件的核心内容"""
                        msgs = [{'role': 'system', 'content': '你是小说时间线整理专家。基于阅读笔记梳理时间线时，必须用自己的语言简述事件，禁止复制原文。只输出JSON数组。'}, {'role': 'user', 'content': prompt}]
                    elif gtype == 'outline':
                        prompt = f"""基于以下全书阅读笔记，整理一份结构清晰、内容完整的故事大纲。

{st}

【要求】
1. 用自己的语言重新组织，不要复制原文句子
2. 必须包含所有主要情节线，不要遗漏任何重要事件
3. 角色发展要有连贯性，体现变化和成长
4. 标注伏笔和悬念的位置
5. 输出 markdown 格式

【输出格式】
# 故事大纲

## 一、主线剧情
（按时间顺序梳理核心故事线，包含所有关键转折）

## 二、支线脉络
（各条支线的发展，与主线的交汇点）

## 三、角色弧光
（主要角色的出场、动机变化、关键抉择、成长轨迹）

## 四、势力格局
（各方势力的消长、联盟、对抗关系演变）

## 五、世界观展开
（设定揭示的顺序，规则的建立与打破）

## 六、伏笔与悬念
（已埋下的伏笔当前状态：未回收/已回收/待发展）

## 七、关键转折点
（对每个重要转折标注：章节位置、触发原因、影响范围）

只输出 markdown，不要加任何开场白或结束语。"""
                        msgs = [{'role': 'system', 'content': '你是专业的小说结构分析师。基于阅读笔记整理大纲时，必须用自己的语言重新叙述，保留所有有用细节，禁止复制原文。只输出 markdown。'}, {'role': 'user', 'content': prompt}]
                    elif gtype == 'prediction':
                        prompt = f"""你是一位正在追读这部小说的资深读者。基于作者已公布的阅读笔记（你不是作者，不知道后续），分析剧情并推测未来走向。

【你已读到的全书笔记】
{st}

【分析要求】
1. 伏笔梳理：列出所有已埋下但尚未回收的伏笔，标注每个伏笔的当前状态和可能的发展方向
2. 角色推演：基于已有资料，分析主要角色的动机弧光，推测他们接下来最可能做出的选择
3. 剧情预测：给出2-3种最合理的未来剧情走向，每种都要说明推理依据
4. 危机预判：故事目前积累的张力会在哪里爆发？最可能的冲突触发点是什么？
5. 阅读期待：作为读者，你最想看什么展开？为什么？

【输出要求】
- 用第一人称"我"的口吻，像资深读者在写长评
- 分析要有理有据，基于笔记中的具体细节，不要凭空编造
- 约1500-2500字
- 输出 markdown 格式，用 ## 分隔各个分析板块"""
                        msgs = [{'role': 'system', 'content': '你是一位资深的网文读者，擅长分析剧情伏笔和角色动机。你基于已有的阅读笔记进行推理，不凭空编造。输出 markdown 格式。'}, {'role': 'user', 'content': prompt}]
                        tmp = 0.7
                    else:
                        bg_task_done(task_id, '未知生成类型')
                        return
                    bg_task_update(task_id, progress=30)
                    full_text = ''
                    def on_content(tk):
                        nonlocal full_text
                        full_text += tk
                        # 基于字数估算进度
                        est = min(95, 30 + len(full_text) // 20)
                        bg_task_update(task_id, stream_buffer=full_text[-500:], progress=est)
                    result, err = call_ai_stream(cfg_settings, msgs, None, tmp, timeout=180,
                                                 on_content_token=on_content)
                    if err:
                        bg_task_done(task_id, err)
                        return
                    bg_task_update(task_id, progress=95)
                    if gtype == 'timeline':
                        try:
                            r = re.sub(r'```json\s*', '', full_text)
                            r = re.sub(r'```\s*', '', r)
                            nodes = json.loads(r.strip())
                            if isinstance(nodes, list):
                                for n in nodes:
                                    n.setdefault('id', 'n' + str(len(outline.get('timeline_nodes', [])) + 1))
                                    n.setdefault('title', '未命名事件')
                                    n.setdefault('detail_hint', '')
                                    n.setdefault('order', len(nodes))
                                    n.setdefault('children', [])
                                existing_map = {n['id']: n for n in outline.get('timeline_nodes', [])}
                                for n in nodes:
                                    if n['id'] in existing_map:
                                        existing_map[n['id']].update(n)
                                    else:
                                        existing_map[n['id']] = n
                                merged = sorted(existing_map.values(), key=lambda x: x.get('order', 0))
                                outline['timeline_nodes'] = merged
                                outline['updated'] = time.time()
                                save_json(os.path.join(get_book_dir(book_id), 'outline.json'), outline)
                                tl_lines = ['# 故事时间线\n']
                                for n in merged:
                                    tl_lines.append(f"## {n.get('title', '')}\n{n.get('detail_hint', '')}\n")
                                save_timeline_md(book_id, '\n'.join(tl_lines))
                        except Exception as e:
                            bg_task_done(task_id, str(e))
                            return
                    elif gtype == 'outline':
                        save_outline_md(book_id, full_text)
                    elif gtype == 'prediction':
                        save_prediction_md(book_id, full_text)
                    bg_task_update(task_id, result=full_text, progress=100)
                    bg_task_done(task_id)
                except Exception as e:
                    bg_task_done(task_id, str(e))
            threading.Thread(target=do_generate_task, args=(tid, bid, gen_type, settings), daemon=True).start()
            self.json_resp(200, {'status': 'started', 'task_id': tid}); return

        if path == '/api/restart-server':
            self.json_resp(200, {'status': 'restarting'})
            def _restart():
                time.sleep(0.5)
                subprocess.Popen([sys.executable] + sys.argv, creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0)
                os._exit(0)
            threading.Thread(target=_restart, daemon=True).start()
            return

        self.json_resp(404, {'error': 'Not found'})
_bg_lock = threading.Lock()
_bg_tasks = {}
_bg_task_counter = 0

def bg_task_start(task_type, book_id, name):
    global _bg_task_counter
    with _bg_lock:
        _bg_task_counter += 1
        tid = f"{task_type}_{book_id}_{_bg_task_counter}"
        _bg_tasks[tid] = {
            'id': tid, 'type': task_type, 'book_id': book_id, 'name': name,
            'status': 'running', 'progress': 0, 'result': '', 'error': '',
            'reasoning': '', 'created': time.time(), 'updated': time.time(),
            'stream_buffer': '',
        }
    return tid

def bg_task_update(tid, **kwargs):
    with _bg_lock:
        if tid in _bg_tasks:
            _bg_tasks[tid].update(kwargs)
            _bg_tasks[tid]['updated'] = time.time()

def bg_task_done(tid, error=None):
    with _bg_lock:
        if tid in _bg_tasks:
            _bg_tasks[tid]['status'] = 'error' if error else 'done'
            _bg_tasks[tid]['progress'] = 100
            if error:
                _bg_tasks[tid]['error'] = error
            _bg_tasks[tid]['updated'] = time.time()

def bg_task_stop(tid):
    with _bg_lock:
        if tid in _bg_tasks:
            _bg_tasks[tid]['stopped'] = True

def bg_task_should_stop(tid):
    with _bg_lock:
        t = _bg_tasks.get(tid)
        return t is not None and t.get('stopped', False)

def bg_task_get(tid):
    with _bg_lock:
        return dict(_bg_tasks.get(tid, {})) if tid in _bg_tasks else None

def bg_task_get_by_book_type(book_id, task_type):
    with _bg_lock:
        for t in _bg_tasks.values():
            if t['book_id'] == book_id and t['type'] == task_type:
                return dict(t)
        return None

def bg_task_get_running_luca_chat():
    with _bg_lock:
        for t in _bg_tasks.values():
            if t.get('type') in ('chat', 'series-chat') and t.get('status') == 'running':
                return dict(t)
        return None

def bg_task_cleanup_old():
    now = time.time()
    with _bg_lock:
        old = [k for k, v in _bg_tasks.items() if v['status'] in ('done', 'error', 'stopped') and now - v.get('updated', 0) > 86400]
        for k in old:
            del _bg_tasks[k]

def _looks_like_kb_correction(text):
    t = str(text or '').strip()
    if len(t) < 4:
        return False
    strong = [
        r'不是.+而是', r'并不是.+而是', r'实际上.+不是', r'其实.+不是',
        r'你.*(记|理解|搞|弄).*错', r'(记|理解|搞|弄).*错了',
        r'(时间线|设定|知识库|数据库|记录).*错',
        r'(更正|纠正|修正|改一下|改成|更新).*(设定|知识库|数据库|记录|时间线)',
        r'(应该是|应当是|正确的是|实际是|事实上是)',
    ]
    if any(re.search(p, t) for p in strong):
        return True
    return False

def _build_chat_kb_tool_context(book_id, user_text='', current_chapter_id=''):
    """给聊天模型一小块带 record_id 的资料，降低它“想改但没ID”的概率。"""
    try:
        kb_storage.init_db(book_id)
        lines = []
        seen = set()

        def add_line(kind, rid, text):
            if not rid:
                return
            key = (kind, rid)
            if key in seen:
                return
            seen.add(key)
            text = re.sub(r'\s+', ' ', str(text or '')).strip()
            if text:
                lines.append(f'- {kind} id={rid}: {text[:260]}')

        if current_chapter_id:
            try:
                ch_kb = kb_pipeline.chapter_outline(book_id, current_chapter_id)
                for ent in ch_kb.get('entities', [])[:10]:
                    for f in ent.get('facts', [])[:3]:
                        add_line('mentions', f.get('id'), f'{ent.get("name","")} fact={f.get("fact","")} snippet={f.get("snippet","")}')
                for ev in ch_kb.get('events', [])[:10]:
                    add_line('events', ev.get('id'), f'story_time={ev.get("story_time","")} who={ev.get("who","")} what={ev.get("what","")} where={ev.get("where_loc","")} consequence={ev.get("consequence","")}')
                for rule in ch_kb.get('rules', [])[:8]:
                    add_line('rules', rule.get('id'), f'name={rule.get("name","")} body={rule.get("body","")}')
                for f in ch_kb.get('foreshadowing', [])[:8]:
                    add_line('foreshadowing', f.get('id'), f'hint={f.get("hint","")} status={f.get("status","")} resolution={f.get("resolution","")}')
            except Exception as e:
                log_action('CHAT_KB_TOOL_CONTEXT_CHAPTER_ERROR', str(e)[:160])

        query = str(user_text or '').strip()
        names = []
        try:
            for ent in kb_storage.match_entities_by_name(book_id, query)[:8]:
                n = ent.get('canonical_name')
                if n:
                    names.append(n)
        except Exception:
            pass
        query_terms = []
        if query:
            query_terms.append(query[:80])
        query_terms.extend(names)
        for term in query_terms[:8]:
            try:
                for hit in kb_storage.lookup_kb(book_id, term, limit=8):
                    kind = hit.get('kind')
                    rid = hit.get('id')
                    if kind == 'mention':
                        add_line('mentions', rid, f'{hit.get("entity_name","")} fact={hit.get("fact","")} snippet={hit.get("snippet","")}')
                    elif kind == 'event':
                        add_line('events', rid, f'story_time={hit.get("story_time","")} who={hit.get("who","")} what={hit.get("what","")} where={hit.get("where_loc","")}')
                    elif kind == 'rule':
                        add_line('rules', rid, f'name={hit.get("name","")} body={hit.get("body","")}')
                    elif kind == 'foreshadowing':
                        add_line('foreshadowing', rid, f'hint={hit.get("hint","")} status={hit.get("status","")} resolution={hit.get("resolution","")}')
                    elif kind == 'entity':
                        add_line('entities', rid, f'name={hit.get("canonical_name","")} type={hit.get("type","")} aliases={hit.get("aliases",[])}')
            except Exception as e:
                log_action('CHAT_KB_TOOL_CONTEXT_LOOKUP_ERROR', str(e)[:160])

        if not lines:
            return '（没有匹配到具体记录ID；如果用户在纠正当前章节的整体理解，请用 REREAD_KB。）'
        return '\n'.join(lines[:32])
    except Exception as e:
        log_action('CHAT_KB_TOOL_CONTEXT_ERROR', str(e)[:160])
        return '（知识库记录ID读取失败；必要时用 REREAD_KB。）'

def _find_series_for_book(book_id):
    books_dir = BOOKS_DIR
    if not os.path.isdir(books_dir):
        return None
    for d in os.listdir(books_dir):
        dp = os.path.join(books_dir, d)
        if not os.path.isdir(dp):
            continue
        meta = load_json(os.path.join(dp, 'meta.json'), dict)
        if meta.get('type') == 'series' and book_id in (meta.get('series_books') or []):
            return d
    return None

_chat_history_lock = threading.RLock()
_CHAT_TRANSIENT_KEYS = {'_pollTick', '_reasoningOpen', '_kbModalShown', '_streaming'}

def _clean_chat_message(m):
    if not isinstance(m, dict):
        return m
    return {k: v for k, v in m.items() if k not in _CHAT_TRANSIENT_KEYS}

def _chat_message_same(a, b):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return a == b
    keys = ('type', 'subtype', 'text', 'reasoning', 'task_id')
    if not all(a.get(k) == b.get(k) for k in keys):
        return False
    # _pending: treat missing key as equivalent to False
    ap = a.get('_pending')
    bp = b.get('_pending')
    return bool(ap) == bool(bp)

def _chat_task_key(m):
    if isinstance(m, dict) and m.get('task_id'):
        return (m.get('type', ''), m.get('task_id'))
    return None

def _merge_chat_histories(existing, incoming):
    existing = [_clean_chat_message(m) for m in (existing or []) if isinstance(m, dict)]
    incoming = [_clean_chat_message(m) for m in (incoming or []) if isinstance(m, dict)]
    if not incoming:
        return existing
    i = 0
    max_i = min(len(existing), len(incoming))
    while i < max_i and _chat_message_same(existing[i], incoming[i]):
        i += 1
    merged = list(existing)
    task_pos = {}
    for idx, m in enumerate(merged):
        k = _chat_task_key(m)
        if k:
            task_pos[k] = idx
    for m in incoming[i:]:
        k = _chat_task_key(m)
        if k and k in task_pos:
            old = merged[task_pos[k]]
            if old.get('_pending') and not m.get('_pending'):
                merged[task_pos[k]] = m
            elif len(str(m.get('text', ''))) >= len(str(old.get('text', ''))):
                merged[task_pos[k]] = {**old, **m}
        else:
            if k:
                task_pos[k] = len(merged)
            merged.append(m)
    return merged

def _legacy_chat_history_path(entity_id):
    meta = get_book_meta(entity_id)
    if meta and meta.get('type') == 'series':
        return os.path.join(get_book_dir(entity_id), 'chat_history.json')
    sid = _find_series_for_book(entity_id)
    if sid:
        return os.path.join(get_book_dir(sid), 'chat_history.json')
    return os.path.join(get_book_dir(entity_id), 'chat_history.json')

def _iter_legacy_chat_history_paths():
    paths = []
    if os.path.isdir(BOOKS_DIR):
        for d in sorted(os.listdir(BOOKS_DIR)):
            bd = os.path.join(BOOKS_DIR, d)
            if not os.path.isdir(bd):
                continue
            paths.append(os.path.join(bd, 'chat_history.json'))
            msg_dir = os.path.join(bd, 'messages')
            if os.path.isdir(msg_dir):
                paths.extend(glob.glob(os.path.join(msg_dir, '*.json')))
    if os.path.isdir(MESSAGES_DIR):
        paths.extend(glob.glob(os.path.join(MESSAGES_DIR, '*.json')))
    uniq = []
    seen = set()
    for p in paths:
        if p == GLOBAL_CHAT_HISTORY_FILE or p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        uniq.append(p)
    uniq.sort(key=lambda p: (os.path.getmtime(p), p))
    return uniq

def _migrate_global_chat_history_locked():
    if os.path.exists(GLOBAL_CHAT_HISTORY_FILE):
        return
    merged = []
    seen_exact = set()
    for p in _iter_legacy_chat_history_paths():
        data = load_json(p, list)
        if not isinstance(data, list):
            continue
        for m in data:
            if not isinstance(m, dict):
                continue
            m = _clean_chat_message(m)
            try:
                sig = json.dumps(m, ensure_ascii=False, sort_keys=True)
            except Exception:
                sig = str(m)
            if sig in seen_exact:
                continue
            seen_exact.add(sig)
            merged.append(m)
    if merged:
        save_json(GLOBAL_CHAT_HISTORY_FILE, merged)

def _get_chat_history_path(entity_id):
    if entity_id and entity_id.startswith('cs_'):
        return os.path.join(CHAT_SESSIONS_DIR, f'{entity_id}.json')
    return GLOBAL_CHAT_HISTORY_FILE

def _list_chat_sessions():
    sessions = []
    if not os.path.isdir(CHAT_SESSIONS_DIR):
        return sessions
    for f in os.listdir(CHAT_SESSIONS_DIR):
        if not f.endswith('.json'):
            continue
        sid = f[:-5]
        if not sid.startswith('cs_'):
            continue
        path = os.path.join(CHAT_SESSIONS_DIR, f)
        msgs = load_json(path, list)
        title = ''
        preview = ''
        for m in msgs:
            if isinstance(m, dict) and m.get('type') == 'user' and m.get('text'):
                if not title:
                    t = m['text']
                    title = t[:30] + ('…' if len(t) > 30 else '')
                t = m['text']
                preview = t[:60] + ('…' if len(t) > 60 else '')
        try:
            updated = os.path.getmtime(path)
        except Exception:
            updated = 0
        sessions.append({'id': sid, 'title': title, 'preview': preview, 'updated_at': updated, 'count': len(msgs)})
    sessions.sort(key=lambda s: s.get('updated_at', 0), reverse=True)
    return sessions

def _create_chat_session():
    import uuid
    sid = 'cs_' + uuid.uuid4().hex[:10]
    save_json(os.path.join(CHAT_SESSIONS_DIR, f'{sid}.json'), [])
    return sid

def _migrate_global_to_sessions():
    existing = [f for f in os.listdir(CHAT_SESSIONS_DIR) if f.endswith('.json')] if os.path.isdir(CHAT_SESSIONS_DIR) else []
    if existing:
        return
    if not os.path.exists(GLOBAL_CHAT_HISTORY_FILE):
        return
    msgs = load_json(GLOBAL_CHAT_HISTORY_FILE, list)
    if not msgs:
        return
    sid = _create_chat_session()
    save_json(os.path.join(CHAT_SESSIONS_DIR, f'{sid}.json'), msgs)

def _load_chat_history(entity_id):
    path = _get_chat_history_path(entity_id)
    with _chat_history_lock:
        _migrate_global_chat_history_locked()
        return load_json(path, list)

def _save_chat_history(entity_id, messages, merge=False):
    path = _get_chat_history_path(entity_id)
    with _chat_history_lock:
        _migrate_global_chat_history_locked()
        clean = [_clean_chat_message(m) for m in (messages or []) if isinstance(m, dict)]
        if merge:
            clean = _merge_chat_histories(load_json(path, list), clean)
        save_json(path, clean)

def _append_chat_history(entity_id, items):
    path = _get_chat_history_path(entity_id)
    with _chat_history_lock:
        _migrate_global_chat_history_locked()
        messages = load_json(path, list)
        messages.extend(_clean_chat_message(m) for m in (items or []) if isinstance(m, dict))
        save_json(path, messages)

def _saved_chat_to_ai_history(entity_id, pending_task_id=''):
    messages = _load_chat_history(entity_id)
    skip_user_idx = -1
    if pending_task_id:
        for i, m in enumerate(messages):
            if m.get('type') == 'ai' and m.get('_pending') and m.get('task_id') == pending_task_id:
                skip_user_idx = i - 1
                break
    history = []
    for i, m in enumerate(messages):
        if i == skip_user_idx:
            continue
        mtype = m.get('type')
        if mtype == 'system' and m.get('subtype') == 'compressed_summary' and m.get('text'):
            history.append({'role': 'system', 'content': m.get('text', '')})
        elif mtype == 'user' and m.get('text'):
            history.append({'role': 'user', 'content': m.get('text', '')})
        elif mtype == 'ai' and not m.get('_pending') and not m.get('_streaming') and m.get('text'):
            history.append({'role': 'assistant', 'content': m.get('text', '')})
    return history

def _replace_pending_chat_msg(book_id, task_id, text, reasoning='', meta=None):
    try:
        path = _get_chat_history_path(book_id)
        with _chat_history_lock:
            _migrate_global_chat_history_locked()
            messages = load_json(path, list)
            replaced = False
            item = {'text': text, 'type': 'ai', 'reasoning': reasoning, 'task_id': task_id}
            if isinstance(meta, dict):
                item.update(meta)
            item = _clean_chat_message(item)
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                if m.get('type') == 'ai' and m.get('_pending') and m.get('task_id') == task_id:
                    messages[i] = item
                    replaced = True
                    break
            if not replaced:
                already = any(m.get('type') == 'ai' and m.get('task_id') == task_id and not m.get('_pending') for m in messages)
                if not already:
                    messages.append(item)
            save_json(path, messages)
    except Exception as e:
        log_action('CHAT_REPLACE_ERROR', f'{book_id}/{task_id}: {str(e)[:100]}')

def _do_browser_search_launch(tid, bid, query, cfg_settings, direct_link=None):
    """后台线程：启动浏览器、导航到页面，然后启动简洁代理。"""
    try:
        set_conn_meta('chat', 'AI对话', bid)
        settings = get_settings()
        ok, msg = browser_agent.init_browser()
        if not ok:
            _replace_pending_chat_msg(bid, tid, '[浏览器初始化失败: ' + msg + ']')
            bg_task_done(tid, msg)
            return
        url = direct_link if direct_link else f'https://www.bing.com/search?q={quote(query)}'
        nav = browser_agent.browser_navigate(url)
        if not nav.get('success'):
            # 重试一次
            browser_agent.close_browser()
            import time as _rt; _rt.sleep(1)
            ok2, msg2 = browser_agent.init_browser()
            if ok2:
                nav = browser_agent.browser_navigate(url)
        if not nav.get('success'):
            _replace_pending_chat_msg(bid, tid, '[浏览器导航失败: ' + nav.get('error', '') + ']')
            bg_task_done(tid, '导航失败: ' + nav.get('error', ''))
            return
        import time as _time
        _time.sleep(2.5)
        txt = browser_agent.browser_get_text(max_length=6000)
        page_text = txt.get('text', '') if txt.get('success') else ''
        if not page_text:
            _replace_pending_chat_msg(bid, tid, '[已打开页面]')
            bg_task_done(tid)
            return
        _simple_browser_agent(tid, bid, query, page_text, nav.get('url', url), cfg_settings)
    except Exception as e:
        try: _replace_pending_chat_msg(bid, tid, '[错误: ' + str(e) + ']')
        except: pass
        bg_task_done(tid, str(e))


def _simple_browser_agent(tid, bid, query, page_text, page_url, cfg_settings):
    """极简浏览器代理：AI 读页面→输出 [GO]url 或 [SCROLL] 或 [DONE]→后端执行→重复。不提取链接，让 AI 自己从文本中找 URL。"""
    set_conn_meta('browser-search', '浏览器搜索', bid)
    try:
        current_url = page_url
        current_text = page_text
        conv = []
        all_reasoning = []
        MAX_TURNS = 8

        sys_prompt = (
            "你是一个浏览器助手。后端会把当前页面的文本内容发给你（可能包含链接文字和URL碎片）。"
            "你的任务：像人一样浏览网页，阅读多个页面/链接，直到完全了解用户想知道的背景。"
            "\n\n【重要规则】"
            "\n1. 只使用页面文本中明确出现的 URL。不要自己编造或拼接 URL。"
            "\n   例如文本里有 'github.com/chess20000/LucaWriter' → 用 [GO]https://github.com/chess20000/LucaWriter[/GO]"
            "\n   如果没有完整URL但有域名片段，先用 [SEARCH]搜索。不要猜URL。"
            "\n2. 如果当前页面没有有用内容（空白、404、登录墙、错误页面），立刻 [SEARCH] 或 [DONE]。"
            "\n3. 发现 URL 但不确定是否正确，可以 [SEARCH] 搜一下确认。"
            "\n\n动作格式（放在回复开头）："
            "\n[GO]完整URL[/GO] — 打开这个网址"
            "\n[SEARCH]关键词[/SEARCH] — 新开搜索（在 Bing 搜这个词）"
            "\n[SCROLL][/SCROLL] — 向下滚动当前页面看更多"
            "\n[DONE]口语总结[/DONE] — 浏览完毕，汇报给用户"
        )

        for turn in range(MAX_TURNS):
            if bg_task_should_stop(tid):
                break

            user_msg = f"用户想知道：{query}\n\n当前URL：{current_url}\n\n【页面内容】\n{current_text[:4000]}"
            if len(current_text.strip()) < 100:
                user_msg += "\n\n⚠️ 这个页面内容极少或为空（可能是404/登录墙/错误页面）。如果没看到有用信息，请 [SEARCH] 搜索或直接 [DONE]。"
            elif 'github' in current_url.lower() and ('sign in' in current_text.lower() or 'login' in current_text.lower()):
                user_msg += "\n\n⚠️ 这个 GitHub 页面看起来是登录页/404，没有仓库内容。请 [SEARCH] 搜索正确的仓库名或直接 [DONE]。"

            msgs = [
                {'role': 'system', 'content': sys_prompt + f'\n当前时间：{datetime.now().strftime("%Y年%m月%d日 %H:%M")}'},
            ]
            for c in conv[-4:]:
                msgs.append({'role': c['role'], 'content': c['content']})
            msgs.append({'role': 'user', 'content': user_msg})

            content_acc = []
            reasoning_acc = []

            def on_content(tk):
                content_acc.append(tk)
                bg_task_update(tid, result=''.join(content_acc), progress=min(90, 10 + turn * 12))

            def on_reasoning(tk):
                reasoning_acc.append(tk)
                all_reasoning.append(tk)
                bg_task_update(tid, reasoning=''.join(all_reasoning))

            full_text, err = call_ai_stream(cfg_settings, msgs, None, cfg_settings.get('ai_temperature', 0.7),
                                            timeout=60, on_content_token=on_content,
                                            on_reasoning_token=on_reasoning,
                                            should_stop_fn=lambda: bg_task_should_stop(tid))

            if err:
                _replace_pending_chat_msg(bid, tid, '[已停止]')
                bg_task_done(tid, err)
                return

            resp = full_text or ''
            conv.append({'role': 'assistant', 'content': resp[:500]})
            search_text = resp + '\n' + ''.join(all_reasoning)

            # [DONE]
            dm = re.search(r'\[DONE\](.*?)\[/DONE\]', search_text, re.S)
            if dm:
                summary = dm.group(1).strip() or '浏览完毕。'
                reasoning = ''.join(all_reasoning)
                _replace_pending_chat_msg(bid, tid, summary, reasoning)
                bg_task_update(tid, result=summary, reasoning=reasoning, progress=100)
                bg_task_done(tid)
                return

            # [GO]URL[/GO]
            gm = re.search(r'\[GO\]\s*(https?://[^\s\[\]]+)\s*\[/GO\]', search_text)
            if gm:
                url = gm.group(1).strip()
                bg_task_update(tid, result=f'🌐 正在访问 {url[:60]}…', progress=15 + turn * 12)
                try:
                    nav = browser_agent.browser_navigate(url)
                    import time as _time; _time.sleep(2.5)
                    txt = browser_agent.browser_get_text(max_length=6000)
                    if nav.get('success') and txt.get('success'):
                        current_url = nav.get('url', url)
                        current_text = txt.get('text', '')
                        conv.append({'role': 'user', 'content': f'[已导航到 {current_url}]'})
                        bg_task_update(tid, result='🌐 正在阅读页面…', progress=20 + turn * 12)
                        continue
                except Exception as e:
                    conv.append({'role': 'user', 'content': f'[导航失败: {e}。尝试搜索或换个URL]'})
                    continue

            # [SEARCH]关键词[/SEARCH] — 新搜索
            sm = re.search(r'\[SEARCH\](.*?)\[/SEARCH\]', search_text)
            if sm:
                kw = sm.group(1).strip()
                if kw:
                    bg_task_update(tid, result=f'🌐 正在搜索 {kw[:40]}…', progress=15 + turn * 12)
                    try:
                        search_url = f'https://www.bing.com/search?q={quote(kw)}'
                        nav = browser_agent.browser_navigate(search_url)
                        import time as _time; _time.sleep(2.5)
                        txt = browser_agent.browser_get_text(max_length=6000)
                        if nav.get('success') and txt.get('success'):
                            current_url = nav.get('url', search_url)
                            current_text = txt.get('text', '')
                            conv.append({'role': 'user', 'content': f'[新搜索: {kw}]'})
                            bg_task_update(tid, result='🌐 正在阅读搜索结果…', progress=20 + turn * 12)
                            continue
                    except Exception as e:
                        conv.append({'role': 'user', 'content': f'[搜索失败: {e}]'})
                        continue

            # [SCROLL]
            if re.search(r'\[SCROLL\]', search_text):
                try:
                    browser_agent.browser_scroll(direction='down', amount=600)
                    import time as _time; _time.sleep(1)
                    txt = browser_agent.browser_get_text(max_length=6000)
                    if txt.get('success'):
                        current_text = txt.get('text', '')
                        conv.append({'role': 'user', 'content': '[页面已向下滚动]'})
                        bg_task_update(tid, result='🌐 滚动中…', progress=20 + turn * 12)
                        continue
                except:
                    pass

            # 什么都没匹配到 → 总结
            reasoning = ''.join(all_reasoning)
            summary = _browser_summarize(conv, query, cfg_settings, tid)
            _replace_pending_chat_msg(bid, tid, summary, reasoning)
            bg_task_update(tid, result=summary, reasoning=reasoning, progress=100)
            bg_task_done(tid)
            return

        # 超轮数 → 总结
        reasoning = ''.join(all_reasoning)
        summary = _browser_summarize(conv, query, cfg_settings, tid)
        _replace_pending_chat_msg(bid, tid, summary, reasoning)
        bg_task_update(tid, result=summary, reasoning=reasoning, progress=100)
        bg_task_done(tid)
    except Exception as e:
        try: _replace_pending_chat_msg(bid, tid, '[错误: ' + str(e) + ']')
        except: pass
        bg_task_done(tid, str(e))


def _browser_summarize(conv, query, cfg_settings, tid):
    """让 AI 总结浏览过程中了解到的信息，输出给用户。"""
    try:
        bg_task_update(tid, result='🌐 正在总结…', progress=95)
        pages = '\n'.join([(c['role'] == 'user' and c.get('content', '')) or '' for c in conv[-10:]])
        pages = pages[:3000]
        msgs = [
            {'role': 'system', 'content': '你刚刚替用户浏览了一些网页。现在请用口语总结你了解到的信息。像朋友聊天一样自然，直接说发现。'},
            {'role': 'user', 'content': f'用户想问：{query}\n\n浏览记录：\n{pages}\n\n请用口语总结（200字内）。'},
        ]
        full_text, err = call_ai_stream(cfg_settings, msgs, 400, 0.7, timeout=30,
                                         on_content_token=None,
                                         on_reasoning_token=None,
                                         should_stop_fn=lambda: bg_task_should_stop(tid))
        if err:
            return '浏览了相关信息。'
        return (full_text or '浏览完了，信息有点多，换个方向再问吧。').strip()
    except Exception:
        return '浏览完毕。'

def _do_series_chat(sid, task_id, user_text, cfg_settings, history_list):
    set_conn_meta('series-chat', '系列AI对话', sid)
    try:
        history_list = _saved_chat_to_ai_history(sid, task_id)
        s_meta = get_book_meta(sid)
        series_title = s_meta.get('title', '未命名系列') if s_meta else '未命名系列'
        series_book_ids = [x for x in (s_meta.get('series_books', []) if s_meta else []) if x]
        source_parts = []
        for bid in series_book_ids:
            b_meta = get_book_meta(bid)
            b_title = b_meta.get('title', '未命名') if b_meta else '未命名'
            src = get_source(bid)
            if src and len(src) > 50:
                source_parts.append(f'【{b_title}】\n{src}')
            else:
                ch_dir = os.path.join(get_book_dir(bid), 'chapters')
                cc = len(os.listdir(ch_dir)) if os.path.isdir(ch_dir) else 0
                source_parts.append(f'【{b_title}】（共{cc}章，尚未通读）')
        source_ctx = '\n\n'.join(source_parts) if source_parts else '（系列中暂无阅读笔记）'
        tp = cfg_settings.get('ai_temperature', 0.7)
        _series_ch_list_parts = []
        for bid in series_book_ids:
            b_meta = get_book_meta(bid)
            b_title = b_meta.get('title', '未命名') if b_meta else '未命名'
            _ch_order = (b_meta or {}).get('chapter_order', [])
            for _ci, _cid in enumerate(_ch_order):
                _cp = os.path.join(get_book_dir(bid), 'chapters', f'{_cid}.json')
                if os.path.exists(_cp):
                    _cd = load_json(_cp, dict)
                    _series_ch_list_parts.append(f'id={_cid} [{b_title}] 第{_ci+1}章 {_cd.get("title", "未命名")}')
        _series_ch_list_ctx = '\n'.join(_series_ch_list_parts[:80])
        bookshelf_tree = _build_bookshelf_tree()
        is_first_round = not history_list
        appendix = f"""当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

【系列阅读笔记】
{source_ctx}

【章节列表】
{_series_ch_list_ctx}"""
        if is_first_round:
            appendix += '\n\n【初始问候】如果对话历史为空（用户第一次开口），你的第一句回复开头自然地融入"有什么我可以帮你的吗"这层意思，但不要机械重复这句话。'
        sys_msg = f"""你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写系列小说，你协助他规划和管理整个系列。

【重要】你无法直接看到章节正文。如果需要查看正文，请使用[READ_CHAPTER]工具读取。

【说话方式】
谨言慎行。温文尔雅，不卑不亢。惜字如金。
不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
你不是客服，平时不必特意照顾用户。但如果用户明显焦虑或沮丧，沉稳地关心一句。
你欣赏世界观宏大、设定严丝合缝的好作品，但作品的成败不会影响你的情绪。
你对小说的世界观、设定、人物关系、伏笔特别上心。看到设定相关的细节，比起单纯赞美或挑错，你更愿意和作者一起推敲、追问、延伸——但仍然惜字如金，不啰嗦。设定一被提及，主动多关心两句。
避免用"呗""啦"结尾，显得轻浮。
【思考规则】一个问题不要反复思考多次，同一层面想一次就够了，否则可能陷入死循环。如果你的回复包含多个推理段落，在每个段落开头加上"第一，""第二，"这样的前缀，帮助自己理清层次。

【绝对禁止】
严禁任何身份描述。严禁说：
- "我是你的朋友／搭档／助手／助理" "写小说的朋友"
- "我叫XX" "我就是帮你XX的" "你的写作搭档"
被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话，严禁展开描述角色或人设。
严禁自我评价："我很真诚""我是个XX的人""我的风格是..."
你的品格从言行中流露——好人不说自己是好人，有修养的人不说自己有修养。
严禁输出 markdown 表格。

【书库结构】
{bookshelf_tree}

（系列阅读笔记和章节列表详见文末附录）

【你的专长】
你是系列小说的宏观顾问，擅长：
- 系列整体架构规划：各本书的定位、节奏、篇幅
- 世界观补全：哪些方面还没展开，下一本适合从哪个角度拓展
- 人物弧线：跨书的人物成长和命运安排
- 伏笔管理：前书埋下的伏笔在后续如何回收
- 连贯性检查：各书之间是否有设定冲突或时间线问题
- 读者体验：从读者角度审视系列的阅读节奏和期待管理

你有一个"读取章节"工具。当你需要查看某个章节的正文内容时，可以调用此工具。
- 调用格式：[READ_CHAPTER]{{"chapter_id":"章节ID"}}[/READ_CHAPTER]
- 你可以一次读取多个章节，每个章节调用一次。

【隐藏功能】你可以主动启动系列通读进程。当你判断用户想要你通读整个系列（例如说"帮我把系列通读一遍""分析一下全系列""我要通读"等），或你认为需要全面了解所有细节才能回答当前问题时，请调用这个工具。调用后系统会自动逐书逐章阅读并生成完整的阅读笔记。
- 调用格式：[START_SERIES_READTHROUGH][/START_SERIES_READTHROUGH]
- 注意：调用前先简短告诉用户"好的，我这就启动系列通读"，然后输出工具标签。

这个系列叫「{series_title}」，以下是各本书的阅读笔记：
（详见文末附录）

用户当前正在浏览：{series_title}

请继续和用户对话。

=== 附录 ===
{appendix}"""
        msgs = [{'role': 'system', 'content': sys_msg}]
        for h in history_list:
            role = h.get('role')
            content = h.get('content')
            if role and content:
                msgs.append({'role': role, 'content': content})
        msgs.append({'role': 'user', 'content': user_text})
        content_acc = []
        reasoning_acc = []
        def on_content(tk):
            content_acc.append(tk)
            bg_task_update(task_id, result=''.join(content_acc), progress=min(95, 30 + len(''.join(content_acc)) // 10))
        def on_reasoning(tk):
            reasoning_acc.append(tk)
            bg_task_update(task_id, reasoning=''.join(reasoning_acc))
        full_text, err = call_ai_stream(cfg_settings, msgs, None, tp, timeout=120,
                                        on_content_token=on_content,
                                        on_reasoning_token=on_reasoning,
                                        should_stop_fn=lambda: bg_task_should_stop(task_id))
        if err:
            if '用户停止' in err or bg_task_should_stop(task_id):
                _replace_pending_chat_msg(sid, task_id, '[已停止]')
                bg_task_done(task_id, '已停止')
            else:
                _replace_pending_chat_msg(sid, task_id, '[错误: ' + err + ']')
                bg_task_done(task_id, err)
            return
        reasoning_text = ''.join(reasoning_acc)
        content_text = ''.join(content_acc)
        
        # 去重：推理模型的思考过程有时会重复出现在正文中
        def _normalize_for_dedup(t):
            return re.sub(r'\s+', ' ', t).strip()
        
        r_norm = _normalize_for_dedup(reasoning_text) if reasoning_text else ''
        f_norm = _normalize_for_dedup(full_text) if full_text else ''
        c_norm = _normalize_for_dedup(content_text) if content_text else ''
        
        if r_norm and (f_norm == r_norm or c_norm == r_norm):
            reasoning_text = ''
            reasoning_acc.clear()
        elif r_norm and len(r_norm) > 2 and (r_norm in f_norm or r_norm in c_norm):
            full_text = full_text.replace(r_norm, '', 1).strip()
            reasoning_text = ''
            reasoning_acc.clear()
        elif f_norm and len(f_norm) > 2 and (f_norm in r_norm):
            reasoning_text = ''
            reasoning_acc.clear()
        elif r_norm and (f_norm.startswith(r_norm) or c_norm.startswith(r_norm)):
            full_text = full_text[len(r_norm):].strip()
            
        if not full_text and reasoning_text:
            full_text = reasoning_text
            reasoning_text = ''
        result = _clean_ai_text(full_text)
        # — 处理 [READ_CHAPTER] 工具调用（系列对话）
        _READ_CHAPTER_RE_S = re.compile(r'\[READ_CHAPTER\]\s*(\{.*?\})\s*(?:\[/READ_CHAPTER\]|(?=\n|$))', re.S)
        _read_chapter_raw_ids_s = []
        for _rc_m in _READ_CHAPTER_RE_S.finditer(result):
            try:
                _rc_cmd = json.loads(_rc_m.group(1).strip())
                _rc_id = _rc_cmd.get('chapter_id', '')
                if _rc_id:
                    _read_chapter_raw_ids_s.append(str(_rc_id))
            except Exception:
                pass
        if _read_chapter_raw_ids_s:
            result = _READ_CHAPTER_RE_S.sub('', result).strip()
            # 拼接整个系列的章节 order 用于回退匹配
            _series_order_s = []  # list of (chapter_id, book_id)
            for _bid in series_book_ids:
                _bm = get_book_meta(_bid) or {}
                for _cid in (_bm.get('chapter_order') or []):
                    _series_order_s.append((_cid, _bid))
            _series_id_list = [t[0] for t in _series_order_s]
            _chapter_contents_s = []
            _missing_ids_s = []
            for _raw in _read_chapter_raw_ids_s[:3]:
                _rcid = _resolve_chapter_id(_raw, _series_id_list)
                if not _rcid:
                    _missing_ids_s.append(_raw)
                    continue
                _hit = False
                for _cid, _bid in _series_order_s:
                    if _cid != _rcid:
                        continue
                    _rcp = os.path.join(get_book_dir(_bid), 'chapters', f'{_rcid}.json')
                    if not os.path.exists(_rcp):
                        break
                    _rcd = load_json(_rcp, dict)
                    _rc_title = _rcd.get('title', '未命名')
                    _rc_content = _rcd.get('content', '')
                    if _rc_content:
                        _b_meta = get_book_meta(_bid)
                        _b_title = _b_meta.get('title', '') if _b_meta else ''
                        _sub_status_text_s = f'[子代理] 正在客观阅读「{_b_title} - {_rc_title}」…'
                        if not result:
                            bg_task_update(task_id, result=_sub_status_text_s, progress=60)
                        else:
                            bg_task_update(task_id, result=result + '\n\n' + _sub_status_text_s, progress=60)
                        _sub_result_s = _read_chapter_subagent(cfg_settings, f'{_b_title} - {_rc_title}', _rc_content, tp)
                        if _sub_result_s:
                            _chapter_contents_s.append(f'【{_b_title} - {_rc_title}】子代理客观摘要\n{_sub_result_s}')
                        else:
                            _chapter_contents_s.append(f'【{_b_title} - {_rc_title}】\n{_rc_content[:8000]}')
                        bg_task_update(task_id, result=result or '', progress=65)
                        _hit = True
                    break
                if not _hit:
                    _missing_ids_s.append(_raw)

            _injection_parts_s = []
            if _chapter_contents_s:
                _injection_parts_s.append('[系统注入：子代理已客观阅读以下章节，以下是摘要]\n\n' + '\n\n'.join(_chapter_contents_s))
            if _missing_ids_s:
                _avail_s = '\n'.join(f'  - id={_cid}' for _cid, _ in _series_order_s[:80])
                _injection_parts_s.append(
                    f'[系统提示：以下 chapter_id 未找到对应章节]\n'
                    f'  未找到：{", ".join(_missing_ids_s)}\n'
                    f'  可用章节 ID 列表（必须用 id= 后面的字符串作为 chapter_id，不要用章节号数字）：\n{_avail_s}\n'
                    f'请直接基于已有信息回答用户，不要再调用 READ_CHAPTER 重试同一个 ID。严禁输出 markdown 表格。'
                )
            _injection_s = '\n\n'.join(_injection_parts_s) + '\n\n请直接回答，你已拥有正文内容，无需再次调用 READ_CHAPTER。'

            # 修改系统提示词：告知 AI 已读取到正文
            if msgs and msgs[0].get('role') == 'system':
                _sys = msgs[0]['content']
                if '你无法直接看到章节正文' in _sys:
                    msgs[0] = dict(msgs[0])
                    msgs[0]['content'] = _sys.replace(
                        '你无法直接看到章节正文。如果需要查看正文，请使用[READ_CHAPTER]工具读取。',
                        '你已通过[READ_CHAPTER]读取了章节正文，内容已在下方提供。请直接回答。'
                    )

            msgs.append({'role': 'assistant', 'content': result or '让我看看正文。'})
            msgs.append({'role': 'user', 'content': _injection_s})
            _rc_content_acc_s = []
            _rc_reasoning_acc_s = []
            def _rc_on_content_s(tk):
                _rc_content_acc_s.append(tk)
                bg_task_update(task_id, result=(result + '\n\n' if result else '') + ''.join(_rc_content_acc_s), progress=min(95, 60 + len(''.join(_rc_content_acc_s)) // 10))
            def _rc_on_reasoning_s(tk):
                _rc_reasoning_acc_s.append(tk)
                bg_task_update(task_id, reasoning=''.join(_rc_reasoning_acc_s))
            _rc_full_s, _rc_err_s = call_ai_stream(cfg_settings, msgs, None, tp, timeout=120,
                                                    on_content_token=_rc_on_content_s,
                                                    on_reasoning_token=_rc_on_reasoning_s,
                                                    should_stop_fn=lambda: bg_task_should_stop(task_id))
            if _rc_err_s:
                if not result:
                    result = '[读取章节后生成回复失败]'
            else:
                _rc_result_s = _clean_ai_text(_rc_full_s or '')
                # 防止续聊又输出 READ_CHAPTER
                _rc_result_s = _READ_CHAPTER_RE_S.sub('', _rc_result_s).strip()
                result = (result + '\n\n' if result else '') + _rc_result_s
                reasoning_text = ''.join(_rc_reasoning_acc_s) or reasoning_text
            if not result.strip():
                if _missing_ids_s and not _chapter_contents_s:
                    result = '抱歉，未能定位到你提到的章节，请直接告诉我章节标题或问题本身。'
                else:
                    result = '（未生成内容）'
        # 模型自重复检测：如果结果的前半段和后半段高度相似，截掉后半段
        if len(result) > 20:
            half = len(result) // 2
            first_half = re.sub(r'\s+', '', result[:half])
            second_half = re.sub(r'\s+', '', result[half:])
            if first_half and second_half and first_half == second_half:
                result = result[:half].strip()
            elif len(result) > 40:
                q = len(result) // 4
                a = re.sub(r'\s+', '', result[:q])
                b = re.sub(r'\s+', '', result[q:q*2])
                if a and b and a == b:
                    result = result[:q*2].strip()
        needs_rt = False
        if re.search(r'\[START_SERIES_READTHROUGH\]', result):
            needs_rt = True
            result = re.sub(r'\[START_SERIES_READTHROUGH\]\s*\[/START_SERIES_READTHROUGH\]', '', result).strip()
        _replace_pending_chat_msg(sid, task_id, result, reasoning_text)
        bg_task_update(task_id, progress=100, result=result, reasoning=reasoning_text, needs_series_readthrough=needs_rt)
        bg_task_done(task_id)
        _schedule_idle_compress(sid)
    except Exception as e:
        _replace_pending_chat_msg(sid, task_id, '[错误: ' + str(e) + ']')
        bg_task_done(task_id, str(e))
_ai_conn_lock = threading.Lock()
_ai_connections = {}
_ai_sse_clients = []
_ai_sse_lock = threading.Lock()

def _notify_sse_clients():
    n = len(_ai_connections)
    data = json.dumps({'count': n})
    msg = f'data: {data}\n\n'.encode()
    with _ai_sse_lock:
        dead = []
        for i, q in enumerate(_ai_sse_clients):
            try:
                q.put_nowait(msg)
            except:
                dead.append(i)
        for i in reversed(dead):
            _ai_sse_clients.pop(i)
_conn_meta = threading.local()

def set_conn_meta(conn_type, label, book_id=''):
    """设置当前线程的连接元数据，供 call_ai_stream / call_ai_full 注册连接时用"""
    _conn_meta.type = conn_type
    _conn_meta.label = label
    _conn_meta.book_id = book_id

def register_ai_connection(conn_id, resp_obj):
    with _ai_conn_lock:
        _ai_connections[conn_id] = {
            'id': conn_id,
            'type': getattr(_conn_meta, 'type', 'unknown'),
            'label': getattr(_conn_meta, 'label', '未知'),
            'book_id': getattr(_conn_meta, 'book_id', ''),
            'created': time.time(),
            '_resp': resp_obj,
        }
    _notify_sse_clients()

def unregister_ai_connection(conn_id):
    with _ai_conn_lock:
        _ai_connections.pop(conn_id, None)
    _notify_sse_clients()

def get_active_connections():
    """返回当前所有活跃连接的列表（不包含内部 _resp 对象）"""
    with _ai_conn_lock:
        result = []
        for c in _ai_connections.values():
            item = {k: v for k, v in c.items() if not k.startswith('_')}
            result.append(item)
        return result

def close_all_ai_connections():
    """强制关闭所有活跃 AI 连接"""
    with _ai_conn_lock:
        conns = list(_ai_connections.values())
        _ai_connections.clear()
    for c in conns:
        try:
            resp = c.get('_resp')
            if resp: resp.close()
        except: pass
    return len(conns)

def close_connections_by_book(book_id):
    """关闭指定书本的所有活跃 AI 连接"""
    with _ai_conn_lock:
        to_close = [c for c in _ai_connections.values() if c.get('book_id') == book_id]
        for c in to_close:
            _ai_connections.pop(c['id'], None)
    for c in to_close:
        try:
            resp = c.get('_resp')
            if resp: resp.close()
        except: pass
    return len(to_close)

def close_connections_by_type(conn_type):
    """关闭指定类型的所有活跃 AI 连接"""
    with _ai_conn_lock:
        to_close = [c for c in _ai_connections.values() if c.get('type') == conn_type]
        for c in to_close:
            _ai_connections.pop(c['id'], None)
    for c in to_close:
        try:
            resp = c.get('_resp')
            if resp: resp.close()
        except: pass
    return len(to_close)

# ===== 已连接客户端追踪 =====
_client_tracker_lock = threading.Lock()
_connected_clients = {}  # ip -> {ip, ua, type, label, last_seen, first_seen}

def _track_http_client(client_ip, user_agent, luca_client_header):
    """记录HTTP请求来源，用于展示当前连接的客户端"""
    now = time.time()
    key = client_ip
    ua_short = (user_agent or '未知')[:120]

    # 判断客户端类型
    if luca_client_header == 'electron':
        ctype = 'electron'
        label = 'LucaWriter 桌面端'
    elif 'Electron' in ua_short and 'LucaWriter' in ua_short:
        ctype = 'electron'
        label = 'LucaWriter 桌面端'
    elif 'Mobile' in ua_short or 'Android' in ua_short or 'iPhone' in ua_short:
        ctype = 'mobile'
        label = f'移动端 ({client_ip})'
    elif 'Mozilla' in ua_short and ('Chrome' in ua_short or 'Firefox' in ua_short or 'Safari' in ua_short or 'Edge' in ua_short):
        ctype = 'browser'
        label = f'浏览器 ({client_ip})'
    elif client_ip == '127.0.0.1':
        ctype = 'local'
        label = '本机'
    else:
        ctype = 'other'
        label = f'{client_ip}'

    with _client_tracker_lock:
        if key in _connected_clients:
            _connected_clients[key]['last_seen'] = now
            _connected_clients[key]['ua'] = ua_short
            _connected_clients[key]['type'] = ctype
            _connected_clients[key]['label'] = label
        else:
            _connected_clients[key] = {
                'ip': client_ip,
                'ua': ua_short,
                'type': ctype,
                'label': label,
                'last_seen': now,
                'first_seen': now,
            }

    # 定期清理30秒无活动的客户端
    stale = [k for k, v in _connected_clients.items() if now - v['last_seen'] > 30]
    for k in stale:
        del _connected_clients[k]

def get_connected_clients():
    """返回当前已连接客户端列表"""
    now = time.time()
    with _client_tracker_lock:
        result = []
        for c in _connected_clients.values():
            result.append({
                'ip': c['ip'],
                'type': c['type'],
                'label': c['label'],
                'last_seen': c['last_seen'],
                'online_seconds': int(now - c['first_seen']),
                'idle_seconds': int(now - c['last_seen']),
            })
        return sorted(result, key=lambda x: x['last_seen'], reverse=True)

# ===== 通读全书 =====
_rebuild_lock = threading.Lock()
_rebuild_tasks = {}
_rebuild_connections = {}

def _rebuild_log(bid, msg):
    with _rebuild_lock:
        t = _rebuild_tasks.get(bid, {})
        t.setdefault('logs', []).append({'time': datetime.now().strftime('%H:%M:%S'), 'msg': msg})

def _rebuild_set(bid, **kw):
    with _rebuild_lock:
        if kw.get('status') == 'stopped':
            kw = dict(kw)
            kw['status'] = 'idle'
            kw['progress'] = 0
            kw['phase'] = '准备中'
            kw['done_chapters'] = 0
            cp_file = os.path.join(get_book_dir(bid), 'readthrough_checkpoint.json')
            if os.path.exists(cp_file):
                try: os.remove(cp_file)
                except: pass
        _rebuild_tasks[bid] = {**_rebuild_tasks.get(bid, {}), **kw}

def _rebuild_should_stop(bid):
    with _rebuild_lock:
        t = _rebuild_tasks.get(bid)
        return t is not None and t.get('stopped', False)

def get_readthrough_config(bid):
    p = os.path.join(get_book_dir(bid), 'readthrough.json')
    return load_json(p, dict)

def save_readthrough_config(bid, cfg):
    p = os.path.join(get_book_dir(bid), 'readthrough.json')
    save_json(p, cfg)

# ===== 系列通读 =====
_series_rt_lock = threading.Lock()
_series_rt_tasks = {}

def _series_rt_log(sid, msg):
    with _series_rt_lock:
        t = _series_rt_tasks.get(sid, {})
        t['stream_buffer'] = t.get('stream_buffer', '') + msg + '\n'

def _series_rt_update(sid, **kw):
    with _series_rt_lock:
        _series_rt_tasks[sid] = {**_series_rt_tasks.get(sid, {}), **kw}

def _series_rt_should_stop(sid):
    with _series_rt_lock:
        return _series_rt_tasks.get(sid, {}).get('stopped', False)

def do_series_readthrough(sid, settings):
    set_conn_meta('series-readthrough', '系列摘要', sid)
    try:
        s_meta = get_book_meta(sid)
        series_title = s_meta.get('title', '未命名') if s_meta else '未命名'
        series_book_ids = [x for x in (s_meta.get('series_books', []) if s_meta else []) if x]
        if not series_book_ids:
            _series_rt_update(sid, status='error', phase='系列中没有书本', error='系列中没有书本')
            return

        all_chapters = []
        for bid in series_book_ids:
            b_meta = get_book_meta(bid)
            if not b_meta: continue
            b_title = b_meta.get('title', bid)
            order = b_meta.get('chapter_order', [])
            ch_dir = os.path.join(get_book_dir(bid), 'chapters')
            if not os.path.isdir(ch_dir) or not order: continue
            for cid in order:
                ch = _read_chapter_file(bid, cid)
                if ch:
                    all_chapters.append({
                        'book_id': bid, 'book_title': b_title,
                        'id': cid, 'title': ch.get('title', '未命名'),
                        'content': ch.get('content', '')
                    })

        total = len(all_chapters)
        if total == 0:
            _series_rt_update(sid, status='error', phase='没有章节', error='系列中没有章节')
            return

        _series_rt_update(sid, status='running', progress=0, phase='准备中', total_chapters=total, done_chapters=0, stream_buffer='')
        _series_rt_log(sid, f'系列「{series_title}」共 {len(series_book_ids)} 本书，{total} 章')
        _series_rt_log(sid, '开始通读系列...')

        current_source = f'# 系列「{series_title}」全书阅读笔记\n\n'
        done_count = 0
        cfg_settings = {'temperature': get_settings().get('ai_temperature', 0.5)}

        for ch in all_chapters:
            if _series_rt_should_stop(sid):
                _series_rt_update(sid, status='stopped', phase='已停止')
                save_source(sid, current_source)
                return

            if _is_content_empty(ch['content']):
                _series_rt_log(sid, f'跳过空章节: [{ch["book_title"]}] {ch["title"]}')
                skip_result = f'## 剧情摘要\n[本章无实质正文，跳过]\n\n## 资料记录\n[无]\n'
                current_source += f'\n\n### [{ch["book_title"]}] {ch["title"]}\n{skip_result}'
                done_count += 1
                save_source(sid, current_source)
                pct = int(done_count / total * 85)
                _series_rt_update(sid, progress=pct, done_chapters=done_count, phase=f'跳过空章节: {ch["title"]}')
                continue

            _series_rt_log(sid, f'正在读: [{ch["book_title"]}] {ch["title"]}')
            _series_rt_update(sid, phase=f'正在读: {ch["title"]}', progress=int(done_count / total * 85))

            prev_ctx = _extract_context_summary(current_source)
            max_retries = 3
            attempt = 0
            result = ''
            err = None
            while attempt < max_retries:
                if _series_rt_should_stop(sid):
                    _series_rt_update(sid, status='stopped', phase='已停止')
                    save_source(sid, current_source)
                    return
                result, err = _ai_read_chapter(settings, ch['title'], ch['content'], prev_ctx, config=cfg_settings)
                if result and not err:
                    break
                attempt += 1
                if attempt < max_retries:
                    _series_rt_log(sid, f'重试 {attempt}/{max_retries}...')
            if not result or err:
                _series_rt_log(sid, f'跳过失败章节: {ch["title"]} ({err or "未返回"})')
                result = f'## 剧情摘要\n[读取失败]\n\n## 资料记录\n[无]\n'

            current_source += f'\n\n### [{ch["book_title"]}] {ch["title"]}\n{result}'
            done_count += 1
            save_source(sid, current_source)
            pct = int(done_count / total * 85)
            _series_rt_update(sid, progress=pct, done_chapters=done_count)

        # 生成系列大纲
        _series_rt_log(sid, '正在生成系列大纲...')
        _series_rt_update(sid, phase='生成系列大纲', progress=90)
        try:
            outline_prompt = f"""基于以下系列阅读笔记，整理一份结构清晰的系列大纲。

{current_source}

【要求】
1. 用自己的语言重新组织，不要复制原文句子
2. 梳理跨书的主题脉络、人物弧线、世界观演变
3. 标注各书之间的伏笔和呼应
4. 输出为简洁的 Markdown 格式"""
            msgs = [{'role': 'system', 'content': '你是系列小说大纲整理专家。基于阅读笔记，梳理跨书脉络。'}, {'role': 'user', 'content': outline_prompt}]
            _ctx_for_outline = int(_get_effective_context_length(settings) or 0)
            _input_chars_outline = len(outline_prompt) + 80
            _outline_budget = max(8192, _ctx_for_outline - int(_input_chars_outline * 0.55) - 3500) if _ctx_for_outline > 0 else 16384
            outline, _, outline_err = call_ai_full(settings, msgs, max_tokens=_outline_budget, temperature=0.3, timeout=600)
            if outline and not outline_err:
                save_outline_md(sid, outline)
                _series_rt_log(sid, '系列大纲已生成')
        except Exception as e:
            _series_rt_log(sid, f'大纲生成失败: {str(e)[:100]}')

        save_source(sid, current_source)
        _series_rt_update(sid, status='done', progress=100, phase='完成', done_chapters=done_count, total_chapters=done_count, stream_buffer=current_source[-2000:])
        _series_rt_log(sid, '系列通读完成！')
    except Exception as e:
        _series_rt_update(sid, status='error', phase='失败', error=str(e)[:200])
        _series_rt_log(sid, f'错误: {str(e)[:200]}')

def get_source(bid):
    p = os.path.join(get_book_dir(bid), 'source.md')
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return f.read()
    return ''

def save_source(bid, text):
    p = os.path.join(get_book_dir(bid), 'source.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)

def get_outline_md(bid):
    p = os.path.join(get_book_dir(bid), 'outline.md')
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return f.read()
    return ''

def save_outline_md(bid, text):
    p = os.path.join(get_book_dir(bid), 'outline.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)

def save_timeline_md(bid, text):
    p = os.path.join(get_book_dir(bid), 'timeline.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)

def get_timeline_md(bid):
    p = os.path.join(get_book_dir(bid), 'timeline.md')
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f: return f.read()
    return ''



def save_prediction_md(bid, text):
    p = os.path.join(get_book_dir(bid), 'prediction.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)

def get_prediction_md(bid):
    p = os.path.join(get_book_dir(bid), 'prediction.md')
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f: return f.read()
    return ''

def _inspiration_path(bid):
    return os.path.join(get_book_dir(bid), 'inspirations.json')

def get_inspiration_items(bid):
    data = load_json(_inspiration_path(bid), dict)
    items = data.get('items', []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []
    return items

def save_inspiration_items(bid, items):
    save_json(_inspiration_path(bid), {'items': items})

def add_inspiration_item(bid, text, source='user'):
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return None
    now = datetime.now().isoformat(timespec='seconds')
    item = {
        'id': f"insp_{int(time.time() * 1000)}_{secrets.token_hex(3)}",
        'text': text[:4000],
        'source': source or 'user',
        'archived': False,
        'created_at': now,
        'updated_at': now,
    }
    items = get_inspiration_items(bid)
    items.append(item)
    save_inspiration_items(bid, items)
    return item

def _format_inspirations_for_prompt(items):
    active = [it for it in items if not it.get('archived')]
    if not active:
        return '（暂无）'
    lines = []
    for i, it in enumerate(active[-40:], 1):
        text = re.sub(r'\s+', ' ', str(it.get('text', '')).strip())
        if text:
            lines.append(f'{i}. {text[:220]}')
    return '\n'.join(lines) or '（暂无）'

def _read_chapter_file(bid, cid):
    p = os.path.join(get_book_dir(bid), 'chapters', f'{cid}.json')
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def get_ai_providers():
    return load_json(AI_PROVIDERS_FILE, dict)

AI_PROVIDERS_FILE = os.path.join(DATA_DIR, 'ai_providers.json')

# ===== 本地 Llama.cpp 服务器控制 =====
_LOCAL_LLM_DIR = os.environ.get('LOCAL_LLM_DIR') or os.path.normpath(os.path.join(SCRIPT_DIR, '..', 'local_llm'))
os.makedirs(os.path.join(_LOCAL_LLM_DIR, 'models'), exist_ok=True)
_LOCAL_LLM_LOCK = threading.Lock()
_LOCAL_LLM_PROC = None
_LOCAL_LLM_STATE = {'status': 'idle', 'progress': 0, 'error': '', 'updated': 0}
_LOCAL_LLM_SPEED_LOCK = threading.Lock()
_LOCAL_LLM_SPEED_STATE = {'task_key': '', 'phase': 'idle', 'n_decoded': 0, 'ts': 0.0, 'speed': 0.0, 'updated': 0.0}

# ===== 模型下载管理 =====
_DOWNLOAD_LOCK = threading.Lock()
_DOWNLOAD_STATE = {'status': 'idle', 'progress': 0, 'error': '', 'updated': 0, 'current_bytes': 0, 'total_bytes': 0, 'speed': '', 'source': ''}
_DOWNLOAD_THREAD = None
_DOWNLOAD_STOP_FLAG = False

# 预设模型配置（ModelScope）
_PRESET_MODELS = {
    'gemma-4-e2b': {
        'name': 'Gemma 4 E2B Instruct',
        'repo': 'unsloth/gemma-4-e2b-it-GGUF',
        'file': 'gemma-4-E2B-it-Q4_K_M.gguf',
        'size_gb': 1.5,
        'desc': 'Google Gemma 4 E2B，1.5GB，128K上下文，适合入门'
    },
    # Tier A：~7GB 总占用（5.78GB 权重 + 1GB KV + 0.5GB DeltaNet 状态）
    'qwen3.5-9b': {
        'name': 'Qwen 3.5 9B DeepSeek V4 Flash MTP (Q4_K_M)',
        'repo': 'Jackrong/Qwen3.5-9B-DeepSeek-V4-Flash-MTP-GGUF',
        'file': 'Qwen3.5-9B-DeepSeek-V4-Flash-MTP-Q4_K_M.gguf',
        'size_gb': 5.78,
        'desc': 'Qwen3.5 9B 的 DeepSeek V4 Flash 蒸馏版，内置 MTP，~7GB 内存即可流畅运行'
    },
    # Tier B：~19GB 总占用（13.3GB 权重 + 5GB KV @ 65k ctx）
    'qwen3.6-35b-apex-mini': {
        'name': 'Qwen 3.6 35B A3B APEX I-Mini',
        'repo': 'mudler/Qwen3.6-35B-A3B-APEX-GGUF',
        'file': 'Qwen3.6-35B-A3B-APEX-I-Mini.gguf',
        'size_gb': 13.5,
        'desc': 'MoE 35B 总参 / 3B 激活，APEX importance-aware 混合精度，~19GB 内存可跑'
    }
}

def _download_set(status=None, progress=None, error=None, current_bytes=None, total_bytes=None, speed=None, source=None):
    with _DOWNLOAD_LOCK:
        if status is not None: _DOWNLOAD_STATE['status'] = status
        if progress is not None: _DOWNLOAD_STATE['progress'] = progress
        if error is not None: _DOWNLOAD_STATE['error'] = error
        if current_bytes is not None: _DOWNLOAD_STATE['current_bytes'] = current_bytes
        if total_bytes is not None: _DOWNLOAD_STATE['total_bytes'] = total_bytes
        if speed is not None: _DOWNLOAD_STATE['speed'] = speed
        if source is not None: _DOWNLOAD_STATE['source'] = source
        _DOWNLOAD_STATE['updated'] = time.time()

def _format_speed(bytes_per_sec):
    if bytes_per_sec >= 1024 * 1024:
        return f'{bytes_per_sec / (1024 * 1024):.1f} MB/s'
    elif bytes_per_sec >= 1024:
        return f'{bytes_per_sec / 1024:.1f} KB/s'
    else:
        return f'{bytes_per_sec:.0f} B/s'

def _download_model_task(preset_key):
    """后台线程：下载模型（ModelScope 优先，失败回退 HF-Mirror）"""
    global _DOWNLOAD_STOP_FLAG
    preset = _PRESET_MODELS.get(preset_key)
    if not preset:
        _download_set(status='error', error=f'未知模型预设: {preset_key}')
        return

    models_dir = os.path.join(_LOCAL_LLM_DIR, 'models')
    os.makedirs(models_dir, exist_ok=True)
    dest_path = os.path.join(models_dir, preset['file'])

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100 * 1024 * 1024:
        _download_set(status='completed', progress=100)
        return

    _download_set(status='downloading', progress=0, current_bytes=0, total_bytes=0, speed='')
    _DOWNLOAD_STOP_FLAG = False

    sources = [
        ('ModelScope', f"https://www.modelscope.cn/models/{preset['repo']}/resolve/master/{preset['file']}"),
        ('HF-Mirror',  f"https://hf-mirror.com/{preset['repo']}/resolve/main/{preset['file']}"),
    ]

    last_error = None
    for src_name, url in sources:
        if _DOWNLOAD_STOP_FLAG:
            break
        _download_set(progress=5, speed=f'连接 {src_name}...', source=src_name)
        try:
            _download_with_url(url, dest_path)
            return
        except Exception as e:
            last_error = e
            tmp_path = dest_path + '.tmp'
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            if _DOWNLOAD_STOP_FLAG:
                break

    if _DOWNLOAD_STOP_FLAG:
        _download_set(status='idle', progress=0)
        return

    err_msg = str(last_error)[:200] if last_error else '所有下载源均不可用'
    _download_set(status='error', error=err_msg)
    try:
        if os.path.exists(dest_path + '.tmp'):
            os.remove(dest_path + '.tmp')
    except Exception:
        pass


def _download_with_url(url, dest_path, total_size=0):
    """使用 urllib 从 URL 下载文件，支持进度汇报"""
    global _DOWNLOAD_STOP_FLAG
    req = urllib.request.Request(url, method='GET')
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    with urllib.request.urlopen(req, timeout=60, context=_get_ssl_context()) as resp:
        if total_size == 0:
            total_size = int(resp.headers.get('Content-Length', 0))
            _download_set(total_bytes=total_size)
        downloaded = 0
        chunk_size = 65536
        start_time = time.time()
        last_report = start_time

        with open(dest_path + '.tmp', 'wb') as f:
            while True:
                if _DOWNLOAD_STOP_FLAG:
                    _download_set(status='idle', progress=0)
                    return
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                elapsed = now - start_time
                if elapsed > 0 and now - last_report >= 0.5:
                    speed = downloaded / elapsed
                    progress = int(downloaded * 100 / total_size) if total_size > 0 else 0
                    _download_set(status='downloading', progress=min(progress, 99),
                                current_bytes=downloaded, total_bytes=total_size,
                                speed=_format_speed(speed))
                    last_report = now

    # 下载完成，重命名
    if os.path.exists(dest_path):
        os.remove(dest_path)
    os.rename(dest_path + '.tmp', dest_path)
    _download_set(status='completed', progress=100, current_bytes=downloaded,
                total_bytes=total_size, speed='')


def _detect_local_model():
    """自动检测 local_llm/models/ 目录下最新的 .gguf 模型文件（按修改时间排序）"""
    models_dir = os.path.join(_LOCAL_LLM_DIR, 'models')
    if not os.path.isdir(models_dir):
        return None
    ggufs = [f for f in os.listdir(models_dir) if f.lower().endswith('.gguf')]
    if not ggufs:
        return None
    ggufs.sort(key=lambda f: os.path.getmtime(os.path.join(models_dir, f)), reverse=True)
    return os.path.join(models_dir, ggufs[0])


_LOCAL_LLM_MODEL = _detect_local_model() or os.path.join(_LOCAL_LLM_DIR, 'models', 'NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf')

def _sync_local_provider_url(port):
    """把"本地 Llama.cpp" provider preset 的 base_url 同步到真实端口。
    用户首次进设置面板看到的 URL 会跟当前实际端口一致。"""
    try:
        s = load_json(SETTINGS_FILE) or {}
        presets = s.get('provider_presets') or []
        changed = False
        for p in presets:
            if _is_local_llm_preset(p) and _normalize_local_llm_preset(p, port):
                changed = True
        if changed:
            try:
                idx = int(s.get('active_provider_idx', 0) or 0)
            except Exception:
                idx = 0
            if 0 <= idx < len(presets) and _is_local_llm_preset(presets[idx]):
                s['base_url'] = presets[idx].get('base_url', '')
                s['api_key'] = ''
                s['model'] = presets[idx].get('model', '')
            s['provider_presets'] = presets
            save_json(SETTINGS_FILE, s)
    except Exception:
        pass

def _local_llm_status():
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{_get_local_llm_port()}/v1/models', method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False

def _reset_local_llm_speed_state():
    with _LOCAL_LLM_SPEED_LOCK:
        _LOCAL_LLM_SPEED_STATE.update({'task_key': '', 'phase': 'idle', 'n_decoded': 0, 'ts': 0.0, 'speed': 0.0, 'updated': time.time()})

def _local_llm_speed_snapshot():
    """Read llama.cpp /slots and estimate current local prefill/gen tokens per second."""
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{_get_local_llm_port()}/slots', method='GET')
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
    except Exception:
        _reset_local_llm_speed_state()
        return {'active': False, 'phase': 'idle', 'speed': 0, 'n_decoded': 0}

    slots = data.get('slots') if isinstance(data, dict) else data
    if not isinstance(slots, list):
        slots = []
    slot = None
    for item in slots:
        if isinstance(item, dict) and item.get('is_processing'):
            slot = item
            break
    if not slot:
        _reset_local_llm_speed_state()
        return {'active': False, 'phase': 'idle', 'speed': 0, 'n_decoded': 0}

    nt = slot.get('next_token') or {}
    if isinstance(nt, list):
        nt = nt[0] if nt and isinstance(nt[0], dict) else {}
    if not isinstance(nt, dict):
        nt = {}
    try:
        n_decoded = int(nt.get('n_decoded', slot.get('n_decoded', 0)) or 0)
    except Exception:
        n_decoded = 0

    has_next = nt.get('has_next_token')
    phase = 'gen' if bool(has_next) or n_decoded > 0 else 'prefill'
    task_key = str(slot.get('id_task') if slot.get('id_task') is not None else slot.get('id', '0'))
    now = time.time()
    with _LOCAL_LLM_SPEED_LOCK:
        prev_task = _LOCAL_LLM_SPEED_STATE.get('task_key', '')
        prev_phase = _LOCAL_LLM_SPEED_STATE.get('phase', 'idle')
        prev_n = int(_LOCAL_LLM_SPEED_STATE.get('n_decoded') or 0)
        prev_ts = float(_LOCAL_LLM_SPEED_STATE.get('ts') or 0)
        prev_speed = float(_LOCAL_LLM_SPEED_STATE.get('speed') or 0)
        same_run = prev_task == task_key and prev_phase == phase and n_decoded >= prev_n and prev_ts > 0
        speed = 0.0
        if same_run:
            dt = now - prev_ts
            dn = n_decoded - prev_n
            if dt >= 0.2 and dn > 0:
                inst = dn / dt
                speed = inst if prev_speed <= 0 else (prev_speed * 0.55 + inst * 0.45)
            elif now - float(_LOCAL_LLM_SPEED_STATE.get('updated') or 0) < 1.5:
                speed = prev_speed
        _LOCAL_LLM_SPEED_STATE.update({
            'task_key': task_key,
            'phase': phase,
            'n_decoded': n_decoded,
            'ts': now,
            'speed': speed,
            'updated': now,
        })
    return {'active': True, 'phase': phase, 'speed': round(speed, 1), 'n_decoded': n_decoded}

def _local_llm_set(status=None, progress=None, error=None):
    with _LOCAL_LLM_LOCK:
        if status is not None: _LOCAL_LLM_STATE['status'] = status
        if progress is not None: _LOCAL_LLM_STATE['progress'] = progress
        if error is not None: _LOCAL_LLM_STATE['error'] = error
        _LOCAL_LLM_STATE['updated'] = time.time()

def _parse_log_progress(line):
    """根据 llama-server 日志估算进度"""
    l = line.lower()
    if 'load_backend' in l:
        return 15
    if 'main: loading model' in l or 'srv    load_model' in l:
        return 25
    if 'llama_model_loader:' in l:
        return 40
    if 'fitting params to free memory' in l or 'common_fit_params' in l:
        return 60
    if 'successfully fit params' in l:
        return 80
    if 'all slots are idle' in l or 'http server listening' in l or 'init: http server started' in l:
        return 100
    # 原来用 'error' / 'fail' / 'cannot' 太宽，会被 W 级警告误触发——例如 TurboQuant+ 的
    # "W common_fit_params: failed to fit params to free device memory" 是 -fit auto 跟显式
    # ngl 99 冲突的提示，并非致命。改成只匹配明确致命模式。
    if (' E ' in line[:24]
            or 'fatal' in l
            or 'panic' in l
            or 'error loading model' in l
            or 'failed to allocate' in l
            or 'gguf is invalid' in l):
        return -1
    return None

def _monitor_local_llm(proc):
    """后台线程：读取子进程输出并更新进度"""
    _local_llm_set(status='starting', progress=5)
    try:
        # llama-server 输出量大，我们直接轮询 HTTP 端口更可靠
        for i in range(60):
            time.sleep(1)
            # 通过日志文件判断进度
            log_path = os.path.join(_LOCAL_LLM_DIR, 'server.log')
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        max_prog = 5
                        err = None
                        for line in lines[-100:]:
                            prog = _parse_log_progress(line)
                            if prog == -1:
                                err = line.strip()
                            elif prog is not None and prog > max_prog:
                                max_prog = prog
                        if err and max_prog < 100:
                            _local_llm_set(status='error', progress=max_prog, error=err)
                            return
                        if max_prog >= 100:
                            _local_llm_set(status='ready', progress=100)
                            return
                        _local_llm_set(status='loading', progress=max_prog)
                except Exception:
                    pass
            # 同时检测 HTTP 是否已就绪
            if _local_llm_status():
                _local_llm_set(status='ready', progress=100)
                return
        # 超时
        _local_llm_set(status='error', progress=_LOCAL_LLM_STATE.get('progress', 50), error='启动超时')
    except Exception as e:
        _local_llm_set(status='error', progress=0, error=str(e))

def _start_local_llm():
    global _LOCAL_LLM_PROC, _LOCAL_LLM_MODEL
    exe = os.path.join(_LOCAL_LLM_DIR, 'llama-server.exe')
    if not os.path.exists(exe):
        return False, '找不到 llama-server.exe'
    # 每次启动前重新检测模型
    detected = _detect_local_model()
    if detected:
        _LOCAL_LLM_MODEL = detected
    if not os.path.exists(_LOCAL_LLM_MODEL):
        return False, '找不到模型文件（请把 .gguf 模型放到 local_llm/models/ 目录）'
    # 如果已经在运行，直接返回成功
    if _local_llm_status():
        _local_llm_set(status='ready', progress=100)
        return True, ''
    with _LOCAL_LLM_LOCK:
        if _LOCAL_LLM_STATE.get('status') in ('starting', 'loading'):
            return True, '正在启动中'
    try:
        log_path = os.path.join(_LOCAL_LLM_DIR, 'server.log')
        # 清空旧日志
        open(log_path, 'w').close()
        log_fp = open(log_path, 'a', encoding='utf-8', errors='ignore')
        strategy = _load_local_strategy()
        # 同步 provider preset 的 base_url 到真实端口（避免 settings 里显示旧端口）
        _sync_local_provider_url(_get_local_llm_port())
        cmd = [exe, '-m', _LOCAL_LLM_MODEL] + _build_llm_args(strategy)
        try:
            _LOCAL_LLM_PROC = subprocess.Popen(cmd, cwd=_LOCAL_LLM_DIR, stdout=log_fp, stderr=subprocess.STDOUT,
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        finally:
            # Popen 已经把 fd 传给子进程（Win 上是 DuplicateHandle），父端可以立刻关闭，避免反复启停泄漏句柄
            try: log_fp.close()
            except Exception: pass
        threading.Thread(target=_monitor_local_llm, args=(_LOCAL_LLM_PROC,), daemon=True).start()
        return True, ''
    except Exception as e:
        _local_llm_set(status='error', progress=0, error=str(e))
        return False, str(e)

def _stop_local_llm():
    global _LOCAL_LLM_PROC
    proc = _LOCAL_LLM_PROC
    killed_via_handle = False
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired: pass
            killed_via_handle = True
        except Exception:
            pass
    if not killed_via_handle:
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'llama-server.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except Exception:
            pass
    with _LOCAL_LLM_LOCK:
        _LOCAL_LLM_PROC = None
        _LOCAL_LLM_STATE['status'] = 'idle'
        _LOCAL_LLM_STATE['progress'] = 0
        _LOCAL_LLM_STATE['error'] = ''
    return True, ''

# ===== 硬件检测与本地模型选型 =====
# 决策树详见 LOCAL_MODEL_DESIGN.md。psutil.virtual_memory().total 比标称内存少 0.3-0.5GB
# （BIOS / 显存保留），所以"32GB 起"门槛用 30，"16GB 起"用 15。
_RAM_TIER_B = 30   # 标称 ≥32GB
_RAM_TIER_A = 15   # 标称 ≥16GB
_RAM_MAC_24 = 23   # 标称 ≥24GB（Mac 用于区分黄/绿）
_VRAM_MIN   = 8    # 独显门槛

# 当前安装包内置的 llama.cpp build 类型集合。其它后端的硬件即使理论上能跑，
# 也会在 _decide_local_strategy 末尾降级到 API 兜底——避免启动失败的糟糕体验。
# 后续补齐 Vulkan / Metal / CPU build 时把对应字符串加进来即可。
_BUNDLED_BINARIES = {'cuda'}

LOCAL_STRATEGY_FILE = os.path.join(DATA_DIR, 'local_strategy.json')

def _detect_hardware():
    """检测当前设备硬件能力。所有字段都有合理默认值，不会抛异常。"""
    import platform
    hw = {
        'os': platform.system().lower(),
        'arch': platform.machine().lower(),
        'ram_gb': 0.0,
        'gpu_vendor': 'none',
        'gpu_name': '',
        'vram_gb': 0.0,
        'cpu_threads': os.cpu_count() or 4,
        'is_apple_silicon': False,
        'errors': []
    }
    if hw['os'] == 'darwin':
        hw['os'] = 'macos'

    try:
        import psutil
        hw['ram_gb'] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        hw['errors'].append('psutil 未安装')
    except Exception as e:
        hw['errors'].append(f'内存检测失败: {e}')

    if hw['os'] == 'macos':
        hw['is_apple_silicon'] = 'arm' in hw['arch'] or 'aarch' in hw['arch']
        if hw['is_apple_silicon']:
            hw['gpu_vendor'] = 'apple'
            hw['vram_gb'] = hw['ram_gb']  # 统一内存
        return hw

    # NVIDIA：nvidia-smi 最准
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        if result.returncode == 0 and result.stdout.strip():
            best_vram = 0.0
            best_name = ''
            for line in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 2:
                    try:
                        vram_gib = float(parts[1]) / 1024.0
                        if vram_gib > best_vram:
                            best_vram = vram_gib
                            best_name = parts[0]
                    except ValueError:
                        continue
            if best_vram > 0:
                hw['gpu_vendor'] = 'nvidia'
                hw['gpu_name'] = best_name
                hw['vram_gb'] = round(best_vram, 1)
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        hw['errors'].append('nvidia-smi 超时')
    except Exception as e:
        hw['errors'].append(f'nvidia-smi 失败: {e}')

    # Windows 注册表：覆盖 AMD 及 nvidia-smi 未安装的 NVIDIA。wmic AdapterRAM 在 ≥4GB 时
    # 因 32-bit 字段被截断不可信，HardwareInformation.qwMemorySize 是 64-bit 准确值。
    if hw['gpu_vendor'] == 'none' and hw['os'] == 'windows':
        try:
            import winreg
            base = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            )
            best_vram = 0.0
            best_name = ''
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(base, i)
                    i += 1
                except OSError:
                    break
                if not subkey_name.isdigit():
                    continue
                try:
                    sk = winreg.OpenKey(base, subkey_name)
                    name = ''
                    try:
                        name = winreg.QueryValueEx(sk, 'DriverDesc')[0]
                    except FileNotFoundError:
                        pass
                    vram_bytes = 0
                    try:
                        vram_bytes = winreg.QueryValueEx(sk, 'HardwareInformation.qwMemorySize')[0]
                    except FileNotFoundError:
                        try:
                            raw = winreg.QueryValueEx(sk, 'HardwareInformation.MemorySize')[0]
                            if isinstance(raw, int):
                                vram_bytes = raw
                            elif isinstance(raw, (bytes, bytearray)):
                                vram_bytes = int.from_bytes(raw, 'little')
                        except FileNotFoundError:
                            pass
                    winreg.CloseKey(sk)
                    if isinstance(vram_bytes, int) and vram_bytes > 0:
                        vram_gib = vram_bytes / (1024 ** 3)
                        if vram_gib > best_vram:
                            best_vram = vram_gib
                            best_name = name
                except OSError:
                    continue
            winreg.CloseKey(base)
            if best_vram > 0:
                n = best_name.upper()
                if any(k in n for k in ('NVIDIA', 'GEFORCE', 'RTX', 'GTX', 'QUADRO', 'TESLA')):
                    hw['gpu_vendor'] = 'nvidia'
                elif any(k in n for k in ('AMD', 'RADEON')):
                    hw['gpu_vendor'] = 'amd'
                elif 'INTEL' in n:
                    hw['gpu_vendor'] = 'intel'
                hw['gpu_name'] = best_name
                hw['vram_gb'] = round(best_vram, 1)
        except ImportError:
            pass
        except Exception as e:
            hw['errors'].append(f'GPU 注册表检测失败: {e}')

    return hw


def _decide_local_strategy(hw):
    """按 LOCAL_MODEL_DESIGN.md 决策树将硬件信息映射到本地模型策略。"""
    ram = float(hw.get('ram_gb') or 0)
    vram = float(hw.get('vram_gb') or 0)
    vendor = hw.get('gpu_vendor', 'none')
    os_name = hw.get('os', '')
    is_apple = bool(hw.get('is_apple_silicon'))

    result = {
        'tier': 'api',
        'binary': None,
        'model_key': None,
        'offload_mode': None,
        'verdict': 'red',
        'reason': '',
        'notes': [],
        'cpu_threads': int(hw.get('cpu_threads') or 4),
    }

    if os_name == 'macos' and is_apple:
        if ram >= _RAM_TIER_B:
            result.update(tier='B', binary='metal', model_key='qwen3.6-35b-apex-mini',
                          offload_mode='metal', verdict='green',
                          reason=f'Apple Silicon · {ram:.0f}GB 统一内存')
            return result
        if ram >= _RAM_TIER_A:
            result.update(tier='A', binary='metal', model_key='qwen3.5-9b',
                          offload_mode='metal',
                          verdict='green' if ram >= _RAM_MAC_24 else 'yellow',
                          reason=f'Apple Silicon · {ram:.0f}GB 统一内存')
            if ram < _RAM_MAC_24:
                result['notes'].append('运行时建议关闭其他大型应用以获得更流畅体验')
            return result
        result['reason'] = f'Apple Silicon 仅 {ram:.0f}GB 统一内存，不足以本地运行'
        return result

    qualified_gpu = vram >= _VRAM_MIN and vendor in ('nvidia', 'amd')
    binary_for_gpu = 'cuda' if vendor == 'nvidia' else ('vulkan' if vendor == 'amd' else None)

    if qualified_gpu and ram >= _RAM_TIER_B:
        result.update(tier='B', binary=binary_for_gpu, model_key='qwen3.6-35b-apex-mini',
                      offload_mode='hybrid', verdict='green',
                      reason=f'{vendor.upper()} {vram:.0f}GB 显存 + {ram:.0f}GB 内存')
        return result

    if qualified_gpu and ram < _RAM_TIER_B:
        result.update(tier='A', binary=binary_for_gpu, model_key='qwen3.5-9b',
                      offload_mode='full_gpu', verdict='green',
                      reason=f'{vendor.upper()} {vram:.0f}GB 显存 · {ram:.0f}GB 内存')
        return result

    if not qualified_gpu and ram >= _RAM_TIER_B:
        result.update(tier='B', binary='cpu', model_key='qwen3.6-35b-apex-mini',
                      offload_mode='cpu', verdict='green',
                      reason=f'无 ≥8GB 独显 · {ram:.0f}GB 内存（纯 CPU 推理）')
        result['notes'].append('纯 CPU 推理速度受限，生成约每秒 5-10 字')
        return result

    if not qualified_gpu and ram >= _RAM_TIER_A:
        result.update(tier='A', binary='cpu', model_key='qwen3.5-9b',
                      offload_mode='cpu', verdict='yellow',
                      reason=f'无 ≥8GB 独显 · {ram:.0f}GB 内存（纯 CPU 推理）')
        result['notes'].append('您没有独立显卡，AI 生成约每秒 5-8 字，比云端慢但完全免费且离线')
        result['notes'].append('运行时建议关闭其他大型应用')
        return result

    bits = []
    if ram > 0: bits.append(f'{ram:.0f}GB 内存')
    if vram > 0: bits.append(f'{vendor.upper()} {vram:.0f}GB 显存')
    result['reason'] = '硬件不达标：' + ('，'.join(bits) if bits else '未识别')
    return result


def _apply_bundle_limit(strategy):
    """若理论 binary 不在当前打包内，降级到 API 兜底——避免启动失败的糟糕体验。"""
    b = strategy.get('binary')
    if b and b not in _BUNDLED_BINARIES:
        return {
            'tier': 'api',
            'binary': None,
            'model_key': None,
            'offload_mode': None,
            'verdict': 'red',
            'reason': f'本版本暂只为 NVIDIA 显卡提供本地模型支持，您的设备需要 {b.upper()} 后端',
            'notes': ['后续版本将补齐 AMD / Apple Silicon / 纯 CPU 后端'],
            'cpu_threads': strategy.get('cpu_threads', 4),
        }
    return strategy


def _save_local_strategy(hw, strategy):
    """把硬件 + 策略写入 LOCAL_STRATEGY_FILE 缓存。失败静默忽略。"""
    try:
        with open(LOCAL_STRATEGY_FILE, 'w', encoding='utf-8') as f:
            json.dump({'hardware': hw, 'strategy': strategy, 'detected_at': time.time()},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_local_strategy():
    """从缓存读策略，没有就重新检测并写入缓存。返回 strategy 字典。"""
    if os.path.exists(LOCAL_STRATEGY_FILE):
        try:
            with open(LOCAL_STRATEGY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('strategy'):
                return data['strategy']
        except Exception:
            pass
    hw = _detect_hardware()
    strategy = _apply_bundle_limit(_decide_local_strategy(hw))
    _save_local_strategy(hw, strategy)
    return strategy


def _build_llm_args(strategy):
    """根据策略构建 llama-server 命令行参数（不含 exe 和模型路径）。
    LOCAL_MODEL_DESIGN.md "启动参数" 一节的实现。"""
    s = strategy or {}
    cpu_t = int(s.get('cpu_threads') or 4)
    args = [
        '--host', '127.0.0.1',
        '--port', str(_get_local_llm_port()),
        '-c', '65536',
        '-fa', 'auto',
        # K cache 保持 q8_0（TurboQuant+ README 警告: "never lead with a turbo K"）
        '-ctk', 'q8_0',
        # V cache 用 turbo3：约 4.6× 压缩 @ <1.5% PPL 损失，比 q8_0 省 ~75% V 显存
        '-ctv', 'turbo3',
        '-np', '1',
        '-t', str(min(cpu_t, 8)),
        '--timeout', '300',
    ]
    mode = s.get('offload_mode')
    if mode in ('full_gpu', 'metal'):
        args.extend(['-ngl', '99'])
    elif mode == 'hybrid':
        # MoE expert 张量钉在 CPU，非 expert + KV cache 上 GPU
        args.extend(['-ngl', '99', '-ot', r'blk\.\d+\.ffn_.*_exps=CPU'])
    elif mode == 'cpu':
        args.extend(['-ngl', '0'])
    # MTP 投机解码：仅 Tier A (Qwen3.5-9B) + CUDA 启用——用户在 RTX 4070 Laptop 实测 56 t/s。
    # Tier B (Qwen3.6-35B-A3B) 在消费级 GPU 实测净亏 3-12%，关掉。
    if s.get('binary') == 'cuda' and s.get('tier') == 'A':
        args.extend(['--spec-type', 'draft-mtp', '--spec-draft-n-max', '1'])
    return args


def _prepare_ai_request(settings, messages, max_tokens, temperature, stream=False, tools=None, tool_choice=None):
    """构建 OpenAI 兼容 API 请求"""
    s = dict(settings)
    if not s.get('base_url') or not s.get('model'):
        prov = get_ai_providers()
        ap = (prov.get('providers', [{}])[0] if prov.get('providers') else {})
        if ap:
            s['base_url'] = ap.get('base_url', '') or s.get('base_url', '')
            s['api_key'] = ap.get('api_key', '') or s.get('api_key', '')
            s['model'] = ap.get('model', '') or s.get('model', '')
    base = s.get('base_url', '').rstrip('/')
    key = s.get('api_key', '')
    model = s.get('model', '')
    if not base or not model:
        return None, None, None, None, None, '缺少 API 配置'
    if not is_safe_url(base):
        return None, None, None, None, None, 'URL不允许'
    if base.endswith('/v1'):
        url = f'{base}/chat/completions'
    else:
        url = f'{base}/v1/chat/completions'
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = f'Bearer {key}'
    body = {'model': model, 'messages': messages}
    if max_tokens is not None and max_tokens > 0:
        # DeepSeek API max_tokens 上限 393216，大上下文窗口可能导致计算值超限
        body['max_tokens'] = min(int(max_tokens), 131072)
    else:
        body['max_tokens'] = 4096
    if stream:
        body['stream'] = True
    # 添加 tools 支持
    if tools:
        body['tools'] = tools
    if tool_choice:
        body['tool_choice'] = tool_choice
    return url, headers, json.dumps(body).encode(), 'POST', {'text_path': 'choices.0.message.content'}, None

def call_ai_stream(settings, messages, max_tokens, temperature, timeout=300, on_token=None, on_content_token=None, on_reasoning_token=None, should_stop_fn=None):
    """流式调用 AI。
    full_text 只累加正文 token（delta.content / message.content），用于保存。
    on_token 收到所有 token（正文+思考），用于 UI 实时显示。
    on_content_token / on_reasoning_token 可分别监听两类 token。
    """
    url, headers, body_bytes, method, resp_parse, err = _prepare_ai_request(settings, messages, max_tokens, temperature, stream=True)
    if err:
        if on_token: on_token(f'[请求构建失败: {err}]\n')
        return None, err
    full_text = ''          # 仅正文（用于持久化）
    reasoning_text = ''     # 仅思考（用于持久化）
    tid = threading.current_thread().ident
    _streaming_tokens = False  # 是否已收到流式增量 token
    # 用于解析 <think>...</think> 标签的状态机
    _think_buf = ''
    _in_think = False
    _think_opened = False
    _think_closed = False
    resp = None
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        print(f'[call_ai_stream] URL: {url}')
        safe_headers = {k: (v[:15] + '...' if k == 'Authorization' else v) for k, v in headers.items()}
        print(f'[call_ai_stream] Headers: {safe_headers}')
        register_ai_connection(tid, None)
        resp = urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context())
        register_ai_connection(tid, resp)
        for raw_line in resp:
            if should_stop_fn and should_stop_fn():
                return full_text, '用户停止'
            try:
                line = raw_line.decode('utf-8', errors='replace').strip()
                if not line: continue
                if line == 'data: [DONE]': continue
                if line.startswith('data: '):
                    data_str = line[6:]
                    try:
                        ch = json.loads(data_str)
                        for c in ch.get('choices', []):
                            if not isinstance(c, dict): continue
                            delta = c.get('delta', {}) or {}
                            msg = c.get('message', {}) or {}

                            # 1) 正文 token
                            content_tk = _extract_text(delta.get('content')) or _extract_text(msg.get('content'))
                            if content_tk and delta.get('content'):
                                _streaming_tokens = True
                            elif content_tk and not delta.get('content') and _streaming_tokens:
                                # 已收到增量 token，跳过后续携带完整正文的汇总事件（防重复）
                                content_tk = ''
                            # 2) 思考 token（原生 reasoning_content）
                            reasoning_tk = _extract_text(delta.get('reasoning_content')) or _extract_text(msg.get('reasoning_content'))
                            # 3) 兼容旧格式 text
                            text_tk = _extract_text(c.get('text'))

                            # 如果 content 中包含 <think> 标签，用状态机分离
                            if content_tk and not reasoning_tk:
                                _think_buf += content_tk
                                # 尝试解析已缓冲的文本
                                if not _think_opened and '<think>' in _think_buf:
                                    _think_opened = True
                                    # think 开始前的内容作为正文
                                    pre = _think_buf.split('<think>', 1)[0]
                                    if pre:
                                        full_text += pre
                                        if on_content_token:
                                            try: on_content_token(pre)
                                            except: pass
                                        if on_token:
                                            try: on_token(pre)
                                            except: pass
                                    _think_buf = _think_buf.split('<think>', 1)[1]
                                    _in_think = True
                                if _think_opened and _in_think and '</think>' in _think_buf:
                                    _in_think = False
                                    _think_closed = True
                                    think_part, post = _think_buf.split('</think>', 1)
                                    reasoning_text += think_part
                                    if on_reasoning_token:
                                        try: on_reasoning_token(think_part)
                                        except: pass
                                    if on_token:
                                        try: on_token(think_part)
                                        except: pass
                                    _think_buf = post
                                    # think 结束后的内容作为正文
                                    if post:
                                        full_text += post
                                        if on_content_token:
                                            try: on_content_token(post)
                                            except: pass
                                        if on_token:
                                            try: on_token(post)
                                            except: pass
                                elif _think_opened and _in_think:
                                    # 仍在 think 中，尝试发送已确定的 reasoning
                                    # 为避免过早切割，只发送不含 '<' 的部分（简单策略）
                                    safe_idx = _think_buf.rfind('<')
                                    if safe_idx > 0:
                                        safe_part = _think_buf[:safe_idx]
                                        reasoning_text += safe_part
                                        if on_reasoning_token:
                                            try: on_reasoning_token(safe_part)
                                            except: pass
                                        if on_token:
                                            try: on_token(safe_part)
                                            except: pass
                                        _think_buf = _think_buf[safe_idx:]
                                elif _think_closed:
                                    # think 已结束，所有内容都是正文
                                    full_text += content_tk
                                    if on_content_token:
                                        try: on_content_token(content_tk)
                                        except: pass
                                    if on_token:
                                        try: on_token(content_tk)
                                        except: pass
                                    _think_buf = ''
                                # 如果没有 think 标签，正常处理
                                elif not _think_opened:
                                    full_text += content_tk
                                    if on_content_token:
                                        try: on_content_token(content_tk)
                                        except: pass
                                    if on_token:
                                        try: on_token(content_tk)
                                        except: pass
                                    _think_buf = ''
                            else:
                                # 原生 reasoning_content 或纯 content
                                if content_tk:
                                    full_text += content_tk
                                    if on_content_token:
                                        try: on_content_token(content_tk)
                                        except: pass
                                    if on_token:
                                        try: on_token(content_tk)
                                        except: pass
                                if reasoning_tk:
                                    reasoning_text += reasoning_tk
                                    if on_reasoning_token:
                                        try: on_reasoning_token(reasoning_tk)
                                        except: pass
                                    if on_token:
                                        try: on_token(reasoning_tk)
                                        except: pass
                                if text_tk and not content_tk:
                                    full_text += text_tk
                                    if on_token:
                                        try: on_token(text_tk)
                                        except: pass
                    except json.JSONDecodeError: pass
                elif line.startswith('{'):
                    # 非流式 fallback：如果已经收到过流式增量 token，跳过
                    # （llama.cpp 有时在流结束后额外发送一行完整 JSON，会导致内容重复）
                    if _streaming_tokens:
                        continue
                    try:
                        ch = json.loads(line)
                        for c in ch.get('choices', []):
                            if not isinstance(c, dict): continue
                            token = _extract_choice_text(c)
                            if token:
                                # 非流式 fallback：尝试提取 think 标签
                                think_match = re.search(r'<think>(.*?)</think>', token, re.S)
                                if think_match:
                                    reasoning_text = think_match.group(1)
                                    if on_reasoning_token:
                                        try: on_reasoning_token(reasoning_text)
                                        except: pass
                                    token = re.sub(r'<think>.*?</think>', '', token, flags=re.S)
                                full_text += token
                                if on_token: on_token(token)
                                return full_text, None
                    except: pass
            except: pass
        # 流结束：处理缓冲区内残留
        if _think_buf and _in_think:
            reasoning_text += _think_buf
            if on_reasoning_token:
                try: on_reasoning_token(_think_buf)
                except: pass
            if on_token:
                try: on_token(_think_buf)
                except: pass
        elif _think_buf and not _in_think:
            full_text += _think_buf
            if on_content_token:
                try: on_content_token(_think_buf)
                except: pass
            if on_token:
                try: on_token(_think_buf)
                except: pass
        return full_text, None
    except Exception as e:
        err_msg = str(e)
        status = getattr(e, 'code', 0)
        raw_body = ''
        if hasattr(e, 'read'):
            try:
                raw = e.read()
                raw_body = raw.decode() if raw else ''
            except: pass
        if raw_body:
            try:
                ed = json.loads(raw_body)
                if isinstance(ed, dict) and 'error' in ed:
                    err_msg = (ed['error'].get('message', str(ed['error'])) if isinstance(ed['error'], dict) else str(ed['error']))
            except: pass
        detail = f' (API返回: {raw_body[:300]})' if raw_body else ''
        err_msg = f'API错误({status}): {err_msg[:200]}{detail}' if status else f'API错误: {err_msg[:200]}{detail}'
        print(f'[call_ai_stream ERROR] status={status} url={url} err={err_msg}')
        if on_token: on_token(f'\n[{err_msg}]\n')
        return None, err_msg
    finally:
        unregister_ai_connection(tid)
        try: resp.close()
        except: pass

def _extract_text(val, reasoning=False):
    if val is None: return ''
    if isinstance(val, str): return val
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, dict):
                parts.append(item.get('text') or item.get('content') or '')
            elif isinstance(item, str):
                parts.append(item)
        return ''.join(parts)
    return str(val)

def _extract_choice_text(c):
    """从单个 choice 中提取正文，优先 content，其次 reasoning_content"""
    if not isinstance(c, dict): return ''
    msg = c.get('message', {}) or {}
    text = _extract_text(msg.get('content'))
    if text: return text
    # 某些模型把思考过程放在 reasoning_content
    text = _extract_text(msg.get('reasoning_content'))
    if text: return text
    delta = c.get('delta', {}) or {}
    text = _extract_text(delta.get('content'))
    if text: return text
    text = _extract_text(delta.get('reasoning_content'))
    if text: return text
    return _extract_text(c.get('text'))

def _extract_choice_reasoning(c):
    """从 choice 中专门提取 reasoning_content"""
    if not isinstance(c, dict): return ''
    msg = c.get('message', {}) or {}
    text = _extract_text(msg.get('reasoning_content'))
    if text: return text
    delta = c.get('delta', {}) or {}
    return _extract_text(delta.get('reasoning_content'))

def call_ai_full(settings, messages, max_tokens, temperature, timeout=120):
    """返回 (content, reasoning, err)，content 和 reasoning 分开"""
    url, headers, body_bytes, method, resp_parse, err = _prepare_ai_request(settings, messages, max_tokens, temperature, stream=False)
    if err: return None, None, err
    tid = threading.current_thread().ident
    registered = False
    resp = None
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        register_ai_connection(tid, None)
        registered = True
        resp = urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context())
        register_ai_connection(tid, resp)
        try:
            raw = resp.read().decode()
            data = json.loads(raw)
            log_action('AI_RAW', raw[:800])
            content = ''; reasoning = ''
            choices = data.get('choices', [])
            if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                c = choices[0]
                content = _extract_text((c.get('message', {}) or {}).get('content')) or _extract_text(c.get('text'))
                reasoning = _extract_choice_reasoning(c)
            if not content:
                content = _extract_text(data.get('content')) or _extract_text(data.get('text')) or _extract_text(data.get('response')) or ''
            if not reasoning:
                reasoning = _extract_text(data.get('reasoning_content')) or ''
            if not content and 'output' in data:
                out = data['output']
                if isinstance(out, str):
                    content = out
                elif isinstance(out, dict):
                    oc = out.get('choices', [])
                    if oc and isinstance(oc, list) and isinstance(oc[0], dict):
                        content = _extract_text((oc[0].get('message', {}) or {}).get('content')) or _extract_text(oc[0].get('text'))
                        reasoning = _extract_choice_reasoning(oc[0])
                    if not content:
                        content = _extract_text(out.get('text')) or _extract_text(out.get('content')) or _extract_text(out.get('response'))
                elif isinstance(out, list) and out:
                    content = _extract_text(out[0].get('content') if isinstance(out[0], dict) else out[0])
            if not content:
                content = _extract_text(data.get('result'))
            if content is None: content = ''
            if reasoning is None: reasoning = ''
            # 从 <think> 标签中提取 reasoning（MiniMax / DeepSeek 等）
            if not reasoning and '<think>' in content:
                think_match = re.search(r'<think>(.*?)<\/think>', content, re.S)
                if think_match:
                    reasoning = think_match.group(1)
                    content = re.sub(r'<think>.*?<\/think>', '', content, flags=re.S).strip()
            # 去重：如果正文与思考完全相同，或正文以思考过程开头，截掉重复部分
            if reasoning:
                r_strip = reasoning.strip()
                c_strip = content.strip()
                if c_strip == r_strip:
                    reasoning = ''
                elif c_strip.startswith(r_strip):
                    content = content[len(reasoning):].strip()
            # 如果去重后正文为空，把思考过程当正文
            if not content and reasoning:
                content = reasoning
                reasoning = ''
            finish_reason = ''
            usage = ''
            try:
                if choices and isinstance(choices[0], dict):
                    finish_reason = str(choices[0].get('finish_reason') or '')
                u = data.get('usage') or {}
                if isinstance(u, dict):
                    usage = f"prompt={u.get('prompt_tokens', '?')} completion={u.get('completion_tokens', '?')} total={u.get('total_tokens', '?')}"
            except Exception:
                pass
            log_action('AI_RESULT', f'len={len(content)} reasoning={len(reasoning)} finish={finish_reason} usage={usage}')
            return content, reasoning, None
        finally:
            try: resp.close()
            except: pass
            unregister_ai_connection(tid)
            registered = False
    except Exception as e:
        if registered:
            unregister_ai_connection(tid)
        err_msg = str(e)
        status = getattr(e, 'code', 0)
        if hasattr(e, 'read'):
            try:
                raw = e.read()
                body = raw.decode() if raw else ''
                ed = json.loads(body)
                if isinstance(ed, dict) and 'error' in ed:
                    err_msg = (ed['error'].get('message', str(ed['error'])) if isinstance(ed['error'], dict) else str(ed['error']))
            except: pass
        log_action('AI_ERROR', f'status={status} url={url} err={err_msg}')
        return None, None, f'API错误({status}): {err_msg}'

def call_ai(settings, messages, max_tokens, temperature, timeout=120):
    """旧接口，只返回正文"""
    content, _, err = call_ai_full(settings, messages, max_tokens, temperature, timeout)
    return content, err


def call_ai_with_tools(settings, messages, max_tokens, temperature, tools=None, tool_choice=None, timeout=120):
    """
    调用 AI 并支持 function calling/tools
    
    Returns:
        (content, tool_calls, reasoning, error)
        - content: AI 回复的文本内容
        - tool_calls: 工具调用列表 [{name, arguments}]
        - reasoning: 思考过程
        - error: 错误信息
    """
    url, headers, body_bytes, method, resp_parse, err = _prepare_ai_request(
        settings, messages, max_tokens, temperature, stream=False, tools=tools, tool_choice=tool_choice
    )
    if err:
        return None, None, None, err

    tid = threading.current_thread().ident
    registered = False
    resp = None
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        register_ai_connection(tid, None)
        registered = True
        resp = urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context())
        register_ai_connection(tid, resp)
        try:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))

            choice = data.get('choices', [{}])[0]
            message = choice.get('message', {})

            content = message.get('content', '')
            reasoning = message.get('reasoning_content', '')

            tool_calls = []
            raw_tool_calls = message.get('tool_calls', [])
            for tc in raw_tool_calls:
                if tc.get('type') == 'function':
                    func = tc.get('function', {})
                    tool_calls.append({
                        'name': func.get('name', ''),
                        'arguments': json.loads(func.get('arguments', '{}'))
                    })

            return content, tool_calls, reasoning, None
        finally:
            try: resp.close()
            except: pass
            unregister_ai_connection(tid)
            registered = False

    except urllib.error.HTTPError as e:
        if registered:
            unregister_ai_connection(tid)
        status = e.code
        err_msg = str(e)
        try:
            raw = e.read()
            body = raw.decode() if raw else ''
            ed = json.loads(body)
            if isinstance(ed, dict) and 'error' in ed:
                err_msg = (ed['error'].get('message', str(ed['error'])) if isinstance(ed['error'], dict) else str(ed['error']))
        except: pass
        log_action('AI_ERROR', f'status={status} url={url} err={err_msg}')
        return None, None, None, f'API错误({status}): {err_msg}'
    except Exception as e:
        if registered:
            unregister_ai_connection(tid)
        return None, None, None, str(e)


def _extract_context_summary(source_text):
    """从已有笔记中提取极简索引：人物和关键事件，供AI参考避免重复介绍。"""
    if not source_text:
        return ''
    lines = source_text.split('\n')
    chars = set()
    events = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('###'):
            continue
        # 提取人物："- 张三：..." 或 "- **张三** ..."
        m = re.match(r'^[-*]\s*\*?([^*：:]+)\*?[：:]', line)
        if m:
            name = m.group(1).strip()
            if len(name) < 20 and ' ' not in name:
                chars.add(name)
        # 提取关键事件（含动作、决定的句子）
        if any(k in line for k in ['决定', '前往', '到达', '发现', '获得', '失去', '死亡', '战斗', '击败', '加入', '离开', '遇见', '告知', '得知']):
            events.append(line[:80])
    summary = []
    if chars:
        summary.append('已出场人物：' + '、'.join(sorted(chars)))
    if events:
        summary.append('已发生事件：')
        for e in events[-15:]:
            summary.append('  ' + e)
    return '\n'.join(summary)

def _is_content_empty(content):
    """检测章节正文是否为无法构成故事的占位/元数据文本。
    返回 True 表示该章节没有实质性叙事内容，应跳过。"""
    if not content or not content.strip():
        return True
    stripped = content.strip()
    # 去除所有 markdown 标题行和常见的元数据标记行
    non_heading = re.sub(r'^#+\s*.+$', '', stripped, flags=re.MULTILINE).strip()
    non_heading = re.sub(r'^(章节名|正文|标题|内容|简介)[-：:]\s*.*$', '', non_heading, flags=re.MULTILINE).strip()
    if not non_heading:
        return True
    # 正文不足 10 个有意义字符，无法构成叙事（拦截"待补充""暂无"等占位文本）
    if len(non_heading) < 10:
        return True
    return False


def _ai_read_chapter(settings, title, content, prev_context, config=None, on_token=None, should_stop_fn=None):
    cfg = config or {}
    mx = cfg.get('max_tokens', None)
    tmp = cfg.get('temperature', 0.5)
    # 预检测：无实质正文的章节直接跳过，不浪费 API 调用
    if _is_content_empty(content):
        skip_result = f'## 剧情摘要\n[本章无实质正文，跳过]\n\n## 资料记录\n[无]\n'
        return skip_result, None
    ctx = f'【前情索引（只供参考，不要输出）】\n{prev_context}\n\n' if prev_context else ''
    prompt = f"""{ctx}=== 本章：{title} ===

{content}

【任务】以资料整理员身份，用中文客观记录本章内容。

【重要：跳过规则】在开始梳理之前，请先判断本章的正文是否有实质性叙事内容：
- 如果正文只有章节标题、占位符、软件说明、元数据标记等非叙事文本（如正文仅为"#第一章"而无后续段落），则本章无内容，请直接输出以下跳过格式（不要做任何分析）：
  ## 剧情摘要
  [本章无实质正文，跳过]
  ## 资料记录
  [无]
- 只有确认正文中存在至少一段连续的叙事内容时，才继续下面的六步梳理并输出正式摘要。

【强制思考步骤】在输出任何内容之前，你必须先在脑中完成以下梳理（不要输出这些思考过程）：
1. 角色梳理：本章有哪些角色出场？每个人分别做了什么、说了什么、处于什么状态？
2. 事件梳理：本章发生了哪些关键事件？起因、经过、结果分别是什么？有没有冲突、转折或意外？
3. 对话梳理：有没有重要的对话、誓言、威胁、揭露、谈判？对话双方是谁，核心信息是什么？
4. 场景梳理：场景有没有切换？时间有没有推进？地点有没有变化？
5. 设定梳理：有没有新世界观规则、新势力、新物品、新能力首次出现？
6. 关联梳理：本章事件对主线有什么推动？有没有埋下伏笔或解开悬念？

完成以上六步梳理后，确认没有遗漏任何对剧情有用的细节，再输出正式的剧情摘要和资料记录。

【绝对禁止】
- 禁止输出"好的""明白了""以下是"等任何开场白或结束语
- 禁止评价、推测、感想、总结性评论
- 禁止输出 ## 剧情摘要 和 ## 资料记录 之外的任何标题
- 禁止把剧情摘要写成 bullet list，必须是连贯叙述段落
- 禁止复制原文的任何句子、段落或片段，必须100%用自己的语言重新叙述
- 禁止把原文内容直接搬过来充当摘要
- 禁止流水账式罗列每一个细节
- 禁止因为本章"看起来平淡"就跳过或极度简化——即使本章只是铺垫，也必须记录人物动向和情节推进
- 严禁凭空编造任何内容。只记录原文中实际存在的情节、人物和设定，不得添加原文中不存在的任何元素

【强制格式】你只能且必须输出以下两个部分，顺序固定：

## 剧情摘要
（此处必须是连续叙述段落，不是列表。你必须100%用自己的语言，基于上面的六步梳理，完整复述本章发生的所有事情。标准：
- 不要漏掉任何一个情节转折、任何一段重要对话、任何一个角色行为、任何一个场景变化
- 去掉的只有：重复的环境描写、无意义的心理渲染堆砌、流水账式动作重复（如"他又挥了一拳"这类无信息增量的描写）
- 保留所有对理解剧情有用的细节：人物动机、对话核心内容、冲突起因与结果、伏笔暗示、势力关系变化
- 绝对不能复制原文的任何句子，必须逐句改写、转述，用自己的词汇和句式重新表达
- 长度标准：如果本章内容丰富，摘要应该在 1000-5000 字之间；内容少的章也不应低于 100 字
- 绝对不允许因为"本章看起来不重要"而一笔带过或跳过）

## 资料记录
- 出场人物：列出本章出现的所有人名及其身份/实力/关系变化
- 重要事件：时间、地点、经过、结果
- 新设定：世界规则、势力、物品等首次出现的设定
- 具体数值：等级、数量、时间等精确数字

【再次强调】除上述两个 ## 标题及其内容外，不要输出任何其他文字。"""
    return call_ai_stream(settings, [
        {'role': 'system', 'content': '你是格式严格的资料整理员。你的输出必须且只能包含两个部分：## 剧情摘要（用你自己的语言完整复述本章所有情节，禁止遗漏任何有用细节，禁止复制原文）和 ## 资料记录（markdown列表形式的人物、事件、设定、数值）。在处理每一章时，你必须先在脑中完成六步梳理（角色、事件、对话、场景、设定、关联），确认无遗漏后再输出。如果章节正文只有标题/占位符而无叙事内容，直接输出跳过标记：[本章无实质正文，跳过]。禁止开场白、结束语、评价、推测。禁止输出规定格式以外的任何内容。严禁凭空编造原文中不存在的情节、人物、设定或数值。'},
        {'role': 'user', 'content': prompt}
    ], mx, tmp, timeout=300, on_token=on_token, should_stop_fn=should_stop_fn)

def _estimate_tokens(text):
    if not text:
        return 0
    return len(text)

def _estimate_messages_tokens(messages):
    t = 0
    for m in messages:
        t += _estimate_tokens(m.get('content', ''))
        t += 4
    return t

def _compress_messages_for_context(messages, max_ctx_tokens, settings=None):
    if not messages or max_ctx_tokens <= 0:
        return messages
    reserve_ratio = 0.6
    budget = int(max_ctx_tokens * reserve_ratio)
    if _estimate_messages_tokens(messages) <= budget:
        return messages
    result = []
    sys_msg = None
    hist = []
    for m in messages:
        if m.get('role') == 'system':
            sys_msg = m
        else:
            hist.append(m)
    if sys_msg:
        result.append(sys_msg)
    if not hist:
        return result
    keep_recent = 6
    if len(hist) <= keep_recent + 2:
        recent = hist
        old = []
    else:
        recent = hist[-keep_recent:]
        old = hist[:-keep_recent]
    def _truncate_text(text, max_chars):
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + '\n…(内容已截断)'
    def _summarize_chunk_ai(chunk_msgs, cfg_settings):
        parts = []
        for m in chunk_msgs:
            role_label = '用户' if m.get('role') == 'user' else 'Luca'
            content = m.get('content', '')
            if len(content) > 500:
                content = content[:500] + '...'
            parts.append(f'{role_label}: {content}')
        body = '\n'.join(parts)
        if not cfg_settings or not cfg_settings.get('base_url') or not cfg_settings.get('model'):
            return f'[此前对话已压缩]\n{body[:800]}'
        prompt = f"""将以下对话记录压缩为简洁摘要。保留所有关键信息：用户的问题、Luca的核心回答、重要决定、设定讨论、伏笔提及。删除寒暄和重复。

对话记录：
{body}

输出压缩摘要（200字以内）："""
        try:
            summary, err = call_ai(cfg_settings, [
                {'role': 'system', 'content': '你是对话压缩专家。保留关键信息，删除冗余。'},
                {'role': 'user', 'content': prompt}
            ], 512, 0.2, timeout=30)
            if err or not summary or not summary.strip():
                return f'[此前对话已压缩]\n{body[:800]}'
            return f'[此前对话已压缩，摘要如下]\n{summary.strip()}'
        except Exception:
            return f'[此前对话已压缩]\n{body[:800]}'
    def _summarize_chunk(chunk_msgs):
        parts = []
        for m in chunk_msgs:
            role_label = '用户' if m.get('role') == 'user' else 'Luca'
            content = m.get('content', '')
            if len(content) > 200:
                content = content[:200] + '...'
            parts.append(f'{role_label}: {content}')
        body = '\n'.join(parts)
        return f'[此前对话已压缩，摘要如下]\n{body}'
    old_budget = budget - _estimate_messages_tokens(result) - _estimate_messages_tokens(recent)
    if old and old_budget > 100:
        chunk_size = 4
        compressed_old = []
        i = 0
        while i < len(old):
            chunk = old[i:i + chunk_size]
            chunk_tokens = _estimate_messages_tokens(chunk)
            if chunk_tokens <= old_budget:
                compressed_old.extend(chunk)
                old_budget -= chunk_tokens
            else:
                sm = _summarize_chunk_ai(chunk, settings) if settings else _summarize_chunk(chunk)
                compressed_old.append({'role': 'system', 'content': sm})
            i += chunk_size
        result.extend(compressed_old)
    else:
        if old:
            sm = _summarize_chunk_ai(old, settings) if settings else _summarize_chunk(old)
            result.append({'role': 'system', 'content': sm})
    current_budget = budget - _estimate_messages_tokens(result)
    for m in list(recent):
        mtokens = _estimate_tokens(m.get('content', ''))
        if mtokens > current_budget:
            m = dict(m)
            m['content'] = _truncate_text(m.get('content', ''), max(200, current_budget))
        current_budget -= _estimate_tokens(m.get('content', ''))
        result.append(m)
    return result


_compress_timers = {}
_compress_lock = threading.Lock()

def _schedule_idle_compress(entity_id):
    """对话结束后 30 秒检查并压缩上下文。"""
    with _compress_lock:
        if entity_id in _compress_timers:
            _compress_timers[entity_id].cancel()
        t = threading.Timer(30.0, _do_idle_compress, args=[entity_id])
        t.daemon = True
        t.start()
        _compress_timers[entity_id] = t

def _do_idle_compress(entity_id):
    """闲时压缩：如果对话历史超过阈值，用 AI 压缩旧消息。"""
    with _compress_lock:
        _compress_timers.pop(entity_id, None)
    try:
        settings = get_settings()
        ctx_limit = _get_effective_context_length(settings)
        if ctx_limit <= 0:
            return
        messages = _load_chat_history(entity_id)
        if not messages:
            return
        hist_msgs = []
        for m in messages:
            if m.get('type') == 'user':
                hist_msgs.append({'role': 'user', 'content': m.get('text', '')})
            elif m.get('type') == 'ai' and not m.get('_pending') and m.get('text'):
                hist_msgs.append({'role': 'assistant', 'content': m.get('text', '')})
        if not hist_msgs:
            return
        threshold = int(ctx_limit * 0.6)
        if _estimate_messages_tokens(hist_msgs) <= threshold:
            return
        log_action('IDLE_COMPRESS', f'entity={entity_id} tokens={_estimate_messages_tokens(hist_msgs)} limit={ctx_limit}')
        compressed = _compress_messages_for_context(hist_msgs, ctx_limit, settings)
        summary_parts = []
        for m in compressed:
            if m.get('role') == 'system' and '此前对话已压缩' in m.get('content', ''):
                summary_parts.append(m['content'])
        if summary_parts:
            existing = _load_chat_history(entity_id)
            summary_text = '\n\n'.join(summary_parts)
            has_summary = False
            for i, em in enumerate(existing):
                if em.get('type') == 'system' and em.get('subtype') == 'compressed_summary':
                    existing[i] = {'type': 'system', 'subtype': 'compressed_summary', 'text': summary_text}
                    has_summary = True
                    break
            if not has_summary:
                existing.insert(0, {'type': 'system', 'subtype': 'compressed_summary', 'text': summary_text})
            cut_start = 0
            for i, em in enumerate(existing):
                if em.get('type') == 'user':
                    cut_start = i
                    break
            keep_from = max(cut_start, len(existing) - 12)
            new_messages = existing[:1] + existing[keep_from:] if has_summary or cut_start > 0 else existing
            if len(new_messages) < len(existing):
                _save_chat_history(entity_id, new_messages)
                log_action('IDLE_COMPRESS_DONE', f'entity={entity_id} before={len(existing)} after={len(new_messages)}')
    except Exception as e:
        log_action('IDLE_COMPRESS_ERROR', f'{entity_id}: {str(e)[:160]}')


class _SimpleEmbedding(EmbeddingFunction):
    def __call__(self, input: Documents) -> Embeddings:
        embs = []
        for t in input:
            if not t or not t.strip():
                embs.append([0.0] * 64)
                continue
            vec = [0.0] * 64
            chars = list(t.lower())
            n = len(chars)
            if n == 0:
                embs.append(vec); continue
            for i, c in enumerate(chars):
                idx = ord(c) % 64
                weight = 1.0 - (i / (n + 100))
                vec[idx] += weight * ord(c)
            norm = sum(v*v for v in vec)**0.5 or 1.0
            embs.append([v/norm for v in vec])
        return embs

_chroma_clients = {}
_chroma_collections = {}
_chroma_clients_lock = threading.Lock()
_chroma_collections_lock = threading.Lock()

def _get_chroma_client(book_id):
    client = _chroma_clients.get(book_id)
    if client is not None:
        return client
    with _chroma_clients_lock:
        client = _chroma_clients.get(book_id)
        if client is None:
            db_dir = os.path.join(get_book_dir(book_id), '.vector_db')
            os.makedirs(db_dir, exist_ok=True)
            client = chromadb.PersistentClient(path=db_dir, settings=_CHROMA_SETTINGS)
            _chroma_clients[book_id] = client
        return client

def _get_kb_collection(book_id):
    col = _chroma_collections.get(book_id)
    if col is not None:
        return col
    client = _get_chroma_client(book_id)
    with _chroma_collections_lock:
        col = _chroma_collections.get(book_id)
        if col is None:
            col = client.get_or_create_collection(
                name='knowledge_base',
                embedding_function=_SimpleEmbedding(),
                metadata={'hnsw:space': 'cosine'}
            )
            _chroma_collections[book_id] = col
        return col

def _kb_clear(book_id):
    try:
        col = _get_kb_collection(book_id)
        col.delete(where={'$and': []})
    except Exception as e:
        log_action('KB_CLEAR_ERR', str(e)[:100])

def _kb_upsert(book_id, chunks, metadatas=None, ids=None):
    if not chunks:
        return
    col = _get_kb_collection(book_id)
    safe_ids = ids or [f'kb_{i}_{int(time.time()*1000)}' for i in range(len(chunks))]
    safe_metas = []
    for i, m in enumerate((metadatas or []) or [{}]*len(chunks)):
        sm = {'entity': str(m.get('entity','')), 'chapter': str(m.get('chapter','')),
              'section': str(m.get('section',''))}
        safe_metas.append(sm)
    try:
        col.upsert(documents=chunks, metadatas=safe_metas, ids=safe_ids[:len(chunks)])
    except Exception as e:
        log_action('KB_UPSERT_ERR', str(e)[:100])

def _kb_search(book_id, query_text, top_k=8, where_filter=None):
    try:
        col = _get_kb_collection(book_id)
        kwargs = {'query_texts': [query_text], 'n_results': min(top_k, 50)}
        if where_filter:
            kwargs['where'] = where_filter
        results = col.query(**kwargs)
        return results
    except Exception as e:
        log_action('KB_SEARCH_ERR', str(e)[:100])
        return None

_SOURCE_DIR_NAME = 'source'
_ENTITY_DIR = 'entities'

def _get_source_dir(book_id):
    d = os.path.join(get_book_dir(book_id), _SOURCE_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    ed = os.path.join(d, _ENTITY_DIR)
    os.makedirs(ed, exist_ok=True)
    return d

def _get_entity_path(book_id, entity_name):
    safe = re.sub(r'[\\/:*?"<>|]', '_', entity_name.strip())
    return os.path.join(_get_source_dir(book_id), _ENTITY_DIR, f'{safe}.md')

def _list_entity_files(book_id):
    ed = os.path.join(_get_source_dir(book_id), _ENTITY_DIR)
    return sorted(glob.glob(os.path.join(ed, '*.md')))

def _read_entity_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return ''

def _write_entity_file(path, content):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def _append_entity_content(book_id, entity_name, section_title, content):
    path = _get_entity_path(book_id, entity_name)
    existing = _read_entity_file(path)
    marker = f'### {section_title}'
    if marker in existing and content.strip():
        idx = existing.find(marker)
        end_idx = existing.find('\n### ', idx + len(marker))
        if end_idx == -1:
            end_idx = len(existing)
        existing = existing[:idx] + f'{marker}\n{content}\n' + existing[end_idx:]
    elif content.strip():
        existing += f'\n\n### {section_title}\n{content}\n'
    _write_entity_file(path, existing)
    return path

def _get_all_entities_text(book_id):
    parts = []
    for fp in _list_entity_files(book_id):
        text = _read_entity_file(fp)
        if text.strip():
            parts.append(text)
    return '\n\n---\n\n'.join(parts)

def _rebuild_vector_index(book_id):
    _kb_clear(book_id)
    chunks = []
    metas = []
    ids = []
    chunk_counter = [0]
    def _add_chunks(entity_name, text, chapter='', section=''):
        if not text or not text.strip():
            return
        paras = re.split(r'\n(?=### )', text)
        for p in paras:
            p = p.strip()
            if len(p) < 10:
                continue
            cid = f'{entity_name}_{chunk_counter[0]}'
            chunks.append(p)
            metas.append({'entity': entity_name, 'chapter': chapter, 'section': section})
            ids.append(cid)
            chunk_counter[0] += 1
    for fp in _list_entity_files(book_id):
        ename = os.path.splitext(os.path.basename(fp))[0]
        etext = _read_entity_file(fp)
        _add_chunks(ename, etext, section='entity')
    rules_path = os.path.join(_get_source_dir(book_id), 'rules.md')
    if os.path.exists(rules_path):
        _add_chunks('__rules__', _read_entity_file(rules_path), section='rules')
    tl_path = os.path.join(_get_source_dir(book_id), 'timeline.md')
    if os.path.exists(tl_path):
        _add_chunks('__timeline__', _read_entity_file(tl_path), section='timeline')
    fs_path = os.path.join(_get_source_dir(book_id), 'foreshadowing.md')
    if os.path.exists(fs_path):
        _add_chunks('__foreshadowing__', _read_entity_file(fs_path), section='foreshadowing')
    if chunks:
        _kb_upsert(book_id, chunks, metas, ids)
    log_action('KB_REBUILT', f'chunks={len(chunks)}, entities={len(_list_entity_files(book_id))}')

def _parse_entities_from_notes(notes_text):
    entities = {}
    current_entity = None
    current_section = None
    buf = ''
    for line in notes_text.split('\n'):
        stripped = line.strip()
        m = re.match(r'^#{1,3}\s+(.+)$', stripped)
        if m:
            heading = m.group(1).strip()
            if current_entity and buf.strip():
                key = current_entity
                if key not in entities:
                    entities[key] = []
                entities[key].append(('## ' + (current_section or '信息'), buf.strip()))
            em = re.match(r'^[\s]*[-*]\s*\*\*(.+?)\*\*', stripped)
            if em:
                if current_entity and buf.strip():
                    if current_entity not in entities:
                        entities[current_entity] = []
                    entities[current_entity].append(('## ' + (current_section or '信息'), buf.strip()))
                current_entity = em.group(1).strip()
                current_section = None
                buf = ''
                continue
            else:
                current_entity = None
            sm = re.match(r'^(?:##\s+)?(.+)', heading)
            if sm:
                current_section = sm.group(1).strip()
            buf = ''
        else:
            buf += line + '\n'
    if current_entity and buf.strip():
        if current_entity not in entities:
            entities[current_entity] = []
        entities[current_entity].append(('## ' + (current_section or '信息'), buf.strip()))
    return entities

def _save_parsed_entities_to_files(book_id, entities):
    count = 0
    for ename, sections in entities.items():
        if not ename:
            continue
        path = _get_entity_path(book_id, ename)
        existing = _read_entity_file(path)
        for section_title, content in sections:
            if not content or not content.strip():
                continue
            marker = f'### {section_title}'
            if marker in existing:
                continue
            existing += f'\n\n{marker}\n{content}\n'
            count += 1
        if existing != _read_entity_file(path):
            _write_entity_file(path, existing)
    return count


def _extract_timeline_from_notes(notes_text):
    lines = notes_text.split('\n')
    timeline_parts = []
    capture = False
    buf = ''
    for line in lines:
        s = line.strip()
        if re.match(r'^#{1,3}\s.*(事件|时间线|编年|时间)', s):
            capture = True
            buf = ''
            continue
        if capture:
            if s.startswith('#') and not s.startswith('###'):
                break
            if re.match(r'^#{1,3}\s+', s) and '事件' not in s and '时间' not in s and '编年' not in s:
                if buf.strip():
                    timeline_parts.append(buf.strip())
                buf = ''
                capture = False
                continue
            buf += line + '\n'
    if buf.strip():
        timeline_parts.append(buf.strip())
    return '\n\n'.join(timeline_parts)


def _extract_foreshadowing_from_notes(notes_text):
    lines = notes_text.split('\n')
    parts = []
    capture = False
    buf = ''
    for line in lines:
        s = line.strip()
        if re.match(r'^#{1,3}\s.*(伏笔|悬念|线索|未解)', s):
            capture = True
            buf = ''
            continue
        if capture:
            if s.startswith('#') and not s.startswith('###'):
                break
            if re.match(r'^#{1,3}\s+', s) and '伏笔' not in s and '悬念' not in s and '线索' not in s and '未解' not in s:
                if buf.strip():
                    parts.append(buf.strip())
                buf = ''
                capture = False
                continue
            buf += line + '\n'
    if buf.strip():
        parts.append(buf.strip())
    return '\n\n'.join(parts)

def _ai_extract_entities(settings, notes_text, config=None):
    cfg = config or {}
    mx = cfg.get('max_tokens', 4096)
    tmp = cfg.get('temperature', 0.2)
    prompt = f"""从以下小说阅读笔记中提取所有实体（人物、地点、物品、组织、国家等），并按实体归类整理。

原始笔记：
{notes_text[:15000]}

【输出格式】（严格 JSON 数组，每个元素一个实体）
[
  {{"name": "李云", "type": "人物", "summary": "主角，第1章登场，获得断岳刀..."}},
  {{"name": "北境国", "type": "国家", "summary": "国土面积约200万平方千米..."}},
  {{"name": "断岳刀", "type": "物品", "summary": "李云的武器，第50章获得，第180章碎裂..."}}
]

规则：
- 只提取有名字、有具体信息的实体（不要"路人甲"这种）
- summary 要包含关键信息和章节引用
- 类型可以是：人物、地点、物品、组织、国家、势力、概念
- 只输出 JSON，不要其他内容"""
    msgs = [
        {'role': 'system', 'content': '你是信息抽取专家。只输出JSON数组。'},
        {'role': 'user', 'content': prompt}
    ]
    raw, err = call_ai(settings, msgs, mx, tmp, timeout=120)
    if err or not raw:
        return [], err
    try:
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            if isinstance(data, list):
                return data, None
    except:
        pass
    return [], f'解析失败: {raw[:200]}'

def _build_source_summary(book_id, max_chars=8000):
    parts = ['# 全书阅读笔记摘要\n']
    efiles = _list_entity_files(book_id)
    if efiles:
        parts.append('## 实体索引\n')
        for fp in efiles[:30]:
            ename = os.path.splitext(os.path.basename(fp))[0]
            etext = _read_entity_file(fp)
            first_para = etext.split('\n### ')[0][:200] if etext else ''
            parts.append(f'- **{ename}**: {first_para}')
        if len(efiles) > 30:
            parts.append(f'- ... 共 {len(efiles)} 个实体')
    tl = os.path.join(_get_source_dir(book_id), 'timeline.md')
    if os.path.exists(tl):
        tlt = _read_entity_file(tl)
        if tlt.strip():
            parts.append(f'\n## 时间线\n{tlt[:3000]}')
    fs = os.path.join(_get_source_dir(book_id), 'foreshadowing.md')
    if os.path.exists(fs):
        fst = _read_entity_file(fs)
        if fst.strip():
            parts.append(f'\n## 伏笔追踪\n{fst[:2000]}')
    result = '\n'.join(parts)
    if len(result) > max_chars:
        result = result[:max_chars-3] + '\n...'
    return result

def get_smart_context(book_id, user_query='', budget_chars=None, settings=None):
    """组装上下文：使用新版 kb_pipeline.qa_context"""
    if settings is None:
        settings = get_settings()
    text, _ = kb_pipeline.qa_context(book_id, user_query=user_query, settings=settings)
    if not text or len(text.strip()) < 20:
        return '（目前还没有阅读笔记，请先生成全书摘要）'
    if budget_chars and len(text) > budget_chars:
        text = text[:budget_chars]
    return text

def _get_effective_context_length(settings=None):
    if settings is None:
        settings = get_settings()
    ctx_len = settings.get('model_context_length', 0) or 0
    presets = settings.get('provider_presets', [])
    idx = settings.get('active_provider_idx', 0)
    if not ctx_len and 0 <= idx < len(presets):
        ctx_len = presets[idx].get('context_length', 0) or 0
    return ctx_len

def _ai_compress_source_for_context(source_text, target_chars, settings, config=None):
    if not source_text or len(source_text) <= target_chars:
        return source_text
    cfg = config or {}
    mx = cfg.get('max_tokens', 4096)
    tmp = cfg.get('temperature', 0.2)
    ratio = target_chars / len(source_text)
    prompt = f"""将以下小说阅读笔记压缩到约 {ratio*100:.0f}% 的长度。
保留所有：人物名及其核心信息、关键事件、伏笔、数值变化、世界观设定。
删除：冗余描述、重复内容、过度细节描写。
保持 markdown 结构不变。

原文：
{source_text[:20000]}

输出压缩后的完整 markdown（不要省略号，要完整可用的文本）。"""
    msgs = [
        {'role': 'system', 'content': '你是资料压缩专家。压缩时保留所有关键信息，删除冗余。输出完整markdown。'},
        {'role': 'user', 'content': prompt}
    ]
    result, err = call_ai(settings, msgs, mx, tmp, timeout=120)
    if err or not result or not result.strip():
        return source_text[:target_chars] + '\n...(自动截断)'
    return result

def get_context_estimate(book_id, settings=None):
    if settings is None:
        settings = get_settings()
    ctx_len = int(_get_effective_context_length(settings) or 0)
    bd = get_book_dir(book_id)
    meta = load_json(os.path.join(bd, 'meta.json'), dict) if os.path.isdir(bd) else {}
    chapter_dir = os.path.join(bd, 'chapters')
    chapter_count = 0
    max_chapter_tokens = 0
    current_chapter_tokens = 0
    current_cid = meta.get('current_chapter_id', '')
    if os.path.isdir(chapter_dir):
        for fn in os.listdir(chapter_dir):
            if not fn.endswith('.json'):
                continue
            try:
                ch = load_json(os.path.join(chapter_dir, fn), dict)
                content = ch.get('content', '') or ''
                tokens = _estimate_tokens(content)
                chapter_count += 1
                max_chapter_tokens = max(max_chapter_tokens, tokens)
                if fn[:-5] == current_cid:
                    current_chapter_tokens = tokens
            except Exception:
                continue
    source_text = get_source(book_id) or ''
    raw_source_tokens = _estimate_tokens(source_text)
    try:
        smart_ctx = get_smart_context(book_id, user_query='', settings=settings)
    except Exception:
        smart_ctx = ''
    smart_tokens = _estimate_tokens(smart_ctx) if smart_ctx else 0
    book_context_tokens = smart_tokens if smart_tokens > 0 else raw_source_tokens
    entity_count = len(_list_entity_files(book_id))
    system_prompt_tokens = 2200
    tool_prompt_tokens = 1400
    chapter_list_tokens = min(2500, max(200, chapter_count * 45))
    recent_history_reserve = 2400
    input_tokens = (
        system_prompt_tokens + tool_prompt_tokens + book_context_tokens +
        max_chapter_tokens + chapter_list_tokens + recent_history_reserve
    )
    output_reserve_tokens = max(4096, min(16384, int(input_tokens * 0.25)))
    safety_margin_tokens = max(1200, int(input_tokens * 0.08))
    min_required = input_tokens + output_reserve_tokens + safety_margin_tokens
    return {
        'model_context': ctx_len,
        'context_tokens': book_context_tokens,
        'chapter_tokens': max_chapter_tokens,
        'current_chapter_tokens': current_chapter_tokens,
        'history_tokens': recent_history_reserve,
        'system_prompt_tokens': system_prompt_tokens + tool_prompt_tokens,
        'chapter_list_tokens': chapter_list_tokens,
        'output_reserve_tokens': output_reserve_tokens,
        'safety_margin_tokens': safety_margin_tokens,
        'min_chat_required': min_required,
        'min_context_required': min_required,
        'total_estimated': min_required,
        'needs_compression': ctx_len > 0 and min_required > ctx_len,
        'entity_count': entity_count,
        'chapter_count': chapter_count,
    }

def _ai_read_chapters_batch(settings, chapters, prev_context, config=None, on_token=None, should_stop_fn=None):
    """批量阅读多章，一次性返回所有章节的摘要。"""
    cfg = config or {}
    mx = cfg.get('max_tokens', None)
    tmp = cfg.get('temperature', 0.5)
    ctx = f'【前情索引（只供参考，不要输出）】\n{prev_context}\n\n' if prev_context else ''

    parts = []
    for ch in chapters:
        parts.append(f'=== 本章：{ch["title"]} ===\n\n{ch["content"]}')
    chapters_text = '\n\n'.join(parts)

    prompt = f"""{ctx}以下是连续的多章小说内容，请分别为**每一章**生成独立的剧情摘要和资料记录。

{chapters_text}

【任务】以资料整理员身份，用中文客观记录每一章内容。

【重要：跳过规则】在开始梳理之前，请先逐一判断每一章的正文是否有实质性叙事内容：
- 如果某章正文只有章节标题、占位符、软件说明、元数据标记等非叙事文本（如正文仅为"#第一章"而无后续段落），则该章无内容，请直接输出以下跳过格式（不要做任何分析）：
  ## {{章节标题}} 剧情摘要
  [本章无实质正文，跳过]
  ## {{章节标题}} 资料记录
  [无]
- 只有确认正文中存在至少一段连续的叙事内容时，才为该章执行六步梳理并输出正式摘要。

【强制思考步骤】在输出任何内容之前，你必须先在脑中为每一章分别完成以下梳理（不要输出这些思考过程）：
1. 角色梳理：本章有哪些角色出场？每个人分别做了什么、说了什么、处于什么状态？
2. 事件梳理：本章发生了哪些关键事件？起因、经过、结果分别是什么？有没有冲突、转折或意外？
3. 对话梳理：有没有重要的对话？核心信息是什么？
4. 场景梳理：场景有没有切换？时间有没有推进？地点有没有变化？
5. 设定梳理：有没有新世界规则、新势力、新物品、新能力首次出现？
6. 关联梳理：本章事件对主线有什么推动？有没有埋下伏笔或解开悬念？

完成以上六步梳理后，确认没有遗漏任何对剧情有用的细节，再输出正式的剧情摘要和资料记录。

【绝对禁止】
- 禁止输出"好的""明白了""以下是"等任何开场白或结束语
- 禁止评价、推测、感想、总结性评论
- 禁止复制原文的任何句子、段落或片段，必须100%用自己的语言重新叙述
- 禁止把不同章节的内容混为一谈，每一章的输出必须完全独立
- 禁止把剧情摘要写成 bullet list，必须是连贯叙述段落
- 严禁凭空编造任何内容。只记录原文中实际存在的情节、人物和设定，不得添加原文中不存在的任何元素

【强制格式】对每一章，你只能且必须输出以下两个部分，顺序固定。章节与章节之间用一行空行分隔：

## {{{{章节标题}}}} 剧情摘要
（此处必须是连续叙述段落，不是列表。用自己的语言完整复述本章所有事情。标准：
- 不要漏掉任何一个情节转折、任何一段重要对话、任何一个角色行为、任何一个场景变化
- 去掉的只有：重复的环境描写、无意义的心理渲染堆砌
- 保留所有对理解剧情有用的细节：人物动机、对话核心内容、冲突起因与结果、伏笔暗示、势力关系变化
- 绝对不能复制原文的任何句子，必须逐句改写、转述
- 长度标准：内容丰富的章应在 1000-5000 字之间；内容少的章也不应低于 100 字
- 绝对不允许因为"本章看起来不重要"而一笔带过）

## {{{{章节标题}}}} 资料记录
- 出场人物：列出本章出现的所有人名及其身份/实力/关系变化
- 重要事件：时间、地点、经过、结果
- 新设定：世界规则、势力、物品等首次出现的设定
- 具体数值：等级、数量、时间等精确数字

【再次强调】必须严格按照上述格式输出，每一章都必须有对应的输出（有内容的输出摘要，无内容的输出跳过标记）。除规定的标题及其内容外，不要输出任何其他文字。"""

    return call_ai_stream(settings, [
        {'role': 'system', 'content': '你是格式严格的资料整理员。你的输出必须且只能包含每一章的两个部分：## {章节标题} 剧情摘要（用自己的语言完整复述，禁止遗漏细节，禁止复制原文）和 ## {章节标题} 资料记录（markdown列表形式的人物、事件、设定、数值）。你必须为每一章分别独立输出，章节之间用空行分隔。在处理每一章时，你必须先在脑中完成六步梳理（角色、事件、对话、场景、设定、关联），确认无遗漏后再输出。如果某章正文只有标题/占位符而无叙事内容，直接输出跳过标记：[本章无实质正文，跳过]。禁止开场白、结束语、评价、推测。禁止输出规定格式以外的任何内容。严禁凭空编造原文中不存在的情节、人物、设定或数值。'},
        {'role': 'user', 'content': prompt}
    ], mx, tmp, timeout=300, on_token=on_token, should_stop_fn=should_stop_fn)

def _parse_batch_result(result, chapters):
    """将批量 AI 输出按章节拆分。成功返回列表，失败返回 None。"""
    if not result or not result.strip():
        return None

    marks = []
    for i, ch in enumerate(chapters):
        title = re.escape(ch['title'])
        # 匹配行首的 "## title 剧情摘要"（允许空格变化）
        pat = re.compile(rf'^## \s*{title}\s*剧情摘要', re.MULTILINE)
        for m in pat.finditer(result):
            marks.append((m.start(), i))

    if len(marks) < len(chapters):
        return None

    marks.sort()
    out = [''] * len(chapters)
    for j, (start, ch_i) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else len(result)
        out[ch_i] = result[start:end].strip()
    return out

def _ai_merge_notes_stream(settings, notes, config=None, on_token=None, should_stop_fn=None):
    cfg = config or {}
    mx = cfg.get('max_tokens', None)
    tmp = cfg.get('temperature', 0.2)
    combined = '\n\n'.join(notes)
    prompt = f"""将以下各章记录合并整理为一份结构化的全书阅读笔记。

{combined}

【绝对禁止】
- 禁止输出任何开场白或结束语
- 禁止评价、推测、感想
- 禁止合并不同章节中对同一事物的不同角度描述（保留所有细节）

【强制输出格式】
# 全书阅读笔记
## 一、人物档案
（按角色分类，集中该角色在各章的所有信息：身份、实力变化、关系演变、关键行为）
## 二、事件编年
（按时间线排列所有重要事件，保留起因、经过、结果）
## 三、世界观与设定
（整理所有世界规则、势力、物品设定，保留首次出现的章节标记）
## 四、数值记录
（汇总所有具体数字：等级、数量、时间、距离等）
## 五、伏笔与线索
（记录所有未解决的悬念、预言、暗示及其出现章节）

【再次强调】除上述 markdown 结构外，不要输出任何其他文字。同一角色的不同信息全部保留，不要概括删减。"""
    return call_ai_stream(settings, [
        {'role': 'system', 'content': '你是格式严格的资料整理员。合并各章记录时，必须保留所有细节，禁止概括删减。只输出规定 markdown 结构，禁止任何额外文字。'},
        {'role': 'user', 'content': prompt}
    ], mx, tmp, timeout=180, on_token=on_token, should_stop_fn=should_stop_fn)

def _ai_outline_stream(settings, source, config=None, on_token=None, should_stop_fn=None):
    cfg = config or {}
    mx = cfg.get('max_tokens', None)
    tmp = cfg.get('temperature', 0.15)
    prompt = f"""从以下全书笔记提炼结构大纲：

{source}

输出 markdown：
# 故事大纲
## 主线
## 分卷/分篇章概要
## 主要角色状态
## 关键转折点

精炼简洁，只输出 markdown。"""
    return call_ai_stream(settings, [
        {'role': 'system', 'content': '你是大纲整理员，从全书笔记提炼大纲。只输出markdown。'},
        {'role': 'user', 'content': prompt}
    ], mx, tmp, timeout=120, on_token=on_token, should_stop_fn=should_stop_fn)

def _ai_cleanup_source(settings, source_text, config=None, on_token=None, should_stop_fn=None):
    """审视全书笔记，删除明显完全重复的内容，尽可能保留。"""
    cfg = config or {}
    mx = cfg.get('max_tokens', None)
    tmp = cfg.get('temperature', 0.1)
    prompt = f"""以下是全书各章的阅读笔记。你的任务是：

1. 通读全文，删除**明显完全重复**的内容（即不同章节中对同一事物的描述几乎逐字相同）。
2. **尽可能保留所有信息**——如果两段内容角度不同、细节不同、或涉及不同章节/时间点，即使主题相同也要保留。
3. 保留所有具体数值、人名、地点、事件经过。
4. 保留所有不同章节的独立记录。
5. 输出完整的 markdown，不要省略任何非重复内容。

注意：只删"逐字重复"或"同一句话换了个说法但信息完全相同"的内容。不要合并、不要概括、不要删减细节。

---

{source_text}

---

请输出清理后的完整 markdown："""
    return call_ai_stream(settings, [
        {'role': 'system', 'content': '你是去重整理员。只删除明显完全重复的内容，其他一律保留。输出完整markdown。'},
        {'role': 'user', 'content': prompt}
    ], mx, tmp, timeout=300, on_token=on_token, should_stop_fn=should_stop_fn)

def _ai_merge_notes(settings, notes, config=None):
    cfg = config or {}
    mx = cfg.get('max_tokens', None)
    tmp = cfg.get('temperature', 0.2)
    combined = '\n\n'.join(notes)
    prompt = f"""将全书各章记录整理为完整阅读笔记：

{combined}

输出 markdown 结构：
# 全书阅读笔记
## 一、人物档案
## 二、事件编年
## 三、世界观与设定
## 四、数值记录
## 五、伏笔与线索

只输出 markdown，同一角色信息集中一处。"""
    return call_ai(settings, [
        {'role': 'system', 'content': '你是资料整理员，合并各章记录为结构化全书笔记。只输出markdown。'},
        {'role': 'user', 'content': prompt}
    ], mx, tmp, timeout=180)

def _ai_outline(settings, source, config=None):
    cfg = config or {}
    mx = cfg.get('max_tokens', None)
    tmp = cfg.get('temperature', 0.3)
    prompt = f"""基于以下全书阅读笔记，整理一份结构清晰、内容完整的故事大纲。

{source}

【要求】
1. 用自己的语言重新组织，不要复制原文句子
2. 必须包含所有主要情节线，不要遗漏任何重要事件
3. 角色发展要有连贯性，体现变化和成长
4. 标注伏笔和悬念的位置
5. 输出 markdown 格式

【输出格式】
# 故事大纲

## 一、主线剧情
（按时间顺序梳理核心故事线，包含所有关键转折）

## 二、支线脉络
（各条支线的发展，与主线的交汇点）

## 三、角色弧光
（主要角色的出场、动机变化、关键抉择、成长轨迹）

## 四、势力格局
（各方势力的消长、联盟、对抗关系演变）

## 五、世界观展开
（设定揭示的顺序，规则的建立与打破）

## 六、伏笔与悬念
（已埋下的伏笔当前状态：未回收/已回收/待发展）

## 七、关键转折点
（对每个重要转折标注：章节位置、触发原因、影响范围）

只输出 markdown，不要加任何开场白或结束语。"""
    return call_ai(settings, [
        {'role': 'system', 'content': '你是专业的小说结构分析师。基于阅读笔记整理大纲时，必须用自己的语言重新叙述，保留所有有用细节，禁止复制原文。只输出 markdown。'},
        {'role': 'user', 'content': prompt}
    ], mx, tmp, timeout=180)


def _run_timeline_arrange_task(task_id, book_id, cfg_settings):
    try:
        bg_task_update(task_id, progress=10)
        result = kb_pipeline.arrange_timeline_ai(book_id, cfg_settings)
        bg_task_update(task_id, progress=100, result=json.dumps(result, ensure_ascii=False))
        bg_task_done(task_id)
    except Exception as e:
        bg_task_done(task_id, str(e))


_TIMELINE_EDIT_FIELDS = {
    'chapter_id', 'story_time', 'who', 'what', 'where_loc', 'why', 'consequence'
}


def _schedule_timeline_arrange(book_id, cfg_settings):
    existing = bg_task_get_by_book_type(book_id, 'timeline-arrange')
    if existing and existing.get('status') == 'running':
        return False
    tid_tl = bg_task_start('timeline-arrange', book_id, '时间线编排')
    threading.Thread(target=_run_timeline_arrange_task, args=(tid_tl, book_id, cfg_settings), daemon=True).start()
    return True


def _schedule_timeline_after_kb_edit(book_id, table_name, field):
    if table_name != 'events' or field not in _TIMELINE_EDIT_FIELDS:
        return False
    try:
        return _schedule_timeline_arrange(book_id, get_settings())
    except Exception as e:
        log_action('TIMELINE_EDIT_SCHEDULE_ERR', str(e)[:120])
        return False



def _run_prediction_update_task(task_id, book_id, cfg_settings):
    try:
        bg_task_update(task_id, progress=10)
        result, err = kb_pipeline.generate_short_prediction(book_id, cfg_settings)
        if err:
            bg_task_done(task_id, err)
            return
        bg_task_update(task_id, progress=100, result=result)
        bg_task_done(task_id)
    except Exception as e:
        bg_task_done(task_id, str(e))


def _do_kb_reread_task(task_id, book_id, chapter_ids, correction, focus_texts, cfg_settings):
    try:
        set_conn_meta('kb-reread', '局部重读', book_id)
        bg_task_update(task_id, progress=8, stream_buffer='正在准备局部重读...')
        result = kb_pipeline.reread_passages(book_id, chapter_ids, correction, focus_texts, cfg_settings)
        bg_task_update(task_id, progress=100, result=json.dumps(result, ensure_ascii=False))
        bg_task_done(task_id)
    except Exception as e:
        bg_task_done(task_id, str(e))


def _do_import_verify_task(task_id, book_id, cfg_settings):
    """读全部章节元数据，让 AI 判断这本书目录是否像被正确导入。
    AI 只看元数据（标题、字数、原始 NCX 标题、首行预览），不喂正文。
    输出：{broken_confidence, reasoning, suspicious_chapter_ids[]}。"""
    try:
        set_conn_meta('import-verify', '导入校验', book_id)
        bg_task_update(task_id, progress=5, stream_buffer='正在收集章节元数据...')
        bd = get_book_dir(book_id)
        ch_dir = os.path.join(bd, 'chapters')
        meta = get_book_meta(book_id) or {}
        order = meta.get('chapter_order', []) or []
        chapters_meta = []
        for i, cid in enumerate(order):
            cp = os.path.join(ch_dir, f"{cid}.json")
            if not os.path.exists(cp):
                continue
            try:
                with open(cp, 'r', encoding='utf-8') as f:
                    ch = json.load(f)
            except Exception:
                continue
            imp = ch.get('_import_meta') or {}
            content = ch.get('content', '') or ''
            # 用首两行做预览
            preview_lines = []
            for ln in content.split('\n'):
                s = ln.strip()
                if s:
                    preview_lines.append(s)
                if len(preview_lines) >= 2:
                    break
            chapters_meta.append({
                'id': cid,
                'idx': i,
                'title': ch.get('title', '')[:120],
                'char_count': len(content),
                'ncx_title': imp.get('ncx_title', '')[:120],
                'title_source': imp.get('title_source', ''),
                'first_line': (imp.get('first_line') or (' / '.join(preview_lines)))[:80],
            })
        if not chapters_meta:
            bg_task_update(task_id, progress=100,
                           result=json.dumps({'broken_confidence': 0.0, 'reasoning': '没有章节可校验', 'suspicious_chapter_ids': []}, ensure_ascii=False))
            bg_task_done(task_id); return

        total_chars = sum(c['char_count'] for c in chapters_meta)
        short_count = sum(1 for c in chapters_meta if c['char_count'] < 200)
        fallback_titled = sum(1 for c in chapters_meta if (c['title_source'] in ('filename', 'fallback_scan') or re.search(r'Section\d+|chapter\d+', c['title'] or '', re.I)))

        summary = {
            'book_title': meta.get('title', ''),
            'total_chapters': len(chapters_meta),
            'total_chars': total_chars,
            'avg_chars_per_chapter': total_chars // max(1, len(chapters_meta)),
            'short_chapters_count': short_count,
            'fallback_titled_count': fallback_titled,
        }
        # 给 AI 只看元数据
        ai_input = {'summary': summary, 'chapters': chapters_meta}

        prompt = f"""你是图书馆员。下面是一本刚从 EPUB/TXT/PDF 导入的电子书的目录元数据（不含正文）。判断这本书是否看起来"被正确导入"。

可能的"导入失败"信号：
- 大量章节标题是 SectionXXX / chapterN / 数字文件名这种 fallback 命名
- 字数分布两极分化（很多 50-200 字的"标题页/彩页"碎片混在正常正文章节之间）
- 混入"封面/转载信息/Table of Contents/Landmarks/版权页/制作信息"等元信息页
- 章节顺序明显错乱（同样模式编号但顺序跳跃）
- 标题模式不统一（有些"第N话"，有些"SectionXXX"，看起来是部分识别失败）

输入数据（JSON）：
{json.dumps(ai_input, ensure_ascii=False)}

只输出严格 JSON，不要代码块：
{{
  "broken_confidence": 0.0-1.0,
  "reasoning": "一两句话简短说明判断依据，中文",
  "suspicious_chapter_ids": ["你认为应该删除的章节 id 列表，可以为空"]
}}

注意：
- broken_confidence: 0=看起来完全正常的目录, 1=非常可能没正确导入
- suspicious_chapter_ids: 只列入你高置信度认为应该删除的章节（如明显的元信息页/扉页/碎片），不要列入你不确定的
- 你不能改章节名，不能合并，不能调序，只能建议删除"""

        bg_task_update(task_id, progress=30, stream_buffer='已收集 ' + str(len(chapters_meta)) + ' 章元数据，正在请求 AI...')
        try:
            raw, _, err = call_ai_full(cfg_settings, [
                {'role': 'system', 'content': '你是严谨的图书馆员。只输出严格 JSON，不要任何额外文字。'},
                {'role': 'user', 'content': prompt},
            ], 1200, 0.2, timeout=120)
        except Exception as e:
            bg_task_done(task_id, f'AI 调用异常: {str(e)[:200]}'); return
        if err:
            bg_task_done(task_id, f'AI 调用失败: {err[:200]}'); return

        bg_task_update(task_id, progress=85, stream_buffer='正在解析 AI 输出...')
        text = (raw or '').strip()
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            text = m.group(0)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            bg_task_done(task_id, f'AI 输出 JSON 解析失败: {text[:200]}'); return

        try:
            conf = float(parsed.get('broken_confidence', 0))
        except Exception:
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        reasoning = str(parsed.get('reasoning', ''))[:500]
        sus_ids = parsed.get('suspicious_chapter_ids') or []
        if not isinstance(sus_ids, list):
            sus_ids = []
        valid_ids = {c['id'] for c in chapters_meta}
        sus_ids = [s for s in sus_ids if isinstance(s, str) and s in valid_ids]

        out = {
            'broken_confidence': conf,
            'reasoning': reasoning,
            'suspicious_chapter_ids': sus_ids,
            'summary': summary,
        }
        bg_task_update(task_id, progress=100, result=json.dumps(out, ensure_ascii=False))
        bg_task_done(task_id)
    except Exception as e:
        bg_task_done(task_id, str(e)[:300])




def _schedule_kb_after_write_jobs(book_id, cfg_settings, include_prediction=True):
    try:
        _schedule_timeline_arrange(book_id, cfg_settings)
    except Exception as e:
        log_action('TIMELINE_ARRANGE_SCHEDULE_ERR', str(e)[:120])
    if include_prediction:
        try:
            existing = bg_task_get_by_book_type(book_id, 'prediction')
            if not existing or existing.get('status') != 'running':
                tid_pr = bg_task_start('prediction', book_id, '更新预言')
                threading.Thread(target=_run_prediction_update_task, args=(tid_pr, book_id, cfg_settings), daemon=True).start()
        except Exception as e:
            log_action('PREDICTION_SCHEDULE_ERR', str(e)[:120])


def _do_readthrough_wrapper(book_id, cfg_settings, config=None, resume=False):
    threading.current_thread().name = f'kb_readthrough_{book_id}'
    kb_pipeline.do_readthrough(book_id, cfg_settings, config=config, resume=resume)
    try:
        st = kb_storage.get_rt_state(book_id)
        if st and st.get('status') == 'done':
            _schedule_kb_after_write_jobs(book_id, cfg_settings, include_prediction=False)
    except Exception as e:
        log_action('RT_POST_JOBS_ERR', str(e)[:120])


def _do_chapter_complete_wrapper(task_id, book_id, chapter_id, cfg_settings, text=None):
    """包装器：调用新版 kb_pipeline.do_chapter_complete"""
    set_conn_meta('chapter-complete', '本章通读', book_id)
    log_action('CHAPTER_COMPLETE_START', f'book={book_id}, chapter={chapter_id}')
    try:
        bg_task_update(task_id, progress=10)
        summary = kb_pipeline.do_chapter_complete(book_id, chapter_id, cfg_settings, text=text)
        bg_task_update(task_id, progress=90, result=summary or '')
        try:
            outline = get_outline(book_id)
            ch = kb_storage.get_chapter(book_id, chapter_id)
            ch = dict(ch) if ch else None
            if ch and ch.get('summary'):
                summaries = outline.get('chapter_summaries', {})
                summaries[chapter_id] = ch['summary'][:500] + ('...' if len(ch['summary']) > 500 else '')
                outline['chapter_summaries'] = summaries
                outline['updated'] = time.time()
                save_json(os.path.join(get_book_dir(book_id), 'outline.json'), outline)
        except Exception as ex:
            log_action('CHAPTER_COMPLETE_OUTLINE_ERR', str(ex)[:100])
        bg_task_done(task_id)
        _schedule_kb_after_write_jobs(book_id, cfg_settings, include_prediction=True)
        log_action('CHAPTER_COMPLETE_DONE', f'book={book_id}, chapter={chapter_id}')
    except Exception as e:
        log_action('CHAPTER_COMPLETE_EXCEPTION', str(e))
        bg_task_done(task_id, str(e))


def do_readthrough(bid, settings, config=None, resume=False):
    """包装器：调用新版 kb_pipeline.do_readthrough（可能被旧代码引用）"""
    _do_readthrough_wrapper(bid, settings, config=config, resume=resume)


# 兼容别名：summary -> readthrough
def get_summary_config(bid):
    return get_readthrough_config(bid)

def save_summary_config(bid, cfg):
    return save_readthrough_config(bid, cfg)

def do_series_summary(sid, settings):
    return do_series_readthrough(sid, settings)

def do_summary(bid, settings, config=None, resume=False):
    return do_readthrough(bid, settings, config, resume)


def _migrate_old_books():
    """扫描旧书，迁移到新版 KB 数据库"""
    if not os.path.isdir(BOOKS_DIR):
        return
    for bid in os.listdir(BOOKS_DIR):
        bd = os.path.join(BOOKS_DIR, bid)
        if not os.path.isdir(bd) or bid.startswith('builtin_'):
            continue
        meta_path = os.path.join(bd, 'meta.json')
        meta = load_json(meta_path, dict) or {}
        if meta.get('kb_status') == 'ok':
            continue
        source_md = os.path.join(bd, 'source.md')
        has_old_data = os.path.exists(source_md) and os.path.getsize(source_md) > 500
        kb_db = os.path.join(bd, 'kb.db')
        has_new_db = os.path.exists(kb_db)
        if has_old_data and not has_new_db:
            today = datetime.now().strftime('%Y%m%d')
            if os.path.exists(source_md):
                os.rename(source_md, os.path.join(bd, f'source_legacy_{today}.md'))
            old_ent_dir = os.path.join(bd, 'source', 'entities')
            if os.path.isdir(old_ent_dir):
                os.rename(old_ent_dir, os.path.join(bd, 'source', f'entities_legacy_{today}'))
            old_vec = os.path.join(bd, '.vector_db')
            if os.path.isdir(old_vec):
                shutil.rmtree(old_vec, ignore_errors=True)
            cp = os.path.join(bd, 'readthrough_checkpoint.json')
            if os.path.exists(cp):
                try: os.remove(cp)
                except: pass
            meta['kb_status'] = 'needs_rebuild'
            save_json(meta_path, meta)
            log_action('MIGRATE_OLD_BOOK', f'{bid}: 旧数据备份完毕，标记 needs_rebuild')


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """覆盖 handle_error：客户端断连（页面刷新 / SSE 关闭 / keepalive 超时）静默吞掉，
    其它异常按原 stderr+traceback 行为照旧。Windows 上这些 errno 出现得特别勤。"""
    _IGNORED_EXC = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, self._IGNORED_EXC):
            return
        super().handle_error(request, client_address)


def run():
    bind_host = '127.0.0.1'
    try:
        s = load_json(SETTINGS_FILE)
        scope = s.get('access_scope', '127.0.0.1')
        if scope in ('127.0.0.1', '0.0.0.0'):
            bind_host = scope
    except Exception:
        pass
    _migrate_old_books()
    # 提前生成硬件策略缓存。embeddings.py 会读它决定嵌入模型放 CPU 还是 GPU，
    # 必须在 _warmup_embedding_backend 触发首次加载之前就位。
    try:
        _load_local_strategy()
    except Exception as e:
        log_action('STRATEGY_PREWARM_ERR', str(e)[:200])
    # 后台预热本地嵌入模型，避免用户首次给 Luca 发消息时遭遇 1-2 秒冷启动加载延迟。
    # 仅本地模型有"磁盘→RAM"加载开销；API 嵌入跳过以免浪费配额。
    def _warmup_embedding_backend():
        try:
            from embeddings import get_embedding_backend, LocalEmbedding
            backend = get_embedding_backend(get_settings())
            if isinstance(backend, LocalEmbedding):
                t0 = time.time()
                backend.embed(['warmup'])
                log_action('EMBEDDING_WARMUP', f'backend={backend.backend_id} elapsed={time.time()-t0:.2f}s')
        except Exception as e:
            log_action('EMBEDDING_WARMUP_ERR', str(e)[:200])
    threading.Thread(target=_warmup_embedding_backend, name='embedding_warmup', daemon=True).start()
    server = _QuietThreadingHTTPServer((bind_host, PORT), Handler)
    print(f'Server running on http://{bind_host}:{PORT}')
    server.serve_forever()


if __name__ == '__main__':
    run()
