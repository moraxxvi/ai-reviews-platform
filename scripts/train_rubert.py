"""Дообучение RuBERT для классификации тональности (метод ИИ №1, трансформерный вариант).

!!! НЕ ПРОВЕРЕНО в среде разработки: там нет доступа к Hugging Face и к torch. Скрипт написан по
стандартной схеме дообучения; запускать его нужно на вашем компьютере, ошибки (версии библиотек,
нехватка памяти) исправлять по месту. Результаты, которые он выведет, - ваши реальные цифры для главы 3.

Подготовка:   pip install torch transformers
Запуск:       python -m scripts.train_rubert --csv data/reviews.csv --epochs 2 --max-samples 6000
CSV: колонки text/review и label/sentiment со значениями positive / neutral / negative.
Результат:    models/rubert_sentiment/ (его автоматически подхватит ai/sentiment.py)
              models/metrics_rubert.json (macro-F1 и accuracy на отложенной выборке)
Сравнение с опорной моделью: запустите `python -m scripts.train --csv ...` на тех же данных
и сравните macro-F1 из models/metrics.json и models/metrics_rubert.json (разбиение одинаковое: seed=42, 20%).
"""
import argparse
import json
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS = ["negative", "neutral", "positive"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--model", default="DeepPavlov/rubert-base-cased-conversational")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-samples", type=int, default=6000, help="ограничение размера выборки (для ноутбука без GPU)")
    args = ap.parse_args()

    import numpy as np
    import torch
    from sklearn.metrics import f1_score
    from sklearn.model_selection import train_test_split
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from ai.preprocess import clean
    from scripts.train import load_csv

    X, y = load_csv(args.csv)
    X = [clean(t) for t in X]
    if len(X) > args.max_samples:
        idx = random.Random(42).sample(range(len(X)), args.max_samples)
        X, y = [X[i] for i in idx], [y[i] for i in idx]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    l2i = {l: i for i, l in enumerate(LABELS)}

    device = "cuda" if torch.cuda.is_available() else "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
    print("Устройство:", device, "| train:", len(Xtr), "| test:", len(Xte))
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model, num_labels=3, id2label={i: l for l, i in l2i.items()}, label2id=l2i).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)

    def batches(texts, labels, bs, shuffle):
        order = list(range(len(texts)))
        if shuffle:
            random.shuffle(order)
        for i in range(0, len(order), bs):
            b = order[i:i + bs]
            enc = tok([texts[j] for j in b], padding=True, truncation=True, max_length=128, return_tensors="pt")
            yield {k: v.to(device) for k, v in enc.items()}, torch.tensor([l2i[labels[j]] for j in b]).to(device)

    for epoch in range(args.epochs):
        model.train()
        total = 0.0
        for step, (enc, lab) in enumerate(batches(Xtr, ytr, args.batch, True), 1):
            loss = model(**enc, labels=lab).loss
            loss.backward()
            opt.step()
            opt.zero_grad()
            total += float(loss)
            if step % 50 == 0:
                print(f"эпоха {epoch + 1}, шаг {step}, loss {total / step:.4f}")

    model.eval()
    pred = []
    with torch.no_grad():
        for enc, _ in batches(Xte, yte, 64, False):
            pred += model(**enc).logits.argmax(-1).cpu().tolist()
    pred = [LABELS[i] for i in pred]
    m = {"model": args.model, "macro_f1": float(f1_score(yte, pred, average="macro")), "accuracy": float((np.array(pred) == np.array(yte)).mean()),
         "n_train": len(Xtr), "n_test": len(Xte), "epochs": args.epochs}
    print(m)
    out = os.path.join(ROOT, "models", "rubert_sentiment")
    os.makedirs(out, exist_ok=True)
    model.save_pretrained(out)
    tok.save_pretrained(out)
    with open(os.path.join(ROOT, "models", "metrics_rubert.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    print("Сохранено в", out, "- сервер автоматически использует эту модель при следующем запуске")


if __name__ == "__main__":
    main()
