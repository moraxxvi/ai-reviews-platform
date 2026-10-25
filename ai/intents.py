"""Распознавание намерений (intent) и извлечение сущностей (NER) - метод ИИ №3.

Намерения: подбор, вопрос_о_товаре, сравнение, доставка_возврат, поддержка, приветствие.
Классификатор - TF-IDF + логистическая регрессия (опорное решение из СРО2, п. 2.4).
Сущности извлекаются правилами и словарями: категория, бренд, бюджет, аспекты, упомянутые товары.
"""
import os
import re
import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline, make_union

from .lexicon import BRANDS, BRAND_ALIASES, CATEGORIES, match_aspects
from .preprocess import clean

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
INTENT_PATH = os.path.join(MODEL_DIR, "intent_clf.joblib")
CONF_THRESHOLD = 0.45  # ниже порога консультант задаёт уточняющий вопрос (СРО2, п. 2.8)

_OPERATOR = re.compile(r"оператор|живо\w* (?:человек|сотрудник)|с человеком|менеджер|претензи|жалоб")


def train_intents(texts, labels, save=True, seed=42):
    X = [clean(t) for t in texts]
    Xtr, Xte, ytr, yte = train_test_split(X, labels, test_size=0.2, random_state=seed, stratify=labels)

    def build():
        return make_pipeline(
            make_union(TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
                       TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)),
            LogisticRegression(max_iter=2000, C=6.0, class_weight="balanced"),
        )

    pred = build().fit(Xtr, ytr).predict(Xte)
    metrics = {"macro_f1": float(f1_score(yte, pred, average="macro")), "accuracy": float((np.array(pred) == np.array(yte)).mean()),
               "n_train": len(Xtr), "n_test": len(Xte)}
    model = build().fit(X, labels)
    if save:
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(model, INTENT_PATH)
    return model, metrics


class IntentClassifier:
    def __init__(self):
        if not os.path.exists(INTENT_PATH):
            raise RuntimeError("Классификатор намерений не обучен: выполните `python -m scripts.train`")
        self.model = joblib.load(INTENT_PATH)

    def predict(self, text):
        t = clean(text)
        if _OPERATOR.search(t):  # правило безопасности: явная просьба об операторе
            return "поддержка", 1.0
        probs = self.model.predict_proba([t])[0]
        i = int(probs.argmax())
        return str(self.model.classes_[i]), float(probs[i])


# ---------------- NER ----------------
_GENERIC_WEIGHT = {"pro": 0.3, "air": 0.5, "slim": 0.5, "gb": 0.0, "oled": 0.2, "fe": 0.8, "max": 0.3, "plus": 0.3}


def extract_budget(text):
    """Бюджет в тенге: '300 000', '300к', '300 тыс', 'до 1.5 млн'."""
    t = clean(text)
    best = None
    for m in re.finditer(r"(\d[\d ]*(?:[.,]\d+)?)\s*(млн|миллион\w*|тыс\w*|к(?![а-я])|k(?![a-z]))?", t):
        raw = m.group(1).replace(" ", "").replace(",", ".")
        try:
            val = float(raw)
        except ValueError:
            continue
        unit = m.group(2) or ""
        if unit.startswith(("млн", "миллион")):
            val *= 1_000_000
        elif unit.startswith(("тыс", "к", "k")):
            val *= 1_000
        if val >= 10_000:  # цены меньше 10 000 ₸ - это скорее номер модели/размер
            best = int(val)
    return best


def extract_aspects(text):
    return match_aspects(clean(text))


def extract_category(text):
    t = clean(text)
    for cat, words in CATEGORIES.items():
        if any(w in t for w in words):
            return cat
    return None


def extract_brand(text):
    t = clean(text)
    for b in BRANDS:
        if b in t:
            return b
    for alias, b in BRAND_ALIASES.items():
        if alias in t:
            return b
    return None


def match_products_scored(text, products):
    """Возвращает [(product_id, вес совпадения)] по убыванию. Товар найден, если вес >= 1."""
    q = set(clean(text).split())
    scored = []
    for p in products:
        brand_tokens = set(clean(p.get("brand") or "").split())
        w = 0.0
        for tok in clean(p["title"]).split():
            if tok in brand_tokens or tok not in q:
                continue
            if tok in _GENERIC_WEIGHT:
                w += _GENERIC_WEIGHT[tok]
            elif tok.isdigit():
                w += 0.5
            else:
                w += 1.0
        if w >= 1.0:
            scored.append((p["product_id"], w))
    scored.sort(key=lambda t: -t[1])
    return scored


def match_products(text, products):
    return [pid for pid, _ in match_products_scored(text, products)]


def extract_entities(text, products):
    return {
        "category": extract_category(text),
        "brand": extract_brand(text),
        "budget": extract_budget(text),
        "aspects": extract_aspects(text),
        "products": match_products(text, products),
        "products_scored": match_products_scored(text, products),
    }
