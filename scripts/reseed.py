"""Пересоздаёт БД (товары, отзывы) и обрабатывает отзывы ТЕКУЩИМИ моделями, НЕ переобучая их.

Запуск: python -m scripts.reseed
(в отличие от scripts.setup не затирает models/, в том числе модели, обученные на реальном датасете)
"""
from app import db, pipeline
from scripts import seed_db


def main():
    seed_db.seed_raw(reset=True)
    pipeline.Models.reset()
    with db.session() as conn:
        touched = pipeline.process_pending(conn)
    m = pipeline.Models.get()
    print(f"БД пересоздана, товаров: {len(touched)}. Тональность:", m.sentiment.name, "| эмбеддинги:", m.embedder.backend)


if __name__ == "__main__":
    main()
