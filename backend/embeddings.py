import os
import re
import hashlib
import numpy as np
from abc import ABC, abstractmethod


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

    def _ensure_model(self):
        if self._model is not None or self._fallback is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self._fallback = HashEmbedding()
            self.backend_id = self._fallback.backend_id
            self.dim = self._fallback.dim
            return
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'usrdata', 'models'
        )
        os.makedirs(cache_dir, exist_ok=True)
        try:
            self._model = SentenceTransformer(self.model_name, cache_folder=cache_dir)
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
            from main import _get_ssl_context
            ctx = _get_ssl_context()
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            raise RuntimeError(f'API 嵌入调用失败: {e}')
        data = result.get('data', [])
        data.sort(key=lambda x: x.get('index', 0))
        vecs = [d['embedding'] for d in data]
        if vecs and not self.dim:
            self.dim = len(vecs[0])
        return vecs


def get_embedding_backend(settings):
    choice = settings.get('embedding_backend', 'local')
    if choice == 'api':
        return APIEmbedding(
            base_url=settings.get('base_url', ''),
            api_key=settings.get('api_key', ''),
            model=settings.get('embedding_model', 'text-embedding-3-small'),
        )
    return LocalEmbedding(
        model_name=settings.get('local_embedding_model', 'BAAI/bge-small-zh-v1.5'),
    )
