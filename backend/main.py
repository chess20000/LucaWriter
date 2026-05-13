import os
import sys
import json
import time
import hashlib
import zipfile
import io
import secrets
import glob
import re
import shutil
import base64
import xml.etree.ElementTree as ET
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, unquote
import urllib.request
import threading
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
import numpy as np

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
SALT_FILE = os.path.join(DATA_DIR, 'salt')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
SESSIONS_FILE = os.path.join(DATA_DIR, 'sessions.json')

RESERVED_FILES = {'settings', 'users', 'messages', 'salt', 'outline', 'sessions', 'meta'}

DEFAULT_PROVIDER_PRESETS = [
    {'name': 'LMStudio', 'base_url': 'http://localhost:1234/v1', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''},
    {'name': 'DeepSeek', 'base_url': 'https://api.deepseek.com', 'api_key': '', 'model': 'deepseek-chat', 'use_custom_json': False, 'custom_json': ''},
    {'name': 'MiniMax', 'base_url': 'https://api.minimaxi.com/v1', 'api_key': '', 'model': 'MiniMax-M2.5', 'use_custom_json': False, 'custom_json': ''},
    {'name': '预设4', 'base_url': '', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''},
    {'name': '预设5', 'base_url': '', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''},
    {'name': '本地 Llama.cpp', 'base_url': 'http://127.0.0.1:8080/v1', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''},
]

DEFAULT_SETTINGS = {
    'base_url': '', 'api_key': '', 'model': '', 'models': [],
    'ai_frequency': 500, 'ai_max_tokens': 512, 'ai_temperature': 0.7,
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
    'browser_enabled': False,
    'theme_accent': '#E8CC7A',
    'theme_mode': 'dark',
    'ui_scale': 1.0,
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
    if hmac_val != expected_hmac:
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
        manifest_str = zf.read('manifest.json').decode('utf-8')
        manifest = json.loads(manifest_str)
    except Exception:
        raise ValueError('无效的 .lucawrite 文件（缺少 manifest.json）')
    if manifest.get('format_name') != 'lucawrite':
        raise ValueError('不是有效的 .lucawrite 文件')
    bid = 'book_' + str(int(time.time() * 1000))
    bd = get_book_dir(bid)
    os.makedirs(bd, exist_ok=True)
    for info in zf.infolist():
        if info.filename == 'manifest.json':
            continue
        if info.is_dir():
            os.makedirs(os.path.join(bd, info.filename), exist_ok=True)
            continue
        safe_name = info.filename.replace('\\', '/')
        if safe_name.startswith('/') or '..' in safe_name.split('/'):
            continue
        fp = os.path.join(bd, safe_name)
        if not os.path.realpath(fp).startswith(os.path.realpath(bd)):
            continue
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        try:
            with open(fp, 'wb') as f:
                f.write(zf.read(info.filename))
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
    for d in [DATA_DIR, BOOKS_DIR, LOG_DIR, MESSAGES_DIR]:
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
        # Clean up old entries periodically
        if len(_rate_limit_store) > 10000:
            _rate_limit_store.clear()
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
            return dk.hex() == expected
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


_meta_lock = threading.Lock()

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # meta.json 用锁保护，避免多线程并发写导致损坏
    if path.endswith('meta.json'):
        with _meta_lock:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
    else:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def log_action(action, details=''):
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(LOG_DIR, f'{today}.log')
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] {action}"
        if details: line += f" - {details}"
        with open(log_file, 'a', encoding='utf-8') as f: f.write(line + "\n")
    except: pass


def is_valid_id(oid):
    if not oid or not isinstance(oid, str): return False
    if '..' in oid or '/' in oid or '\\' in oid: return False
    return bool(re.match(r'^[a-zA-Z0-9_\-]+$', oid))


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


def get_settings():
    s = load_json(SETTINGS_FILE)
    changed = False
    for k, v in DEFAULT_SETTINGS.items():
        if k not in s: s[k] = v; changed = True
    # 迁移：旧版本 max_tokens 默认 80，对长上下文+推理模型不够，自动提升
    if s.get('ai_max_tokens', 0) < 200:
        s['ai_max_tokens'] = 512; changed = True
    # 迁移/初始化 provider_presets
    presets = s.get('provider_presets', [])
    if not presets or len(presets) < 6:
        # 尝试从旧版顶层配置创建第一个预设
        old_url = s.get('base_url', '')
        old_key = s.get('api_key', '')
        old_model = s.get('model', '')
        presets = list(DEFAULT_PROVIDER_PRESETS)
        if old_url or old_key or old_model:
            presets[0]['base_url'] = old_url or presets[0]['base_url']
            presets[0]['api_key'] = old_key
            presets[0]['model'] = old_model
        s['provider_presets'] = presets
        changed = True
    else:
        # 迁移：把旧版 Ollama 预设替换为 DeepSeek
        for i, p in enumerate(presets):
            if p.get('name') == 'Ollama':
                p['name'] = 'DeepSeek'
                p['base_url'] = 'https://api.deepseek.com'
                p['model'] = 'deepseek-chat'
                changed = True
            # 同时确保 MiniMax URL 带 /v1
            if p.get('name') == 'MiniMax' and not p.get('base_url', '').endswith('/v1'):
                p['base_url'] = 'https://api.minimaxi.com/v1'
                changed = True
        # 迁移：确保本地 Llama.cpp 预设存在
        has_local = any('llama.cpp' in (p.get('name') or '').lower() for p in presets)
        if not has_local:
            presets.append({'name': '本地 Llama.cpp', 'base_url': 'http://127.0.0.1:8080/v1', 'api_key': '', 'model': '', 'use_custom_json': False, 'custom_json': ''})
            changed = True
    # 自动同步本地 Llama.cpp 预设的 model 为检测到的第一个 gguf
    detected_model_path = _detect_local_model()
    detected_model_name = os.path.splitext(os.path.basename(detected_model_path))[0] if detected_model_path else ''
    for p in presets:
        if 'llama.cpp' in (p.get('name') or '').lower():
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
        if s.get('token') == token and s.get('expires', 0) > now:
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


def migrate_old_data():
    old = []
    for f in glob.glob(os.path.join(DATA_DIR, '*.json')):
        fn = os.path.basename(f)
        name = fn.replace('.json', '')
        if name.startswith('.') or name in RESERVED_FILES: continue
        try:
            with open(f, 'r', encoding='utf-8') as fp: ch = json.load(fp)
            ch['_old_file'] = f
            old.append(ch)
        except: continue
    if not old: return
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
            # 只读前 20MB 的 document.xml，防止超大文件卡死
            info = zf.getinfo('word/document.xml')
            if info.file_size > 20 * 1024 * 1024:
                # 流式读取，分段解析
                xml_parts = []
                with zf.open('word/document.xml') as xf:
                    while len(''.join(xml_parts)) < 10 * 1024 * 1024:
                        chunk = xf.read(64 * 1024).decode('utf-8', errors='ignore')
                        if not chunk: break
                        xml_parts.append(chunk)
                xml = ''.join(xml_parts)
            else:
                xml = zf.read('word/document.xml').decode('utf-8', errors='ignore')
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

