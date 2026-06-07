import os
import re
import json
import hashlib
import threading
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


class LocalEmbedding(EmbeddingBackend):
    def __init__(self, model_name='BAAI/bge-small-zh-v1.5'):
        self.model_name = model_name
        self.backend_id = f'local:{model_name}'
        self._model = None
        self._fallback = None
        self.dim = 0
        self._load_lock = threading.Lock()

    def _ensure_model(self):
        if self._model is not None or self._fallback is not None:
            return
        with self._load_lock:
            if self._model is not None or self._fallback is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                self._fallback = HashEmbedding()
                self.backend_id = self._fallback.backend_id
                self.dim = self._fallback.dim
                return
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_dir = os.path.join(root_dir, 'models_cache')
            os.makedirs(cache_dir, exist_ok=True)
            try:
                device = _pick_embedding_device()
                # 优先使用 bundled 模型路径（打包时内置），无需联网
                # 优先使用 bundled 模型路径
                model_path = self.model_name
                _local_name = self.model_name.replace('/', '_').replace('\\', '_')
                builtin_path = os.path.join(root_dir, 'builtin', 'models', _local_name)
                if os.path.isdir(builtin_path):
                    model_path = builtin_path
                kwargs = {'cache_folder': cache_dir}
                if device:
                    kwargs['device'] = device
                self._model = SentenceTransformer(model_path, **kwargs)
                try:
                    self.dim = self._model.get_embedding_dimension()
                except AttributeError:
                    self.dim = self._model.get_sentence_embedding_dimension()
            except Exception:
                self._fallback = HashEmbedding()
                self.backend_id = self._fallback.backend_id
                self.dim = self._fallback.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        if not texts:
            return []
        if self._fallback is not None:
            return self._fallback.embed(texts)
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return vecs.tolist()


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
