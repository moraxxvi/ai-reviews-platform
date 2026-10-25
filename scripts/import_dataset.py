"""Загрузка РЕАЛЬНЫХ отзывов из CSV в базу вместо синтетических (для главы 3 и защиты).

CSV: колонка text/review (текст), по желанию rating (1-5), product (название товара из каталога), date (ГГГГ-ММ-ДД).
Если product не указан - отзывы раздаются по товарам каталога по кругу (для демонстрации интерфейса).
Если rating не указан, он выводится из колонки label/sentiment (positive=5, neutral=3, negative=1).

Запуск:  python -m scripts.import_dataset --csv data/reviews.csv --max 3000 [--keep-synthetic]
Затем:   python -m scripts.train --csv data/reviews.csv   (обучить модель на реальных данных)
"""
import argparse
import random
from datetime import datetime, timedelta

from app import db
from app.pipeline import Models, process_pending, recompute_profiles


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--max", type=int, default=3000)
    ap.add_argument("--keep-synthetic", action="store_true", help="не удалять демо-отзывы")
    args = ap.parse_args(argv)

    import pandas as pd
    df = pd.read_csv(args.csv)
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("text") or cols.get("review") or df.columns[0]
    rcol, pcol, dcol = cols.get("rating"), cols.get("product"), cols.get("date")
    lcol = cols.get("label") or cols.get("sentiment")
    df = df.dropna(subset=[tcol]).head(args.max)
    rnd = random.Random(42)
    lab2r = {"positive": 5, "pos": 5, "1": 5, "neutral": 3, "0": 3, "negative": 1, "neg": 1, "-1": 1}

    with db.session() as c:
        products = {r["title"]: r["product_id"] for r in c.execute("SELECT product_id, title FROM products")}
        if not products:
            raise SystemExit("Сначала выполните: python -m scripts.setup")
        ids = list(products.values())
        if not args.keep_synthetic:
            c.execute("DELETE FROM review_aspects")
            c.execute("DELETE FROM reviews")
        user = c.execute("SELECT user_id FROM users WHERE role='buyer' LIMIT 1").fetchone()["user_id"]
        today = datetime.now()
        n = 0
        for i, row in enumerate(df.itertuples(index=False)):
            d = row._asdict() if hasattr(row, "_asdict") else {}
            vals = list(row)
            text = str(vals[df.columns.get_loc(tcol)]).strip()
            if len(text) < 3:
                continue
            if rcol:
                rating = int(max(1, min(5, round(float(vals[df.columns.get_loc(rcol)])))))
            elif lcol:
                rating = lab2r.get(str(vals[df.columns.get_loc(lcol)]).lower(), 3)
            else:
                rating = 3
            pid = products.get(str(vals[df.columns.get_loc(pcol)])) if pcol else None
            pid = pid or ids[i % len(ids)]
            date = str(vals[df.columns.get_loc(dcol)])[:10] if dcol else (today - timedelta(days=rnd.randint(0, 180))).strftime("%Y-%m-%d")
            c.execute("INSERT INTO reviews (product_id, user_id, rating, text, date, is_verified, analyzed) VALUES (?,?,?,?,?,?,0)",
                      (pid, user, rating, text, date, 1))
            n += 1
        Models.get()
        process_pending(c)
        recompute_profiles(c)
    print(f"Импортировано отзывов: {n}. Профили товаров пересчитаны.")


if __name__ == "__main__":
    main()
