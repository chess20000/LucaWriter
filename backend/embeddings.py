import os
import re
import json
import hashlib
import threading
import multiprocessing
import numpy as np
from abc import ABC, abstractmethod


def _pick_embedding_device():
    """根据本地模型策略决定嵌入模型放 CPU 还是 GPU。
    仅在 Tier A (9B) + CUDA + 显存 ≤8.5GB（9B 模型贴边塞 8GB 显卡的情况）强制 CPU，
    把 ~300MB 显存让给 LLM（用户报告里 LLM 自己就吃掉 7.9/8GB）。
    其它情况返回 None 让 SentenceTransformer 自己选（一般是 GPU 优先）。"""
    try:
        data_dir = os.environ.get('DATA_DIR') or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'usrdata'
        )
        strategy_file = os.path.join(data_dir, 'local_strategy.json')
        if not os.path.exists(strategy_file):
            return None
        with open(strategy_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        hw = data.get('hardware') or {}
        strat = data.get('strategy') or {}
        if (strat.get('tier') == 'A'
                and strat.get('binary') == 'cuda'
                and float(hw.get('vram_gb') or 0) <= 8.5):
            return 'cpu'
    except Exception:
        pass
    return None


class EmbeddingBackend(ABC):
    backend_id: str = ''
    dim: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        pass


class _ModelUnavailable(Exception):
    """子进程报告模型无法加载（未安装 sentence_transformers / 加载报错）。"""


def _resolve_local_model_path(model_name):
    """优先用 bundled 内置模型路径（打包时内置，无需联网），否则原样返回模型名。"""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _local_name = model_name.replace('/', '_').replace('\\', '_')
    builtin_path = os.path.join(root_dir, 'builtin', 'models', _local_name)
    if os.path.isdir(builtin_path):
        return builtin_path, root_dir
    return model_name, root_dir


def _embedding_worker_main(conn, model_name):
    """嵌入子进程入口：唯一加载 torch / SentenceTransformer 的地方。
    放进独立进程后，torch 的原生崩溃（段错误/访问违例）只会杀死本子进程，
    主服务进程毫发无伤，下次调用会自动重启本子进程。

    协议（父子通过 Pipe 通信）：
      启动后立刻回握手 ('ready', dim) 或 ('nomodel', err)；
      之后每收到一个 list[str] 就回 ('ok', vecs) 或 ('err', msg)；收到 None 退出。"""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:
        try: conn.send(('nomodel', f'sentence_transformers 不可用: {e}'))
        except Exception: pass
        return
    try:
        model_path, root_dir = _resolve_local_model_path(model_name)
        cache_dir = os.path.join(root_dir, 'models_cache')
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass
        kwargs = {'cache_folder': cache_dir}
        device = _pick_embedding_device()
        if device:
            kwargs['device'] = device
        model = SentenceTransformer(model_path, **kwargs)
        try:
            dim = model.get_embedding_dimension()
        except AttributeError:
            dim = model.get_sentence_embedding_dimension()
        conn.send(('ready', int(dim or 0)))
    except Exception as e:
        try: conn.send(('nomodel', f'模型加载失败: {e}'))
        except Exception: pass
        return
    while True:
        try:
            req = conn.recv()
        except EOFError:
            break
        if req is None:
            break
        try:
            vecs = model.encode(req, normalize_embeddings=True, show_progress_bar=False)
            conn.send(('ok', vecs.tolist()))
        except Exception as e:
            try: conn.send(('err', str(e)))
            except Exception: break


class LocalEmbedding(EmbeddingBackend):
    """本地嵌入后端。模型跑在隔离子进程里，崩溃不波及主进程。
    embed() 对外行为与原来一致：模型不可用时永久退化到 HashEmbedding；
    向量化报错照旧抛异常（但因隔离，主进程不会被原生崩溃带走）。"""

    _LOAD_TIMEOUT = 180   # 首次加载（含子进程冷启动 + 模型载入）的握手超时
    _ENCODE_TIMEOUT = 120  # 单批向量化超时

    def __init__(self, model_name='BAAI/bge-small-zh-v1.5'):
        self.model_name = model_name
        self.backend_id = f'local:{model_name}'
        self.dim = 0
        self._fallback = None          # HashEmbedding：模型不可用时的永久兜底
        self._proc = None
        self._conn = None
        self._ctx = None
        self._worker_ever_ready = False
        self._lock = threading.Lock()

    def _degrade_to_hash(self):
        if self._fallback is None:
            self._fallback = HashEmbedding()
            self.backend_id = self._fallback.backend_id
            self.dim = self._fallback.dim
        return self._fallback

    def _kill_worker(self):
        proc, conn = self._proc, self._conn
        self._proc = None
        self._conn = None
        if conn is not None:
            try: conn.close()
            except Exception: pass
        if proc is not None:
            try:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=3)
            except Exception:
                pass
            try:
                if proc.is_alive():
                    proc.kill()
            except Exception:
                pass

    def _ensure_worker(self):
        """确保子进程在跑且已就绪。失败时抛 _ModelUnavailable（模型加载不了）
        或其它异常（启动/握手崩溃，由调用方决定重试还是兜底）。"""
        if self._proc is not None and self._proc.is_alive() and self._conn is not None:
            return
        self._kill_worker()
        if self._ctx is None:
            # 强制 spawn：主服务多线程，fork 之后用 torch 很容易死锁/崩溃
            self._ctx = multiprocessing.get_context('spawn')
        parent_conn, child_conn = self._ctx.Pipe()
        proc = self._ctx.Process(
            target=_embedding_worker_main,
            args=(child_conn, self.model_name),
            daemon=True,
        )
        proc.start()
        child_conn.close()  # 父进程只持有 parent_conn，关掉子端才能正确检测 EOF
        try:
            if not parent_conn.poll(self._LOAD_TIMEOUT):
                raise TimeoutError('嵌入子进程加载超时')
            kind, payload = parent_conn.recv()
        except Exception:
            self._proc, self._conn = proc, parent_conn
            self._kill_worker()
            raise
        if kind == 'ready':
            self._proc = proc
            self._conn = parent_conn
            self._worker_ever_ready = True
            if payload:
                self.dim = payload
            return
        # 'nomodel' 或意外 → 模型加载不了
        self._proc, self._conn = proc, parent_conn
        self._kill_worker()
        raise _ModelUnavailable(str(payload))

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            if self._fallback is not None:
                return self._fallback.embed(texts)
            last_err = None
            for _attempt in range(2):
                try:
                    self._ensure_worker()
                except _ModelUnavailable:
                    return self._degrade_to_hash().embed(texts)
                except Exception as e:
                    last_err = e
                    self._kill_worker()
                    # 从未成功加载过 → 当作加载失败，永久退化 hash（与原 load 失败一致）
                    if not self._worker_ever_ready:
                        return self._degrade_to_hash().embed(texts)
                    continue
                try:
                    self._conn.send(texts)
                    if not self._conn.poll(self._ENCODE_TIMEOUT):
                        raise TimeoutError('嵌入子进程响应超时')
                    kind, payload = self._conn.recv()
                except (EOFError, BrokenPipeError, ConnectionResetError, OSError, TimeoutError) as e:
                    # 子进程崩溃/卡死 → 杀掉，下一轮重启重试
                    last_err = e
                    self._kill_worker()
                    continue
                if kind == 'ok':
                    return payload
                if kind == 'err':
                    # 子进程内向量化报错（非崩溃）→ 照旧抛出，语义同原 in-process encode
                    raise RuntimeError(payload)
                raise RuntimeError(f'嵌入子进程返回异常: {kind!r}')
            raise RuntimeError(f'本地嵌入子进程多次崩溃，已放弃本次请求: {last_err}')


