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
    refresh_demo_dates()


def refresh_demo_dates(max_age_days=3):
    """Демо-отзывы датированы относительно момента создания БД. Если база давно не обновлялась,
    сдвигаем все даты вперёд, чтобы окно «последние 30 дней» (предупреждения продавцу) не опустело."""
    from datetime import datetime
    with db.session() as conn:
        # берём 98-й процентиль, а не максимум: пара свежих пользовательских отзывов не должна скрывать «старость» демо-данных
        n = db.one(conn, "SELECT COUNT(*) n FROM reviews")["n"]
        row = db.one(conn, "SELECT date d FROM reviews ORDER BY date DESC LIMIT 1 OFFSET ?", (int(n * 0.02),))
        if not row or not row["d"]:
            return 0
        try:
            last = datetime.fromisoformat(row["d"])
        except ValueError:
            return 0
        age = (datetime.now() - last).days
        if age <= max_age_days:
            return 0
        conn.execute("UPDATE reviews SET date = strftime('%Y-%m-%dT%H:%M:%S', date, ?)", (f"+{age} days",))
        print(f"[bootstrap] даты демо-отзывов сдвинуты на {age} дн.")
        return age
