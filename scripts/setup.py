"""Полная подготовка проекта одной командой: БД -> обучение моделей -> обработка отзывов.

    python -m scripts.setup                          # демо-данные
    python -m scripts.setup --csv data/reviews.csv   # обучить тональность на реальном датасете
"""
import argparse

from app import db, pipeline
from scripts import seed_db, train


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    args = ap.parse_args(argv)
    print("1/3 Создаю БД и демо-данные...")
    seed_db.seed_raw(reset=True)
    print("2/3 Обучаю модели...")
    train.main(["--csv", args.csv] if args.csv else [])
    pipeline.Models.reset()
    print("3/3 Обрабатываю отзывы (ABSA, эмбеддинги, антифрод, профили)...")
    with db.session() as conn:
        touched = pipeline.process_pending(conn)
    print(f"Готово: обработано товаров - {len(touched)}. Запуск: uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()