class HashEmbedding(EmbeddingBackend):
    def __init__(self, dim=384):
        self.backend_id = f'fallback:hash-ngram-{dim}'
        self.dim = dim

    def _features(self, text):
        text = (text or '').lower()
        tokens = re.findall(r'[\u4e00-\u9fff]|[a-z0-9_]+', text)
        feats = []
        feats.extend(tokens)
        for i in range(len(tokens) - 1):
            feats.append(tokens[i] + tokens[i + 1])
        for i in range(len(tokens) - 2):
            feats.append(tokens[i] + tokens[i + 1] + tokens[i + 2])
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for feat in self._features(text):
                digest = hashlib.blake2b(feat.encode('utf-8'), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], 'little') % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vec[idx] += sign
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
            vectors.append(vec.tolist())
        return vectors


class APIEmbedding(EmbeddingBackend):
    def __init__(self, base_url, api_key, model='text-embedding-3-small'):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.backend_id = f'api:{model}'
        self.dim = 1536 if model == 'text-embedding-3-small' else 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import urllib.request
        import json
        url = f'{self.base_url}/embeddings'
        body = json.dumps({
            'model': self.model,
            'input': texts,
        }).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            from main import _get_ssl_context, register_ai_connection, unregister_ai_connection
        except Exception:
            _get_ssl_context = lambda: None
            register_ai_connection = lambda *a, **kw: None
            unregister_ai_connection = lambda *a, **kw: None
        tid = threading.get_ident()
        ctx = None
        try:
            ctx = _get_ssl_context()
        except Exception:
            pass
        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=60)
            register_ai_connection(tid, resp)
            try:
                result = json.loads(resp.read().decode('utf-8'))
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
                unregister_ai_connection(tid)
        except Exception as e:
            raise RuntimeError(f'API 嵌入调用失败: {e}') from e
        data = result.get('data', [])
        data.sort(key=lambda x: x.get('index', 0))
        vecs = [d['embedding'] for d in data]
        if vecs and not self.dim:
            self.dim = len(vecs[0])
        return vecs


_BACKEND_CACHE = {}
_BACKEND_CACHE_LOCK = threading.Lock()


def get_embedding_backend(settings):
    """进程级缓存：相同 backend 配置返回同一实例，避免每次重新加载模型/重建 HTTP 客户端。
    本地嵌入模型（SentenceTransformer）首次加载约 1-2 秒，缓存后后续调用仅做向量化。"""
    choice = settings.get('embedding_backend', 'local')
    if choice == 'api':
        key = (
            'api',
            settings.get('base_url', ''),
            settings.get('api_key', ''),
            settings.get('embedding_model', 'text-embedding-3-small'),
        )
    else:
        key = ('local', settings.get('local_embedding_model', 'BAAI/bge-small-zh-v1.5'))

    cached = _BACKEND_CACHE.get(key)
    if cached is not None:
        return cached

    with _BACKEND_CACHE_LOCK:
        cached = _BACKEND_CACHE.get(key)
        if cached is not None:
            return cached
        if key[0] == 'api':
            be = APIEmbedding(
                base_url=settings.get('base_url', ''),
                api_key=settings.get('api_key', ''),
                model=key[3],
            )
        else:
            be = LocalEmbedding(model_name=key[1])
        _BACKEND_CACHE[key] = be
        return be
