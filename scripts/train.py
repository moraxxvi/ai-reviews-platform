"""Обучение опорных моделей: тональность, намерения, эмбеддер.

Использование:
    python -m scripts.train                         # демо-корпус (синтетический!)
    python -m scripts.train --csv data/reviews.csv  # реальный датасет: колонки text, label (positive/neutral/negative)
Результаты метрик пишутся в models/metrics.json - их можно переносить в главу 3 отчёта.
"""
import argparse
import json
import os

from ai import intents as intents_mod
from ai import sentiment as sentiment_mod
from ai.embeddings import Embedder
from data import demo_corpus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_csv(path):
    """CSV: текст (text/review) + метка (label/sentiment) ИЛИ оценка в звёздах (rating/score/stars 1-5)."""
    import pandas as pd

    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    tcol = cols.get("text") or cols.get("review") or df.columns[0]
    lcol = cols.get("label") or cols.get("sentiment")
    rcol = cols.get("rating") or cols.get("score") or cols.get("stars")
    if lcol is not None:
        df = df[[tcol, lcol]].dropna()
        df[lcol] = df[lcol].astype(str).str.lower().str.strip().map({"positive": "positive", "pos": "positive", "1": "positive", "negative": "negative", "neg": "negative", "-1": "negative", "neutral": "neutral", "0": "neutral"})
    elif rcol is not None:
        df = df[[tcol, rcol]].dropna()
        lcol = "_label"
        r = pd.to_numeric(df[rcol], errors="coerce")
        df[lcol] = r.map(lambda v: None if pd.isna(v) else ("negative" if v <= 2 else "neutral" if v == 3 else "positive"))
    else:
        raise SystemExit("В CSV нужна колонка label/sentiment (positive/neutral/negative) или rating (1-5)")
    df = df.dropna()
    print("Распределение классов:", df[lcol].value_counts().to_dict())
    return df[tcol].astype(str).tolist(), df[lcol].tolist()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="реальный датасет отзывов")
    ap.add_argument("--max-samples", type=int, default=60000, help="ограничение размера выборки (случайная, seed=42)")
    args = ap.parse_args(argv)

    if args.csv:
        X, y = load_csv(args.csv)
        if len(X) > args.max_samples:
            import random
            idx = random.Random(42).sample(range(len(X)), args.max_samples)
            X, y = [X[i] for i in idx], [y[i] for i in idx]
            print(f"Взята случайная выборка: {len(X)} примеров (параметр --max-samples)")
        source = f"реальный датасет: {args.csv} (выборка {len(X)})"
    else:
        X, y = demo_corpus.gen_sentiment_clauses(3000)
        source = "ДЕМО (синтетические шаблонные клаузы)"
    print(f"Источник данных для тональности: {source}; примеров: {len(X)}")
    _, m_sent = sentiment_mod.train_baseline(X, y)
    print("Тональность: macro-F1 = %.3f, accuracy = %.3f" % (m_sent["macro_f1"], m_sent["accuracy"]))
    print(m_sent["report"])

    Xi, yi = demo_corpus.gen_intents(120)
    _, m_int = intents_mod.train_intents(Xi, yi)
    print("Намерения: macro-F1 = %.3f, accuracy = %.3f (демо-шаблоны)" % (m_int["macro_f1"], m_int["accuracy"]))

    # эмбеддер учится на текстах отзывов; для демо - сгенерированные отзывы из seed
    from scripts.seed_db import build_review_texts

    texts = X[:20000] if args.csv else build_review_texts()
    Embedder(backend="tfidf_svd").fit(texts)
    print("Эмбеддер обучен на %d текстах" % len(texts))

    os.makedirs(os.path.join(ROOT, "models"), exist_ok=True)
    with open(os.path.join(ROOT, "models", "metrics.json"), "w", encoding="utf-8") as f:
        json.dump({"data_source": source, "sentiment": {k: v for k, v in m_sent.items() if k != "report"}, "intents": m_int}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