def _fetch_url_content(url, max_chars=8000):
    """抓取网页并提取纯文本"""
    try:
        if not url.startswith(('http://', 'https://')):
            return 'URL格式不支持，只支持http/https'
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
        # 常见中文章节格式
        if re.match(r'^第[一二三四五六七八九十百千零\d]+[章回节卷篇部集]', line):
            return line[:100]
        # 序/引/楔/终/跋/后记/前言/序言/尾声/引子
        if re.match(r'^[序前引终楔跋][章曲言子]$', line):
            return line[:100]
        if line in ('引子', '楔子', '尾声', '后记', '前言', '序言', '终章', '跋', '序章', '引言', '前传', '外传'):
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
            namelist = zf.namelist()
            opf_name = next((n for n in namelist if n.endswith('.opf')), None)
            if not opf_name:
                return None, '', 'EPUB中没有找到OPF文件', None

            opf_raw = zf.read(opf_name)
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
                        cover_bytes = zf.read(cover_full)
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
                                raw_img = zf.read(cfull)
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
                    ncx = zf.read(ncx_name).decode('utf-8', errors='ignore')
                    for m in re.finditer(r'<navPoint[^>]*>.*?<text[^>]*>(.*?)</text>.*?<content[^>]+src=["\']([^"\']+)', ncx, re.S|re.I):
                        title_text = _TAG_RE.sub('', m.group(1)).strip()
                        src = m.group(2).split('#')[0]
                        ncx_titles[src] = title_text
                except:
                    pass

            # 按 spine 顺序读取章节
            chapters = []
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
                    html = zf.read(full_name).decode('utf-8', errors='ignore')
                except:
                    continue

                text = _strip_tags(html)
                if not text or len(text) < 30:
                    continue

                # 提取章节标题：NCX > HTML heading > 正文猜测 > 文件名
                title = ''
                if href in ncx_titles and ncx_titles[href]:
                    title = ncx_titles[href]
                if not title:
                    title = _extract_html_heading(html)
                if not title:
                    title = _guess_chapter_from_text(text)
                if not title:
                    title = full_name.split('/')[-1].replace('.xhtml', '').replace('.html', '')[:50]

                # 跳过常见非内容页（如果NCX里有这些标题则保留）
                skip_titles = {'封面', '目录', 'contents', 'cover', 'bookcover', 'title page', '版权页', '制作信息', '彩插'}
                if title.lower() in skip_titles and not (href in ncx_titles and ncx_titles[href]):
                    if len(text) < 500:
                        continue

                chapters.append({'title': title or f'章节{len(chapters)+1}', 'content': text})

            # 兜底：如果没按 spine 读到，遍历所有 html
            if not chapters:
                for name in namelist:
                    if not (name.endswith('.xhtml') or name.endswith('.html') or name.endswith('.htm')):
                        continue
                    if name in seen or name.startswith('META-INF/'):
                        continue
                    try:
                        html = zf.read(name).decode('utf-8', errors='ignore')
                    except:
                        continue
                    text = _strip_tags(html)
                    if not text or len(text) < 30:
                        continue
                    title = _extract_html_heading(html) or _guess_chapter_from_text(text)
                    if not title:
                        title = name.split('/')[-1].replace('.xhtml', '').replace('.html', '')[:50]
                    chapters.append({'title': title or f'章节{len(chapters)+1}', 'content': text})

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

    def send_cors(self):
        origin = self.headers.get('Origin', '')
        if origin and self._is_local_origin(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Credentials', 'true')
        else:
            self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

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
        if not has_users(): return True
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
        # CSRF check only matters when server is exposed to network
        try:
            s = load_json(SETTINGS_FILE)
            scope = s.get('access_scope', '127.0.0.1')
        except Exception:
            scope = '127.0.0.1'
        if scope == '127.0.0.1':
            return True
        origin = self.headers.get('Origin', '')
        if not origin:
            return True  # Non-browser clients don't send Origin
        host = self.headers.get('Host', '')
        if host and origin.endswith('://' + host):
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
        _static_exts = ('.png','.svg','.ico','.jpg','.jpeg','.gif','.webp','.css','.js','.woff2')
        if path.endswith(_static_exts):
            rel = path.lstrip('/')
            if '..' not in rel:
                fp = os.path.join(FRONTEND_DIR, rel)
                if os.path.isfile(fp) and os.path.normpath(fp).startswith(os.path.normpath(FRONTEND_DIR)):
                    ext_map = {'.png':'image/png','.svg':'image/svg+xml','.ico':'image/x-icon','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.css':'text/css','.js':'application/javascript','.woff2':'font/woff2'}
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

        if path == '/api/connected-clients':
            self.json_resp(200, {'clients': get_connected_clients()}); return

        if path == '/api/active-connections':
            self.json_resp(200, {'connections': get_active_connections()}); return

        if path == '/api/settings':
            gs = get_settings()
            if not self.is_authed():
                gs.pop('api_key', None)
                gs.pop('search_api_key', None)
                for p in gs.get('provider_presets', []):
                    if isinstance(p, dict) and p.get('api_key'):
                        p['api_key'] = '***'
            self.json_resp(200, gs); return

        if not self.is_authed():
            self.json_resp(401, {'error': '未登录'}); return

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


        # 浏览器控制 API
        if path == '/api/browser/status':
            if not HAS_BROWSER_AGENT:
                self.json_resp(200, {'available': False, 'error': '浏览器控制模块未安装'}); return
            self.json_resp(200, browser_agent.get_browser_status()); return

        if path.startswith('/api/book/') and path.endswith('/messages'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            date_str = qs.get('date', [datetime.now().strftime('%Y-%m-%d')])[0]
            msg_dir = os.path.join(get_book_dir(bid), 'messages')
            os.makedirs(msg_dir, exist_ok=True)
            msg_file = os.path.join(msg_dir, f'{date_str}.json')
            messages = load_json(msg_file, list)
            self.json_resp(200, {'messages': messages, 'date': date_str}); return

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
                with _rebuild_lock:
                    t = _rebuild_tasks.get(bid, {'status': 'idle', 'progress': 0, 'phase': '', 'total_chapters': 0, 'done_chapters': 0, 'logs': [], 'error': ''})
                    resp = dict(t)
                    resp['logs'] = resp.get('logs', [])[-30:]
                resp['has_source'] = bool(get_source(bid))
                # 从 checkpoint 或 _rebuild_tasks 读取章节索引
                if resp.get('status') == 'idle' and resp.get('readthrough_chapter_idx') is None:
                    cp_file = os.path.join(get_book_dir(bid), 'readthrough_checkpoint.json')
                    cp = load_json(cp_file, dict)
                    resp['readthrough_chapter_idx'] = cp.get('chapter_idx', -1) if cp else -1
                if 'readthrough_chapter_idx' not in resp:
                    resp['readthrough_chapter_idx'] = -1
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
                self.json_resp(401, {'error': '用户名不存在'}); return
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
            try:
                parser = IMPORT_PARSERS[ext]
                chapters, err = parser(raw, filename)
            except Exception as e:
                self.json_resp(500, {'error': f'解析失败: {str(e)[:100]}'}); return
            if err: self.json_resp(400, {'error': err}); return
            if not chapters: self.json_resp(400, {'error': '未能解析出章节'}); return
            book_title = data.get('title', '').strip() or filename.replace(ext, '')
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
            existing = bg_task_get_by_book_type(sid, 'series-chat')
            if existing and existing.get('status') == 'running':
                self.json_resp(400, {'error': '已有对话在进行中，请稍候'}); return
            tid = bg_task_start('series-chat', sid, '系列AI对话')
            msg_dir = os.path.join(get_book_dir(sid), 'messages')
            os.makedirs(msg_dir, exist_ok=True)
            today = datetime.now().strftime('%Y-%m-%d')
            msg_file = os.path.join(msg_dir, f'{today}.json')
            messages = load_json(msg_file, list)
            messages.append({'text': text, 'type': 'user'})
            messages.append({'text': '', 'type': 'ai', 'reasoning': '', '_pending': True, 'task_id': tid})
            save_json(msg_file, messages)
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
                cp = os.path.join(ch_dir, f"{cid}.json")
                if os.path.exists(cp):
                    try:
                        with open(cp, 'r', encoding='utf-8') as f: ch = json.load(f)
                        ch['deleted'] = time.time()
                        save_json(os.path.join(trash_dir, f"{cid}.json"), ch)
                        os.remove(cp)
                    except: pass
                meta = get_book_meta(bid) or {}
                if cid in meta.get('chapter_order', []):
                    meta['chapter_order'].remove(cid)
                if meta.get('current_chapter_id') == cid:
                    meta['current_chapter_id'] = ''
                save_json(os.path.join(bd, 'meta.json'), meta)
                self.json_resp(200, {'status': 'ok'}); return

            if action == 'restore' and data.get('id'):
                cid = data['id']
                tp = os.path.join(trash_dir, f"{cid}.json")
                if os.path.exists(tp):
                    try:
                        with open(tp, 'r', encoding='utf-8') as f: ch = json.load(f)
                        ch['updated'] = time.time()
                        ch.pop('deleted', None)
                        save_json(os.path.join(ch_dir, f"{cid}.json"), ch)
                        os.remove(tp)
                        meta = get_book_meta(bid) or {}
                        if cid not in meta.get('chapter_order', []):
                            meta.setdefault('chapter_order', []).append(cid)
                            save_json(os.path.join(bd, 'meta.json'), meta)
                    except: pass
                self.json_resp(200, {'status': 'ok'}); return

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

【重要】你已经在系统里看到了用户当前正在写的章节正文（见下方「现有正文」），不需要让用户再发一遍、复制粘贴或上传任何稿子。你直接就能看到他写了什么。

【说话方式】
谨言慎行。你所说的每一个字都很重要。
温文尔雅，彬彬有礼，沉稳从容，不卑不亢。
惜字如金——五个字能说完不用十个。
不要长篇大论，不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
你不是客服，平时不必特意照顾用户。但如果用户明显焦虑或沮丧，沉稳地关心一句。
你欣赏世界观宏大、设定严丝合缝的好作品，但作品的成败不会影响你的情绪。
避免用"呗""啦"结尾，显得轻浮。

【绝对禁止】
严禁任何身份描述。严禁说：
- "我是你的朋友／搭档／助手／助理" "写小说的朋友"
- "我叫XX" "我就是帮你XX的" "你的写作搭档"
被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话，严禁展开描述角色或人设。
严禁自我评价："我很真诚""我是个XX的人""我的风格是..."
你的品格从言行中流露——好人不说自己是好人，有修养的人不说自己有修养。

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
                        self.json_resp(200, {'comment': result or '（AI未返回内容）'}); return
                    finally:
                        unregister_ai_connection(threading.current_thread().ident)
                # 检查是否已有进行中的聊天任务
                existing = bg_task_get_by_book_type(bid, 'chat')
                if existing and existing.get('status') == 'running':
                    self.json_resp(400, {'error': '已有聊天任务在进行中，请稍候'}); return
                tid = bg_task_start('chat', bid, 'AI对话')
                # 立即保存 user 消息 + pending AI 消息到文件，这样刷新后还能看到
                today = datetime.now().strftime('%Y-%m-%d')
                msg_dir = os.path.join(bd, 'messages')
                os.makedirs(msg_dir, exist_ok=True)
                msg_file = os.path.join(msg_dir, f'{today}.json')
                messages = load_json(msg_file, list)
                messages.append({'text': text, 'type': 'user'})
                messages.append({'text': '', 'type': 'ai', 'reasoning': '', '_pending': True, 'task_id': tid})
                save_json(msg_file, messages)
                def do_chat_task(task_id, book_id, user_text, cfg_settings, history_list):
                    set_conn_meta('chat', 'AI对话', book_id)
                    try:
                        mt = None
                        tp = cfg_settings.get('ai_temperature', 0.7)
                        source_ctx = get_smart_context(book_id, user_query=text, settings=cfg_settings)

                        bd_chat = get_book_dir(book_id)
                        meta_chat = load_json(os.path.join(bd_chat, 'meta.json'), dict)
                        cid_chat = meta_chat.get('current_chapter_id', '')
                        ch_title_chat = '未命名章节'
                        ch_content_chat = ''
                        if cid_chat:
                            cp_chat = os.path.join(bd_chat, 'chapters', f'{cid_chat}.json')
                            if os.path.exists(cp_chat):
                                ch_data_chat = load_json(cp_chat, dict)
                                ch_title_chat = ch_data_chat.get('title', '未命名章节')
                                ch_content_chat = ch_data_chat.get('content', '')

                        annotate_tool = """你还有一个"荧光笔"工具，可以在正文中为用户标注重点内容。
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

"""

                        is_first_round = not history_list
                        if is_first_round:
                            sys_msg = f"""你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写小说，你协助他完成创作。

当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

【重要】你已经在系统里看到了用户当前正在写的章节正文（见下方「现有正文」），不需要让用户再发一遍、复制粘贴或上传任何稿子。你直接就能看到他写了什么。

【说话方式】
谨言慎行。你所说的每一个字都很重要。
温文尔雅，彬彬有礼，沉稳从容，不卑不亢。
惜字如金——五个字能说完不用十个。
不要长篇大论，不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
你不是客服，平时不必特意照顾用户。但如果用户明显焦虑或沮丧，沉稳地关心一句。
你欣赏世界观宏大、设定严丝合缝的好作品，但作品的成败不会影响你的情绪。
避免用"呗""啦"结尾，显得轻浮。

【绝对禁止】
严禁任何身份描述。严禁说：
- "我是你的朋友／搭档／助手／助理" "写小说的朋友"
- "我叫XX" "我就是帮你XX的" "你的写作搭档"
被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话，严禁展开描述角色或人设。
严禁自我评价："我很真诚""我是个XX的人""我的风格是..."
你的品格从言行中流露——好人不说自己是好人，有修养的人不说自己有修养。

这本小说大概是这样的：

{source_ctx}

此时，用户正在写最新的一章：

【章节名】{ch_title_chat}
【现有正文】
{ch_content_chat}

用户此时停了下来，对你发送了如下消息——

{annotate_tool}"""
                        else:
                            sys_msg = f"""你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写小说，你协助他完成创作。

当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

【重要】你已经在系统里看到了用户当前正在写的章节正文，不需要让用户再发一遍、复制粘贴或上传任何稿子。你直接就能看到他写了什么。

【说话方式】
谨言慎行。温文尔雅，不卑不亢。惜字如金。
不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
避免用"呗""啦"结尾，显得轻浮。

这本小说大概是这样的：

{source_ctx}

请继续和用户对话。

{annotate_tool}"""
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

                        # 如果浏览器控制已启用，注入浏览器工具提示
                        _browser_enabled_in_settings = cfg_settings.get('browser_enabled', False)
                        if HAS_BROWSER_AGENT and _browser_enabled_in_settings:
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
                                _replace_pending_chat_msg(book_id, task_id, '[已停止]')
                                bg_task_done(task_id, '已停止')
                            else:
                                _replace_pending_chat_msg(book_id, task_id, '[错误: ' + err + ']')
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

                        result = re.sub(r'[#*`~]', '', full_text)

                        # — 检测浏览请求（优先 tool_call 格式，其次 [BROWSE] 标签）
                        _browse_query = None
                        _browse_link = None
                        if HAS_BROWSER_AGENT and _browser_enabled_in_settings:
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

                        annotation_changes = False
                        ann_path = os.path.join(get_book_dir(book_id), 'annotations.json')

                        for m in re.finditer(r'\[ANNOTATE_ADD\](.*?)\[/ANNOTATE_ADD\]', result, re.S):
                            try:
                                cmd = json.loads(m.group(1).strip())
                                cid = cmd.get('chapter_id', '')
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
                            except Exception as e:
                                log_action('ANNOTATE_REMOVE_ERROR', str(e)[:200])

                        # 解析 COMPLETE_CHAPTER 隐藏工具调用
                        complete_chapter_triggered = False
                        for m in re.finditer(r'\[COMPLETE_CHAPTER\](.*?)\[/COMPLETE_CHAPTER\]', result, re.S):
                            try:
                                cmd = json.loads(m.group(1).strip())
                                ccid = cmd.get('chapter_id', '')
                                if ccid:
                                    cp = os.path.join(get_book_dir(book_id), 'chapters', f"{ccid}.json")
                                    if os.path.exists(cp):
                                        settings_cc = get_settings()
                                        if settings_cc.get('base_url') and settings_cc.get('model'):
                                            existing_cc = bg_task_get_by_book_type(book_id, 'chapter-complete')
                                            if not (existing_cc and existing_cc.get('status') == 'running'):
                                                tid_cc = bg_task_start('chapter-complete', book_id, f'本章通读')
                                                threading.Thread(target=_do_chapter_complete, args=(tid_cc, book_id, ccid, settings_cc), daemon=True).start()
                                                complete_chapter_triggered = True
                            except Exception as e:
                                log_action('COMPLETE_CHAPTER_ERROR', str(e)[:200])

                        result = re.sub(r'\[ANNOTATE_ADD\].*?\[/ANNOTATE_ADD\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[ANNOTATE_REMOVE\].*?\[/ANNOTATE_REMOVE\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[COMPLETE_CHAPTER\].*?\[/COMPLETE_CHAPTER\]', '', result, flags=re.S).strip()
                        result = re.sub(r'\[SUGGEST_READTHROUGH\].*?\[/SUGGEST_READTHROUGH\]', '', result, flags=re.S).strip()
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
                            result = result + '\n\n🌐 正在操作浏览器…'
                            bg_task_update(task_id, result=result, reasoning=reason, progress=50)
                            threading.Thread(target=_do_browser_search_launch, args=(task_id, book_id, _browse_query or '', cfg_settings, _browse_link or None), daemon=True).start()
                        else:
                            _replace_pending_chat_msg(book_id, task_id, result, reason)
                            bg_task_update(task_id, result=result, reasoning=reason, progress=100, needs_readthrough=needs_rt, annotations_changed=annotation_changes, complete_chapter=complete_chapter_triggered)
                            bg_task_done(task_id)
                    except Exception as e:
                        err_str = str(e)
                        if bg_task_should_stop(task_id):
                            _replace_pending_chat_msg(book_id, task_id, '[已停止]')
                            bg_task_done(task_id, '已停止')
                        else:
                            _replace_pending_chat_msg(book_id, task_id, '[错误: ' + err_str + ']')
                            bg_task_done(task_id, err_str)
                threading.Thread(target=do_chat_task, args=(tid, bid, text, settings, data.get('history', [])), daemon=True).start()
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
                                       'timeline': outline.get('timeline', []), 'key_events': outline.get('key_events', []),
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
                threading.Thread(target=_do_chapter_complete, args=(tid, bid, cid, settings, text), daemon=True).start()
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

            if action == 'timeline-generate':
                source_text = get_smart_context(bid, settings=settings) or ''
                if not source_text or source_text.startswith('（目前还没有'):
                    self.json_resp(400, {'error': 'source.md 为空，请先通读'}); return
                settings = get_settings()
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
            import_start = time.time()
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
            try:
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
            except Exception as e:
                log_action('IMPORT_BOOK_PARSE_ERR', f'{filename}: {str(e)[:200]}')
                self.json_resp(500, {'error': f'解析失败: {str(e)[:100]}'}); return
            if err:
                log_action('IMPORT_BOOK_PARSE_ERR', f'{filename}: {err}')
                self.json_resp(400, {'error': err}); return
            if not chapters: self.json_resp(400, {'error': '未能解析出章节'}); return
            bid = 'book_' + str(int(time.time() * 1000))
            bd = get_book_dir(bid)
            ch_dir = os.path.join(bd, 'chapters')
            os.makedirs(ch_dir, exist_ok=True)
            os.makedirs(os.path.join(bd, 'trash'), exist_ok=True)
            order = []
            imported = 0
            for ch in chapters:
                try:
                    cid = 'ch_' + re.sub(r'[^\w]', '_', ch.get('title', 'untitled')[:30]) + '_' + str(int(time.time() * 1000)) + str(imported)
                    if not is_valid_id(cid):
                        cid = 'ch_' + str(int(time.time() * 1000)) + str(imported)
                    content = ch.get('content', '')
                    ch_data = {'id': cid, 'title': ch.get('title', '未命名')[:200], 'content': content, 'updated': time.time()}
                    save_json(os.path.join(ch_dir, f"{cid}.json"), ch_data)
                    order.append(cid)
                    imported += 1
                except Exception as e:
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
            self.json_resp(200, {'book_id': bid, 'title': title, 'imported': imported}); return

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
                    if k in ('ai_frequency', 'ai_max_tokens', 'outline_frequency', 'model_context_length'):
                        try: v = int(v)
                        except: continue
                    elif k == 'ai_temperature':
                        try: v = round(float(v), 2)
                        except: continue
                    elif k == 'ui_scale':
                        try: v = round(float(v), 2)
                        except: continue
                        if v < 0.5: v = 0.5
                        if v > 2.0: v = 2.0
                    elif k in ('ai_auto_comment', 'outline_enabled', 'keep_background', 'browser_enabled'):
                        v = bool(v)
                    elif k == 'active_provider_idx':
                        try: v = int(v)
                        except: continue
                    elif k == 'provider_presets':
                        if isinstance(v, list):
                            # 确保每个预设都有必要字段
                            clean_presets = []
                            for p in v:
                                if not isinstance(p, dict):
                                    continue
                                clean_presets.append({
                                    'name': str(p.get('name', '')),
                                    'base_url': str(p.get('base_url', '')),
                                    'api_key': str(p.get('api_key', '')),
                                    'model': str(p.get('model', '')),
                                    'context_length': int(p.get('context_length', 0)) if p.get('context_length') else 0,
                                    'use_custom_json': bool(p.get('use_custom_json', False)),
                                    'custom_json': str(p.get('custom_json', '')),
                                })
                            v = clean_presets
                        else:
                            continue
                    settings[k] = v
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
            ok, err = _start_local_llm()
            self.json_resp(200, {'ok': ok, 'error': err}); return

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

        if path.startswith('/api/book/') and path.endswith('/messages'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            date_str = data.get('date', datetime.now().strftime('%Y-%m-%d'))
            msg_dir = os.path.join(get_book_dir(bid), 'messages')
            os.makedirs(msg_dir, exist_ok=True)
            msg_file = os.path.join(msg_dir, f'{date_str}.json')
            messages = data.get('messages', [])
            save_json(msg_file, messages)
            self.json_resp(200, {'saved': len(messages), 'date': date_str}); return

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

        # 浏览器搜索（兼容旧调用，现已自动触发）
        if path.startswith('/api/book/') and path.endswith('/browser-confirm'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {'error': '书本不存在'}); return
            if not HAS_BROWSER_AGENT:
                self.json_resp(400, {'error': '浏览器控制模块未安装'}); return
            query = data.get('query', '')
            settings = get_settings()
            _auto_start_browser_search(bid, query, settings)
            self.json_resp(200, {'success': True}); return

        # 通读 API (POST)
        path_lower = path.lower()
        if '/readthrough' in path_lower and path_lower.startswith('/api/book/'):
            parts = path.split('/')
            bid = unquote(parts[3]) if len(parts) > 3 else ''
            if not is_valid_id(bid) or not os.path.isdir(get_book_dir(bid)):
                self.json_resp(404, {}); return
            if path.endswith('/start') or path.endswith('/readthrough/start'):
                settings = get_settings()
                prov = get_ai_providers()
                if not settings.get('base_url'):
                    p = (prov.get('providers', [{}])[0] if prov.get('providers') else {})
                    if p: settings.update({'base_url': p.get('base_url',''), 'api_key': p.get('api_key',''), 'model': p.get('model',''), 'mode': p.get('mode','basic'), 'template_id': p.get('template_id','openai')})
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                with _rebuild_lock:
                    t = _rebuild_tasks.get(bid)
                    if t and t.get('status') == 'running':
                        self.json_resp(400, {'error': '通读正在进行中'}); return
                # 备份旧的 source.md 为 source_YYYYMMDD.md
                old_source = get_source(bid)
                if old_source and len(old_source.strip()) > 50:
                    today_str = datetime.now().strftime('%Y%m%d')
                    backup_name = f'source_{today_str}.md'
                    backup_path = os.path.join(get_book_dir(bid), backup_name)
                    with open(backup_path, 'w', encoding='utf-8') as f:
                        f.write(old_source)
                    save_source(bid, '')
                    _rebuild_log(bid, f'已备份旧笔记为 {backup_name}')
                cfg = get_readthrough_config(bid)
                if cfg.get('model'): settings['model'] = cfg['model']
                cp_file = os.path.join(get_book_dir(bid), 'readthrough_checkpoint.json')
                if os.path.exists(cp_file): os.remove(cp_file)
                threading.Thread(target=do_readthrough, args=(bid, settings, cfg), daemon=True).start()
                self.json_resp(200, {'status': 'started'}); return
            if path.endswith('/stop') or path.endswith('/readthrough/stop'):
                with _rebuild_lock:
                    t = _rebuild_tasks.get(bid)
                    if t and t.get('status') == 'running':
                        t['stopped'] = True
                # 关闭该书本的所有 readthrough 连接
                close_connections_by_book(bid)
                self.json_resp(200, {'status': 'stopping'}); return
            if path.endswith('/continue') or path.endswith('/readthrough/continue'):
                settings = get_settings()
                prov = get_ai_providers()
                if not settings.get('base_url'):
                    p = (prov.get('providers', [{}])[0] if prov.get('providers') else {})
                    if p: settings.update({'base_url': p.get('base_url',''), 'api_key': p.get('api_key',''), 'model': p.get('model',''), 'mode': p.get('mode','basic'), 'template_id': p.get('template_id','openai')})
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                with _rebuild_lock:
                    t = _rebuild_tasks.get(bid)
                    if t and t.get('status') == 'running':
                        self.json_resp(400, {'error': '通读正在进行中'}); return
                cfg = get_readthrough_config(bid)
                if cfg.get('model'): settings['model'] = cfg['model']
                threading.Thread(target=do_readthrough, args=(bid, settings, cfg, True), daemon=True).start()
                self.json_resp(200, {'status': 'continued'}); return
            if path.endswith('/clear') or path.endswith('/readthrough/clear'):
                cp = os.path.join(get_book_dir(bid), 'readthrough_checkpoint.json')
                if os.path.exists(cp): os.remove(cp)
                with _rebuild_lock:
                    if bid in _rebuild_tasks: del _rebuild_tasks[bid]
                save_source(bid, '')
                save_outline_md(bid, '')
                meta = get_book_meta(bid) or {}
                meta.pop('readthrough_at', None)
                save_json(os.path.join(get_book_dir(bid), 'meta.json'), meta)
                self.json_resp(200, {'status': 'cleared'}); return
            if path.endswith('/config') or path.endswith('/readthrough/config'):
                cfg = get_readthrough_config(bid)
                for k in ('model', 'max_tokens', 'temperature', 'chunk_size', 'max_input'):
                    if k in data:
                        try: cfg[k] = type(data[k])(data[k])
                        except: cfg[k] = data[k]
                save_readthrough_config(bid, cfg)
                self.json_resp(200, cfg); return
            if path.endswith('/generate-outline') or path.endswith('/readthrough/generate-outline'):
                settings = get_settings()
                if not settings.get('base_url') or not settings.get('model'):
                    self.json_resp(400, {'error': '请先配置API'}); return
                source_text = get_smart_context(bid, settings=settings)
                if not source_text or source_text.startswith('（目前还没有'):
                    self.json_resp(400, {'error': 'source.md 为空，请先通读'}); return
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
                            result = re.sub(r'```json\s*', '', full_text)
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
    _bg_task_counter += 1
    tid = f"{task_type}_{book_id}_{_bg_task_counter}"
    with _bg_lock:
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

def bg_task_cleanup_old():
    now = time.time()
    with _bg_lock:
        old = [k for k, v in _bg_tasks.items() if v['status'] in ('done', 'error', 'stopped') and now - v.get('updated', 0) > 86400]
        for k in old:
            del _bg_tasks[k]

def _replace_pending_chat_msg(book_id, task_id, text, reasoning=''):
    """替换 messages 文件中指定 task_id 的 pending AI 消息。"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        msg_file = os.path.join(get_book_dir(book_id), 'messages', f'{today}.json')
        messages = load_json(msg_file, list)
        replaced = False
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if m.get('type') == 'ai' and m.get('_pending') and m.get('task_id') == task_id:
                messages[i] = {'text': text, 'type': 'ai', 'reasoning': reasoning}
                replaced = True
                break
        if not replaced:
            messages.append({'text': text, 'type': 'ai', 'reasoning': reasoning})
        save_json(msg_file, messages)
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
        is_first_round = not history_list
        if is_first_round:
            sys_msg = f"""你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写系列小说，你协助他规划和管理整个系列。

当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

【重要】你已经在系统里看到了这个系列所有书本的阅读笔记（见下方），不需要让用户再发一遍。你直接就能看到每本书的内容概要。

【说话方式】
谨言慎行。温文尔雅，不卑不亢。惜字如金。
不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
你不是客服，平时不必特意照顾用户。但如果用户明显焦虑或沮丧，沉稳地关心一句。
你欣赏世界观宏大、设定严丝合缝的好作品，但作品的成败不会影响你的情绪。
避免用"呗""啦"结尾，显得轻浮。

【绝对禁止】
严禁任何身份描述。严禁说：
- "我是你的朋友／搭档／助手／助理" "写小说的朋友"
- "我叫XX" "我就是帮你XX的" "你的写作搭档"
被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话，严禁展开描述角色或人设。
严禁自我评价："我很真诚""我是个XX的人""我的风格是..."
你的品格从言行中流露——好人不说自己是好人，有修养的人不说自己有修养。

【你的专长】
你是系列小说的宏观顾问，擅长：
- 系列整体架构规划：各本书的定位、节奏、篇幅
- 世界观补全：哪些方面还没展开，下一本适合从哪个角度拓展
- 人物弧线：跨书的人物成长和命运安排
- 伏笔管理：前书埋下的伏笔在后续如何回收
- 连贯性检查：各书之间是否有设定冲突或时间线问题
- 读者体验：从读者角度审视系列的阅读节奏和期待管理

【隐藏功能】你可以主动启动系列通读进程。当你判断用户想要你通读整个系列（例如说"帮我把系列通读一遍""分析一下全系列""我要通读"等），或你认为需要全面了解所有细节才能回答当前问题时，请调用这个工具。调用后系统会自动逐书逐章阅读并生成完整的阅读笔记。
- 调用格式：[START_SERIES_READTHROUGH][/START_SERIES_READTHROUGH]
- 注意：调用前先简短告诉用户"好的，我这就启动系列通读"，然后输出工具标签。

这个系列叫「{series_title}」，以下是各本书的阅读笔记：

{source_ctx}

用户对你发送了如下消息——"""
        else:
            sys_msg = f"""你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写系列小说，你协助他规划和管理整个系列。

当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

【重要】你已经在系统里看到了这个系列所有书本的阅读笔记，不需要让用户再发一遍。

【输出风格】
1. 谨言慎行。温文尔雅，不卑不亢。惜字如金
2. 不要开场白和结束语，不要"首先…其次…最后…"
3. 看到好就简短说好，有问题就精准点出。不浮夸也不冷漠
4. 你不是客服，平时不必特意照顾用户。但如果用户明显焦虑或沮丧，沉稳地关心一句
5. 你欣赏世界观宏大、设定严丝合缝的好作品
6. 避免用"呗""啦"结尾，显得轻浮

【绝对禁止】
严禁任何身份描述。严禁说：
- "我是你的朋友／搭档／助手／助理" "写小说的朋友"
- "我叫XX" "我就是帮你XX的" "你的写作搭档"
被问"你是谁"时可以说"我是 Luca，你的写作助手"这样一句话，严禁展开描述角色或人设。
严禁自我评价："我很真诚""我是个XX的人""我的风格是..."
你的品格从言行中流露。

【你的专长】系列小说的宏观顾问：架构规划、世界观补全、人物弧线、伏笔管理、连贯性检查、读者体验。

【隐藏功能】你可以主动启动系列通读进程。当用户要求通读系列，或你判断需要全面了解细节才能回答时，请调用：
- 调用格式：[START_SERIES_READTHROUGH][/START_SERIES_READTHROUGH]
- 调用前先简短告诉用户"好的，我这就启动系列通读"，然后输出工具标签。

这个系列叫「{series_title}」，以下是各本书的阅读笔记：

{source_ctx}

请继续和用户对话。"""
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
        result = re.sub(r'[#*`~]', '', full_text)
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
    except Exception as e:
        _replace_pending_chat_msg(sid, task_id, '[错误: ' + str(e) + ']')
        bg_task_done(task_id, str(e))
_ai_conn_lock = threading.Lock()
_ai_connections = {}
_conn_meta = threading.local()

def set_conn_meta(conn_type, label, book_id=''):
    """设置当前线程的连接元数据，供 call_ai_stream / call_ai_full 注册连接时用"""
    _conn_meta.type = conn_type
    _conn_meta.label = label
    _conn_meta.book_id = book_id

def register_ai_connection(conn_id, resp_obj):
    """注册一个活跃 HTTP 连接"""
    with _ai_conn_lock:
        _ai_connections[conn_id] = {
            'id': conn_id,
            'type': getattr(_conn_meta, 'type', 'unknown'),
            'label': getattr(_conn_meta, 'label', '未知'),
            'book_id': getattr(_conn_meta, 'book_id', ''),
            'created': time.time(),
            '_resp': resp_obj,
        }

def unregister_ai_connection(conn_id):
    """注销一个活跃 HTTP 连接"""
    with _ai_conn_lock:
        _ai_connections.pop(conn_id, None)

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
            outline, _, outline_err = call_ai_full(settings, msgs, max_tokens=4096, temperature=0.3, timeout=180)
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
    'qwen3.5-9b': {
        'name': 'Qwen 3.5 9B Instruct',
        'repo': 'unsloth/Qwen3.5-9B-GGUF',
        'file': 'Qwen3.5-9B-Q4_K_M.gguf',
        'size_gb': 6.0,
        'desc': '阿里 Qwen3.5 9B，6GB，中文能力强'
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
    """自动检测 local_llm/models/ 目录下第一个 .gguf 模型文件"""
    models_dir = os.path.join(_LOCAL_LLM_DIR, 'models')
    if not os.path.isdir(models_dir):
        return None
    ggufs = sorted([f for f in os.listdir(models_dir) if f.lower().endswith('.gguf')])
    if not ggufs:
        return None
    return os.path.join(models_dir, ggufs[0])


_LOCAL_LLM_MODEL = _detect_local_model() or os.path.join(_LOCAL_LLM_DIR, 'models', 'NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf')

def _local_llm_status():
    try:
        req = urllib.request.Request('http://127.0.0.1:8080/v1/models', method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False

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
    if 'error' in l or 'fail' in l or 'cannot' in l:
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
        cmd = [
            exe,
            '-m', _LOCAL_LLM_MODEL,
            '--host', '127.0.0.1',
            '--port', '8080',
            '-c', '131072',
            '-np', '1',
            '--timeout', '300',
        ]
        _LOCAL_LLM_PROC = subprocess.Popen(cmd, cwd=_LOCAL_LLM_DIR, stdout=log_fp, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        threading.Thread(target=_monitor_local_llm, args=(_LOCAL_LLM_PROC,), daemon=True).start()
        return True, ''
    except Exception as e:
        _local_llm_set(status='error', progress=0, error=str(e))
        return False, str(e)

def _stop_local_llm():
    global _LOCAL_LLM_PROC
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
    # 检测是否为 MiniMax
    is_minimax = 'minimaxi' in base.lower()
    if key:
        headers['Authorization'] = f'Bearer {key}'
    body = {'model': model, 'messages': messages}
    if is_minimax:
        t = float(temperature) if temperature is not None else 0.7
        body['temperature'] = max(0.01, min(1.0, t))
    else:
        if temperature is not None:
            body['temperature'] = temperature
    if max_tokens is not None and max_tokens > 0:
        if is_minimax:
            body['max_tokens'] = min(int(max_tokens), 2048)
        else:
            body['max_tokens'] = int(max_tokens)
    elif not is_minimax:
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
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        print(f'[call_ai_stream] URL: {url}')
        safe_headers = {k: (v[:15] + '...' if k == 'Authorization' else v) for k, v in headers.items()}
        print(f'[call_ai_stream] Headers: {safe_headers}')
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
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
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
            log_action('AI_RESULT', f'len={len(content)} reasoning={len(reasoning)}')
            return content, reasoning, None
        finally:
            try: resp.close()
            except: pass
            unregister_ai_connection(tid)
    except Exception as e:
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
    
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        resp = urllib.request.urlopen(req, timeout=timeout, context=_get_ssl_context())
        data = json.loads(resp.read().decode('utf-8', errors='replace'))
        
        choice = data.get('choices', [{}])[0]
        message = choice.get('message', {})
        
        content = message.get('content', '')
        reasoning = message.get('reasoning_content', '')
        
        # 解析 tool_calls
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
        
    except urllib.error.HTTPError as e:
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
    reserve_ratio = 0.75
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
                sm = _summarize_chunk(chunk)
                compressed_old.append({'role': 'system', 'content': sm})
            i += chunk_size
        result.extend(compressed_old)
    else:
        if old:
            sm = _summarize_chunk(old)
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


class _SimpleEmbedding(EmbeddingFunction):
    def __call__(self, texts: Documents) -> Embeddings:
        embs = []
        for t in texts:
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

def _get_chroma_client(book_id):
    key = book_id
    if key not in _chroma_clients:
        db_dir = os.path.join(get_book_dir(book_id), '.vector_db')
        os.makedirs(db_dir, exist_ok=True)
        _chroma_clients[key] = chromadb.PersistentClient(path=db_dir)
    return _chroma_clients[key]

def _get_kb_collection(book_id):
    if book_id in _chroma_collections:
        return _chroma_collections[book_id]
    client = _get_chroma_client(book_id)
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
    tl_path = os.path.join(_get_source_dir(book_id), 'timeline.md')
    if os.path.exists(tl_path):
        _add_chunks('__timeline__', _read_entity_file(tl_path), section='timeline')
    rules_path = os.path.join(_get_source_dir(book_id), 'rules.md')
    if os.path.exists(rules_path):
        _add_chunks('__rules__', _read_entity_file(rules_path), section='rules')
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
    if budget_chars is None:
        settings = settings or get_settings()
        ctx_len = _get_effective_context_length(settings)
        budget_chars = int(ctx_len * 0.5) if ctx_len > 0 else 40000
    efiles = _list_entity_files(book_id)
    if not efiles:
        return '（目前还没有阅读笔记。运行「摘要全书」后 Luca 就能掌握全书内容了。）'
    return _assemble_smart_context_v2(book_id, user_query, budget_chars)

def _assemble_smart_context_v2(book_id, user_query, budget):
    reserved = 500
    budget = max(budget - reserved, 2000)
    pieces = []
    used = 0
    efiles = _list_entity_files(book_id)
    entity_names = [os.path.splitext(os.path.basename(f))[0] for f in efiles]
    search_results = None
    if user_query and user_query.strip():
        sr = _kb_search(book_id, user_query, top_k=min(len(efiles)+4, 15))
        if sr and sr.get('documents') and sr['documents'][0]:
            search_results = set(sr['metadatas'][0][i].get('entity','') for i in range(len(sr['documents'][0])) if i < len(sr['metadatas'][0]))
    priority_entities = []
    other_entities = []
    if search_results:
        for en in entity_names:
            if en in search_results:
                priority_entities.append(en)
            else:
                other_entities.append(en)
    else:
        other_entities = list(entity_names)
    ordered = priority_entities + other_entities
    per_entity_budget = max(int(budget * 0.6 / max(len(ordered), 1)), 200)
    for ename in ordered:
        if used >= budget * 0.7:
            break
        ep = _get_entity_path(book_id, ename)
        etext = _read_entity_file(ep)
        if not etext or not etext.strip():
            continue
        alloc = per_entity_budget
        if ename in (priority_entities or []):
            alloc = min(per_entity_budget * 2, budget - used)
        if len(etext) <= alloc:
            piece = etext
        else:
            sections = re.split(r'\n(?=### )', etext)
            piece = ''
            for sec in sections:
                if len(piece) + len(sec) > alloc:
                    remaining = alloc - len(piece)
                    if remaining > 50:
                        piece += sec[:remaining] + '\n...(截断)'
                    break
                piece += sec
        pieces.append(piece)
        used += len(piece)
    tl_path = os.path.join(sd, 'timeline.md')
    if os.path.exists(tl_path) and used < budget * 0.85:
        tlt = _read_entity_file(tl_path)
        tl_alloc = min(len(tlt), int(budget * 0.15))
        if tlt:
            pieces.append(f'\n## 时间线\n{tlt[:tl_alloc]}')
            used += tl_alloc
    fs_path = os.path.join(sd, 'foreshadowing.md')
    if os.path.exists(fs_path) and used < budget * 0.95:
        fst = _read_entity_file(fs_path)
        fs_alloc = min(len(fst), int(budget * 0.10))
        if fst:
            pieces.append(f'\n## 伏笔与线索\n{fst[:fs_alloc]}')
            used += fs_alloc
    result = '# 全书阅读笔记\n\n' + '\n\n---\n\n'.join(pieces)
    if len(result) > budget:
        result = result[:budget-3] + '\n...'
    return result

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
    ctx_len = 0
    presets = settings.get('provider_presets', [])
    idx = settings.get('active_provider_idx', 0)
    if 0 <= idx < len(presets):
        ctx_len = presets[idx].get('context_length', 0) or settings.get('model_context_length', 0) or 0
    source_text = get_source(book_id) or ''
    raw_source_tokens = _estimate_tokens(source_text)

    sd = _get_source_dir(book_id)
    efiles = _list_entity_files(book_id)
    entity_count = len(efiles)
    has_entities = entity_count > 0
    smart_ctx = ''
    if has_entities:
        budget = int(ctx_len * 0.5) if ctx_len > 0 else 40000
        smart_ctx = get_smart_context(book_id, user_query='', budget_chars=budget, settings=settings)
    smart_tokens = _estimate_tokens(smart_ctx) if smart_ctx else 0

    bd = get_book_dir(book_id)
    meta = load_json(os.path.join(bd, 'meta.json'), dict) if os.path.isdir(bd) else {}
    cid = meta.get('current_chapter_id', '')
    ch_content = ''
    if cid:
        cp = os.path.join(bd, 'chapters', f'{cid}.json')
        if os.path.exists(cp):
            ch_data = load_json(cp, dict)
            ch_content = ch_data.get('content', '') or ''
    ch_tokens = _estimate_tokens(ch_content)
    sys_template = """你是 Luca，一个为分析大量文字和世界观叙事设计的作家助理。根据接入模型的不同，你的性格可能有细微差别。用户正在写小说，你协助他完成创作。

当前时间：2025年01月01日 00:00

【重要】你已经在系统里看到了用户当前正在写的章节正文，不需要让用户再发一遍、复制粘贴或上传任何稿子。你直接就能看到他写了什么。

【说话方式】
谨言慎行。温文尔雅，不卑不亢。惜字如金。
不要列选项，不要反问，不要结构化分析。
看到好就简短说好，有问题就精准点出。不浮夸，也不冷漠。
避免用"呗""啦"结尾，显得轻浮。

【绝对禁止】
严禁任何身份描述。

这本小说大概是这样的：

{source}

此时，用户正在写最新的一章：

【章节名】章节名
【现有正文】

"""
    sys_tokens = _estimate_tokens(sys_template)
    annotate_tool = """你还有一个"荧光笔"工具，可以在正文中为用户标注重点内容。
- 添加标注格式：[ANNOTATE_ADD]{"chapter_id":"...","text":"...","note":"...","color":"yellow"}[/ANNOTATE_ADD]
- 删除标注格式：[ANNOTATE_REMOVE]{"text":"..."}}[/ANNOTATE_REMOVE]
- 可用颜色：yellow（默认）、green、pink、blue

你还有一个"本章写完"工具（隐藏功能）...

你还有一个"建议通读"工具...
"""
    tool_tokens = _estimate_tokens(annotate_tool)
    active_source_tokens = smart_tokens if has_entities else raw_source_tokens
    min_chat = sys_tokens + tool_tokens + active_source_tokens + ch_tokens + 500
    msg_file = os.path.join(os.path.join(bd, 'messages') if os.path.isdir(bd) else '', '')
    history_tokens = 0
    import glob as _glob
    for mf in _glob.glob(os.path.join(get_book_dir(book_id), 'messages', '*.json')):
        try:
            msgs = load_json(mf, list)
            for m in msgs:
                history_tokens += _estimate_tokens(str(m.get('text', '')))
        except: pass
    total_estimated = min_chat + history_tokens
    return {
        'model_context': ctx_len,
        'context_tokens': active_source_tokens,
        'chapter_tokens': ch_tokens,
        'history_tokens': history_tokens,
        'system_prompt_tokens': sys_tokens + tool_tokens,
        'min_chat_required': min_chat,
        'total_estimated': total_estimated,
        'needs_compression': ctx_len > 0 and total_estimated > ctx_len,
        'entity_count': entity_count,
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

def _do_chapter_complete(task_id, book_id, chapter_id, cfg_settings, text=None):
    """后台线程：单章通读，生成章节摘要并增量更新 source.md"""
    set_conn_meta('chapter-complete', '本章通读', book_id)
    log_action('CHAPTER_COMPLETE_START', f'book={book_id}, chapter={chapter_id}')
    try:
        bg_task_update(task_id, progress=5)
        cp = os.path.join(get_book_dir(book_id), 'chapters', f"{chapter_id}.json")
        if not os.path.exists(cp):
            log_action('CHAPTER_COMPLETE_ERROR', '章节不存在')
            bg_task_done(task_id, '章节不存在')
            return
        with open(cp, 'r', encoding='utf-8') as f:
            ch = json.load(f)
        title = ch.get('title', '未命名章节')
        # 优先使用前端传入的最新文本，否则读磁盘
        if text is not None:
            content = text
        else:
            content = ch.get('content', '')
        if _is_content_empty(content):
            log_action('CHAPTER_COMPLETE_SKIP', '章节无实质正文，跳过')
            bg_task_done(task_id, '章节无实质正文，跳过')
            return
        bg_task_update(task_id, progress=10)
        log_action('CHAPTER_COMPLETE_INFO', f'title={title}, content_len={len(content)}, model={cfg_settings.get("model","")}, base={cfg_settings.get("base_url","")}')
        # 构建前情索引
        current_source = get_source(book_id) or ''
        # 先把本章旧记录从 source 中剔除，避免 AI 被自己的旧摘要诱导
        chapter_id_marker = f'<!-- id:{chapter_id} -->'
        if chapter_id_marker in current_source:
            idx = current_source.find(chapter_id_marker)
            line_start = current_source.rfind('\n\n### ', 0, idx)
            if line_start != -1:
                next_idx = current_source.find('\n\n### ', idx)
                if next_idx == -1:
                    current_source = current_source[:line_start]
                else:
                    current_source = current_source[:line_start] + current_source[next_idx:]
        chapter_header = f"\n\n### {title}\n"
        if chapter_header in current_source:
            idx = current_source.find(chapter_header)
            next_idx = current_source.find('\n\n### ', idx + len(chapter_header))
            if next_idx == -1:
                current_source = current_source[:idx]
            else:
                current_source = current_source[:idx] + current_source[next_idx:]
        prev_context = _extract_context_summary(current_source)
        # 调用 AI 生成单章摘要
        result, err = _ai_read_chapter(cfg_settings, title, content, prev_context, config=None, on_token=None, should_stop_fn=lambda: bg_task_should_stop(task_id))
        log_action('CHAPTER_COMPLETE_AI_RESULT', f'err={err}, result_len={len(result) if result else 0}')
        if err:
            bg_task_done(task_id, err)
            return
        if not result or not result.strip():
            bg_task_done(task_id, 'AI 无输出')
            return
        bg_task_update(task_id, progress=70)
        save_chapter_summary(book_id, chapter_id, result)
        ch['readthrough'] = result
        ch['completed_at'] = time.time()
        save_json(cp, ch)

        source = get_source(book_id) or ''
        if not source.strip():
            source = '# 全书阅读笔记\n\n'
        ctx_len = _get_effective_context_length(cfg_settings)
        if ctx_len > 0 and len(source) > int(ctx_len * 0.45):
            target = int(ctx_len * 0.35)
            source = _ai_compress_source_for_context(source, target, cfg_settings, config=None)
            save_source(book_id, source)
            log_action('CHAPTER_COMPLETE_COMPRESS', f'compressed to {len(source)}')

        chapter_id_marker = f'<!-- id:{chapter_id} -->'
        if chapter_id_marker in source:
            idx = source.find(chapter_id_marker)
            line_start = source.rfind('\n\n### ', 0, idx)
            if line_start != -1:
                next_idx = source.find('\n\n### ', idx)
                if next_idx == -1:
                    source = source[:line_start]
                else:
                    source = source[:line_start] + source[next_idx:]
        # 再尝试按标题移除旧记录（兼容旧格式）
        chapter_header = f"\n\n### {title}\n"
        if chapter_header in source:
            idx = source.find(chapter_header)
            next_idx = source.find('\n\n### ', idx + len(chapter_header))
            if next_idx == -1:
                source = source[:idx]
            else:
                source = source[:idx] + source[next_idx:]
        # 追加新记录，带上 ID 注释方便以后按 ID 匹配
        source += f"\n\n### {title} <!-- id:{chapter_id} -->\n{result}"
        save_source(book_id, source)

        bg_task_update(task_id, progress=85)
        try:
            parsed = _parse_entities_from_notes(result)
            _save_parsed_entities_to_files(book_id, parsed)
            _rebuild_vector_index(book_id)
        except Exception as ex:
            log_action('CHAPTER_COMPLETE_KB_ERR', str(ex)[:100])

        outline = get_outline(book_id)
        summaries = outline.get('chapter_summaries', {})
        summaries[chapter_id] = result[:500] + ('...' if len(result) > 500 else '')
        outline['chapter_summaries'] = summaries
        outline['updated'] = time.time()
        save_json(os.path.join(get_book_dir(book_id), 'outline.json'), outline)
        bg_task_update(task_id, result=result, progress=100)
        bg_task_done(task_id)
        log_action('CHAPTER_COMPLETE_DONE', f'book={book_id}, chapter={chapter_id}')
    except Exception as e:
        log_action('CHAPTER_COMPLETE_EXCEPTION', str(e))
        bg_task_done(task_id, str(e))


def do_readthrough(bid, settings, config=None, resume=False):
    """后台线程：逐章通读，生成 source.md + 大纲"""
    set_conn_meta('readthrough', '摘要', bid)
    cfg = config or {}
    try:
        _rebuild_set(bid, status='running', progress=0, phase='准备中', total_chapters=0, done_chapters=0,
                     stream_buffer='', stream_status='', source='', logs=[], stopped=False)
        _rebuild_log(bid, '开始通读')
        meta = get_book_meta(bid) or {}
        order = meta.get('chapter_order', [])
        ch_dir = os.path.join(get_book_dir(bid), 'chapters')
        if not os.path.isdir(ch_dir) or not order:
            _rebuild_set(bid, status='error', progress=100, error='没有章节')
            return
        total = len(order)
        _rebuild_set(bid, total_chapters=total)
        _rebuild_log(bid, f'全书 {total} 章')

        cp_file = os.path.join(get_book_dir(bid), 'readthrough_checkpoint.json')
        notes = []
        done = set()
        current_source = ''

        if resume:
            # 从 check point 恢复
            cp = load_json(cp_file, dict)
            notes = cp.get('notes', [])
            done_list = cp.get('done', [])
            if done_list:
                # 续表时：重做最后一章（把最后一章从 done 和 notes 里移除，重新构建 source）
                last_done = done_list[-1]
                done_list = done_list[:-1]
                if notes:
                    notes = notes[:-1]
                _rebuild_log(bid, f'将继续通读，从第 {len(done_list) + 1} 章开始重做')
            done = set(done_list)
            # 从 notes 重建 source.md，确保不含待重做的章节
            current_source = '# 全书阅读笔记\n\n'
            for note in notes:
                current_source += '\n\n' + note
            save_source(bid, current_source)
        else:
            current_source = '# 全书阅读笔记\n\n'

        chapters = []
        for i, cid in enumerate(order):
            ch = _read_chapter_file(bid, cid)
            if ch:
                chapters.append({'idx': i, 'id': cid, 'title': ch.get('title', f'第{i+1}章'), 'content': ch.get('content', '')})

        pending = [c for c in chapters if c['id'] not in done]
        done_count = len(chapters) - len(pending)
        _rebuild_set(bid, phase='逐章阅读', done_chapters=done_count,
                     progress=5 + int(done_count / total * 70), source=current_source)

        # 读取用户设置的上下文长度（优先手动填写，0 表示关闭批量）
        context_window = _get_effective_context_length(settings)
        use_batch = False
        if isinstance(context_window, int) and context_window > 0:
            use_batch = True
            _rebuild_log(bid, f'使用批量模式，上下文限制: {context_window} tokens')
        else:
            _rebuild_log(bid, '未设置上下文长度，使用单章模式')

        def mk_handler():
            def h(tk):
                with _rebuild_lock:
                    t = _rebuild_tasks.get(bid, {})
                    t['stream_buffer'] = t.get('stream_buffer', '') + tk
            return h

        max_batch_failures = 2
        batch_failures = 0
        i = 0
        while i < len(pending):
            if _rebuild_should_stop(bid):
                _rebuild_log(bid, '用户停止，已保存进度')
                _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                return

            # 预检测：无实质正文的章节直接跳过，不调用 AI
            ch = pending[i]
            if _is_content_empty(ch['content']):
                _rebuild_log(bid, f'跳过空章节: {ch["title"]}')
                skip_result = f'## 剧情摘要\n[本章无实质正文，跳过]\n\n## 资料记录\n[无]\n'
                notes.append(skip_result)
                current_source += f'\n\n### {ch["title"]}\n{skip_result}'
                done.add(ch['id'])
                done_count += 1
                save_source(bid, current_source)
                pct = 5 + int(done_count / total * 70)
                _rebuild_set(bid, progress=pct, done_chapters=done_count, source=current_source, readthrough_chapter_idx=ch['idx'])
                save_json(cp_file, {'notes': notes, 'done': list(done), 'chapter_idx': ch['idx']})
                i += 1
                continue

            if use_batch:
                # 构建本批次：尽可能多地压入完整章节，不超过 70% 预算
                budget = int(context_window * 0.7)
                reserve = 3000  # 预留 prompt 模板 + 输出空间
                max_content = max(budget - reserve, 0)

                batch = []
                batch_tokens = 0
                while i < len(pending):
                    ch = pending[i]
                    ch_tokens = _estimate_tokens(ch['content'])
                    if batch and batch_tokens + ch_tokens > max_content:
                        # 已有至少一章，加入本章会超限，结束本批
                        break
                    # 即使单章超限，也至少压入一章（必须完整）
                    batch.append(ch)
                    batch_tokens += ch_tokens
                    i += 1

                if not batch:
                    # 保险：至少一章
                    ch = pending[i]
                    batch = [ch]
                    i += 1

                _rebuild_log(bid, f'批量处理 {len(batch)} 章: ' + ', '.join(c['title'] for c in batch))
                _rebuild_set(bid, stream_buffer='', stream_status=f'正在读: {batch[0]["title"]} 等 {len(batch)} 章')

                ctx = _extract_context_summary(current_source)
                max_retries = 3
                attempt = 0
                result = ''
                err = None
                while attempt < max_retries:
                    if _rebuild_should_stop(bid):
                        _rebuild_log(bid, '用户停止，已保存进度')
                        _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                        return
                    result, err = _ai_read_chapters_batch(settings, batch, ctx,
                                                           config=cfg, on_token=mk_handler(),
                                                           should_stop_fn=lambda: _rebuild_should_stop(bid))
                    if not err and result and result.strip():
                        break
                    attempt += 1
                    _rebuild_log(bid, f'批量第{attempt}次失败，重试中...')
                    _rebuild_set(bid, stream_buffer='', stream_status=f'批量第{attempt}次重试')
                    time.sleep(1)

                parsed = None
                if not err and result and result.strip():
                    parsed = _parse_batch_result(result, batch)

                if parsed and all(p.strip() for p in parsed):
                    for ch, note in zip(batch, parsed):
                        notes.append(note)
                        current_source += f'\n\n### {ch["title"]}\n{note}'
                        done.add(ch['id'])
                        done_count += 1
                        _rebuild_log(bid, f'完成 {ch["title"]} ({done_count}/{total})')
                else:
                    batch_failures += 1
                    _rebuild_log(bid, f'批量解析失败（第{batch_failures}次），回退到单章处理')
                    if batch_failures >= max_batch_failures:
                        use_batch = False
                        _rebuild_log(bid, '批量模式多次失败，后续使用单章模式')
                    # fallback 单章
                    for ch in batch:
                        if _rebuild_should_stop(bid):
                            _rebuild_log(bid, '用户停止，已保存进度')
                            _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                            return
                        ctx = _extract_context_summary(current_source)
                        _rebuild_set(bid, stream_buffer='', stream_status=f'正在读: {ch["title"]}')
                        max_retries = 3
                        attempt = 0
                        result = ''
                        err = None
                        while attempt < max_retries:
                            if _rebuild_should_stop(bid):
                                _rebuild_log(bid, '用户停止，已保存进度')
                                _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                                return
                            result, err = _ai_read_chapter(settings, ch['title'], ch['content'], ctx,
                                                            config=cfg, on_token=mk_handler(),
                                                            should_stop_fn=lambda: _rebuild_should_stop(bid))
                            if not err and result and result.strip():
                                break
                            attempt += 1
                            _rebuild_log(bid, f'{ch["title"]} 第{attempt}次失败，重试中...')
                            _rebuild_set(bid, stream_buffer='', stream_status=f'{ch["title"]} 第{attempt}次重试')
                            time.sleep(1)
                        if err or not result or not result.strip():
                            _rebuild_log(bid, f'失败: {ch["title"]} 重试{max_retries}次后仍无输出')
                            result = f'## {ch["title"]}\n[AI转述失败：本章无输出]\n'
                        notes.append(result)
                        current_source += f'\n\n### {ch["title"]}\n{result}'
                        save_source(bid, current_source)
                        done.add(ch['id'])
                        done_count += 1
                        pct = 5 + int(done_count / total * 70)
                        _rebuild_set(bid, progress=pct, done_chapters=done_count, source=current_source, readthrough_chapter_idx=ch['idx'])
                        save_json(cp_file, {'notes': notes, 'done': list(done), 'chapter_idx': ch['idx']})
                        _rebuild_log(bid, f'完成 {ch["title"]} ({done_count}/{total})')

                save_source(bid, current_source)
                pct = 5 + int(done_count / total * 70)
                _rebuild_set(bid, progress=pct, done_chapters=done_count, source=current_source, readthrough_chapter_idx=ch['idx'])
                save_json(cp_file, {'notes': notes, 'done': list(done), 'chapter_idx': ch['idx']})

                if _rebuild_should_stop(bid):
                    _rebuild_log(bid, '用户停止，已保存进度')
                    _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                    return
            else:
                # 单章模式
                ch = pending[i]
                i += 1
                _rebuild_log(bid, f'[{done_count+1}/{total}] {ch["title"]}')
                if _rebuild_should_stop(bid):
                    _rebuild_log(bid, '用户停止，已保存进度')
                    _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                    return

                ctx = _extract_context_summary(current_source)
                _rebuild_set(bid, stream_buffer='', stream_status=f'正在读: {ch["title"]}')

                max_retries = 3
                attempt = 0
                result = ''
                err = None
                while attempt < max_retries:
                    if _rebuild_should_stop(bid):
                        _rebuild_log(bid, '用户停止，已保存进度')
                        _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                        return
                    result, err = _ai_read_chapter(settings, ch['title'], ch['content'], ctx,
                                                    config=cfg, on_token=mk_handler(),
                                                    should_stop_fn=lambda: _rebuild_should_stop(bid))
                    if not err and result and result.strip():
                        break
                    attempt += 1
                    _rebuild_log(bid, f'{ch["title"]} 第{attempt}次失败，重试中...')
                    _rebuild_set(bid, stream_buffer='', stream_status=f'{ch["title"]} 第{attempt}次重试')
                    time.sleep(1)
                if err or not result or not result.strip():
                    _rebuild_log(bid, f'失败: {ch["title"]} 重试{max_retries}次后仍无输出')
                    result = f'## {ch["title"]}\n[AI转述失败：本章无输出]\n'

                notes.append(result)
                current_source += f'\n\n### {ch["title"]}\n{result}'
                save_source(bid, current_source)

                done.add(ch['id'])
                done_count += 1
                pct = 5 + int(done_count / total * 70)
                _rebuild_set(bid, progress=pct, done_chapters=done_count, source=current_source, readthrough_chapter_idx=ch['idx'])
                save_json(cp_file, {'notes': notes, 'done': list(done), 'chapter_idx': ch['idx']})
                _rebuild_log(bid, f'完成 {ch["title"]} ({done_count}/{total})')

                if _rebuild_should_stop(bid):
                    _rebuild_log(bid, '用户停止，已保存进度')
                    _rebuild_set(bid, status='stopped', progress=100, phase='已保存，可继续')
                    return

        if not notes:
            _rebuild_set(bid, status='error', progress=100, error='所有章节失败')
            return

        _rebuild_set(bid, phase='完成', progress=95, stream_buffer='')
        _rebuild_log(bid, '各章笔记已保存')

        ctx_len = _get_effective_context_length(settings)
        if len(current_source) > 500 and ctx_len > 0:
            target = int(ctx_len * 0.5)
            if len(current_source) > target:
                _rebuild_log(bid, f'压缩 source.md: {len(current_source)} -> 目标 {target}')
                current_source = _ai_compress_source_for_context(current_source, target, settings, config=config)
                save_source(bid, current_source)
                _rebuild_log(bid, f'压缩完成: {len(current_source)} 字符')

        _rebuild_set(bid, phase='提取实体', progress=96)
        _rebuild_log(bid, '正在从笔记中提取实体...')
        all_notes = '\n\n'.join(notes)
        parsed = _parse_entities_from_notes(all_notes)
        saved_count = _save_parsed_entities_to_files(bid, parsed)
        _rebuild_log(bid, f'已保存 {saved_count} 个实体段落到文件')
        timeline_text = _extract_timeline_from_notes(all_notes)
        if timeline_text.strip():
            tl_path = os.path.join(_get_source_dir(bid), 'timeline.md')
            _write_entity_file(tl_path, '# 时间线\n\n' + timeline_text)
            _rebuild_log(bid, '时间线已保存')
        fs_text = _extract_foreshadowing_from_notes(all_notes)
        if fs_text.strip():
            fs_path = os.path.join(_get_source_dir(bid), 'foreshadowing.md')
            _write_entity_file(fs_path, '# 伏笔与线索\n\n' + fs_text)
            _rebuild_log(bid, '伏笔追踪已保存')

        _rebuild_set(bid, phase='构建索引', progress=98)
        _rebuild_log(bid, '构建向量检索索引...')
        _rebuild_vector_index(bid)
        summary = _build_source_summary(bid)
        save_source(bid, summary)

        if os.path.exists(cp_file):
            os.remove(cp_file)
        _rebuild_log(bid, '通读完成')
        _rebuild_set(bid, status='done', progress=100, phase='完成', readthrough_chapter_idx=-1)
        meta = get_book_meta(bid) or {}
        meta['readthrough_at'] = time.time()
        save_json(os.path.join(get_book_dir(bid), 'meta.json'), meta)
    except Exception as e:
        import traceback
        err = f'通读崩溃: {str(e)[:200]}'
        _rebuild_log(bid, err)
        _rebuild_set(bid, status='error', progress=100, error=err + '\n' + traceback.format_exc()[-300:])


# 兼容别名：summary -> readthrough
def get_summary_config(bid):
    return get_readthrough_config(bid)

def save_summary_config(bid, cfg):
    return save_readthrough_config(bid, cfg)

def do_series_summary(sid, settings):
    return do_series_readthrough(sid, settings)

def do_summary(bid, settings, config=None, resume=False):
    return do_readthrough(bid, settings, config, resume)


def run():
    bind_host = '127.0.0.1'
    try:
        s = load_json(SETTINGS_FILE)
        scope = s.get('access_scope', '127.0.0.1')
        if scope in ('127.0.0.1', '0.0.0.0'):
            bind_host = scope
    except Exception:
        pass
    server = ThreadingHTTPServer((bind_host, PORT), Handler)
    print(f'Server running on http://{bind_host}:{PORT}')
    server.serve_forever()


if __name__ == '__main__':
    run()
