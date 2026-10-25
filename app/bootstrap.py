"""Автоподготовка при старте (хостинг): если базы нет - создаёт её; если модели не подходят - переобучает на демо-данных."""
import os

from . import db, pipeline


def ensure_ready():
    models_ok = True
    try:
        pipeline.Models.reset()
        pipeline.Models.get()
    except Exception as e:  # нет файлов моделей или другая версия scikit-learn
        print(f"[bootstrap] модели недоступны ({e!r}), обучаю на демо-данных")
        models_ok = False
    if not models_ok:
        from scripts import setup
        setup.main([])
        return
    if not os.path.exists(db.DB_PATH):
        print("[bootstrap] БД не найдена, создаю и обрабатываю отзывы")
        from scripts import seed_db
        seed_db.seed_raw(reset=True)
        pipeline.Models.reset()
        with db.session() as conn:
            pipeline.process_pending(conn)
