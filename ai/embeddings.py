"""Векторные представления текста для семантического поиска - метод ИИ №2.

Бэкенды:
  * tfidf_svd            - символьные n-граммы TF-IDF + TruncatedSVD (LSA), плотный вектор 128-мерный.
                           Работает без скачивания весов.
  * sentence_transformers - модель sentence-transformers (если установлена и скачана),
                           включается переменной EMBEDDER=sentence_transformers.
Косинусная близость - формула (2) из СРО2: sim(q, d) = q·d / (|q||d|); векторы нормируются,
поэтому достаточно скалярного произведения.
"""
import os
import joblib
import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import make_pipeline

from .preprocess import clean

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
EMB_PATH = os.path.join(MODEL_DIR, "embedder_tfidf_svd.joblib")
ST_NAME = os.environ.get("ST_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")


def _norm(m):
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


class Embedder:
    def __init__(self, backend=None):
        want = backend or os.environ.get("EMBEDDER", "tfidf_svd")
        self.backend, self._st, self._pipe = None, None, None
        if want == "sentence_transformers":  # pragma: no cover - нужен пакет и веса
            try:
                from sentence_transformers import SentenceTransformer

                self._st = SentenceTransformer(ST_NAME)
                self.backend = "sentence_transformers"
            except Exception as exc:
                print(f"[embeddings] sentence-transformers недоступен ({exc}); используется tfidf_svd")
        if self.backend is None:
            self.backend = "tfidf_svd"
            if os.path.exists(EMB_PATH):
                self._pipe = joblib.load(EMB_PATH)

    @property
    def ready(self):
        return self._st is not None or self._pipe is not None

    def fit(self, texts, dim=128):
        if self._st is not None:  # предобученная модель в обучении не нуждается
            return self
        X = [clean(t) for t in texts]
        dim = max(2, min(dim, len(X) - 1))
        self._pipe = make_pipeline(
            TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=2),
            TruncatedSVD(n_components=dim, random_state=0),
        ).fit(X)
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(self._pipe, EMB_PATH)
        return self

    def encode(self, texts):
        texts = [clean(t) for t in texts]
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        if self._st is not None:  # pragma: no cover
            return _norm(np.asarray(self._st.encode(texts)))
        if self._pipe is None:
            raise RuntimeError("Эмбеддер не обучен: выполните `python -m scripts.train`")
        return _norm(self._pipe.transform(texts))


def to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob):
    return np.frombuffer(blob, dtype=np.float32)
