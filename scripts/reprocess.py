"""Пересчитать все отзывы в базе текущими моделями (после переобучения на реальном датасете).

    python -m scripts.reprocess
"""
from app import db, pipeline


def main():
    pipeline.Models.reset()
    with db.session() as conn:
        n = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        conn.execute("UPDATE reviews SET analyzed = 0")
        touched = pipeline.process_pending(conn)
    m = pipeline.Models.get()
    print(f"Пересчитано отзывов: {n}, товаров: {len(touched)}. Тональность: {m.sentiment.name}, эмбеддинги: {m.embedder.backend}")


if __name__ == "__main__":
    main()
