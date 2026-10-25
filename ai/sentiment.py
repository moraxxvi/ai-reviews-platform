"""Тональность и аспектный анализ (ABSA) - метод ИИ №1.

Схема (как в СРО2, п. 2.4): отзыв -> клаузы -> для каждой клаузы находим аспекты (словарь)
и определяем тональность клаузы классификатором.

Бэкенды классификатора:
  * baseline  - TF-IDF (слова 1-2 + символьные n-граммы) + логистическая регрессия.
                Это "опорная модель" из СРО1; работает без GPU и без скачивания весов.
  * rubert    - дообученный RuBERT (Hugging Face Transformers). Подключается, если есть
                каталог models/rubert_sentiment (его создаёт scripts/train_rubert.py).
"""
import os
import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline, make_union
from .rules import rule_sentiment

from .lexicon import match_aspects
from .preprocess import clean, split_clauses, emoji_tone

LABELS = ["negative", "neutral", "positive"]
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
BASELINE_PATH = os.path.join(MODEL_DIR, "sentiment_baseline.joblib")
RUBERT_DIR = os.path.join(MODEL_DIR, "rubert_sentiment")


def build_baseline():
    feats = make_union(
        TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1, sublinear_tf=True),
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True),
    )
    return make_pipeline(feats, LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced"))


def train_baseline(texts, labels, test_size=0.2, seed=42, save=True):
    """Обучает опорную модель, возвращает (модель, метрики на отложенной выборке)."""
    X = [clean(t) for t in texts]
    Xtr, Xte, ytr, yte = train_test_split(X, labels, test_size=test_size, random_state=seed, stratify=labels)
    model = build_baseline().fit(Xtr, ytr)
    pred = model.predict(Xte)
    metrics = {
        "macro_f1": float(f1_score(yte, pred, average="macro")),
        "accuracy": float((np.array(pred) == np.array(yte)).mean()),
        "report": classification_report(yte, pred, digits=3, zero_division=0),
        "n_train": len(Xtr),
        "n_test": len(Xte),
    }
    if save:
        os.makedirs(MODEL_DIR, exist_ok=True)
        # финальная модель обучается на всей выборке
        model = build_baseline().fit(X, labels)
        joblib.dump(model, BASELINE_PATH)
    return model, metrics


class SentimentModel:
    """Единый интерфейс: predict_proba(list[str]) -> ndarray [n, 3] в порядке LABELS."""

    def __init__(self, backend=None):
        self.backend = None
        self._clf = None
        self._hf = None
        want = backend or os.environ.get("SENTIMENT_BACKEND", "auto")
        if want in ("rubert", "auto") and os.path.isdir(RUBERT_DIR):
            try:
                self._load_rubert()
                self.backend = "rubert"
            except Exception as exc:  # нет torch/transformers - откат на опорную модель
                print(f"[sentiment] RuBERT недоступен ({exc}); используется baseline")
        if self.backend is None:
            if not os.path.exists(BASELINE_PATH):
                raise RuntimeError("Модель не обучена: выполните `python -m scripts.train`")
            self._clf = joblib.load(BASELINE_PATH)
            self.backend = "baseline"

    @property
    def name(self):
        return "RuBERT (fine-tuned)" if self.backend == "rubert" else "TF-IDF + LogReg (baseline)"

    def _load_rubert(self):  # pragma: no cover - требует torch и весов
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._tok = AutoTokenizer.from_pretrained(RUBERT_DIR)
        self._hf = AutoModelForSequenceClassification.from_pretrained(RUBERT_DIR).eval()
        self._torch = torch
        id2label = {int(k): v for k, v in self._hf.config.id2label.items()}
        self._order = [id2label[i] for i in range(len(id2label))]

    def predict_proba(self, texts):
        texts = [clean(t) for t in texts]
        if not texts:
            return np.zeros((0, 3))
        if self.backend == "rubert":  # pragma: no cover
            out = []
            with self._torch.no_grad():
                for i in range(0, len(texts), 32):
                    enc = self._tok(texts[i:i + 32], padding=True, truncation=True, max_length=128, return_tensors="pt")
                    out.append(self._torch.softmax(self._hf(**enc).logits, dim=-1).numpy())
            p = np.vstack(out)
            idx = [self._order.index(l) for l in LABELS]
            return p[:, idx]
        p = self._clf.predict_proba(texts)
        idx = [list(self._clf.classes_).index(l) for l in LABELS]
        return p[:, idx]

    def predict(self, texts):
        p = self.predict_proba(texts)
        return [LABELS[i] for i in p.argmax(axis=1)], p.max(axis=1)


def find_aspects(clause):
    """Аспекты, упомянутые в клаузе (по словарю основ)."""
    return match_aspects(clean(clause))


def analyze_review(model: SentimentModel, text: str):
    """ABSA: возвращает {'aspects': [{aspect, sentiment, confidence, clause}], 'overall': label}.

    Для клауз без найденного аспекта тональность учитывается только в общей оценке.
    """
    clauses = split_clauses(text)
    if not clauses:
        return {"aspects": [], "overall": "neutral", "overall_score": 0.0}
    labels, conf = model.predict(clauses)
    labels, conf = list(labels), list(conf)
    for i, c in enumerate(clauses):  # правила жалоб и отрицаний поверх модели (ai/rules.py)
        r = rule_sentiment(c)
        if r:
            labels[i], conf[i] = r, max(float(conf[i]), 0.85)
    aspects, score = [], 0.0
    for clause, lab, cf in zip(clauses, labels, conf):
        score += {"positive": 1, "negative": -1, "neutral": 0}[lab] * float(cf)
        for a in find_aspects(clause):
            if lab != "neutral":  # нейтральные упоминания не влияют на долю положительных
                aspects.append({"aspect": a, "sentiment": lab, "confidence": round(float(cf), 3), "clause": clause})
    score += 0.5 * emoji_tone(text)
    overall = "positive" if score > 0.25 else "negative" if score < -0.25 else "neutral"
    return {"aspects": aspects, "overall": overall, "overall_score": round(score, 3)}
