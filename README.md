# AI-консультант интернет-магазина (СРО3, рабочая версия)

Анализ отзывов покупателей (NLP) и чат-консультант с ответами по отзывам (RAG). Учебный проект, Turan University.

## Быстрый запуск
```
pip install -r requirements.txt
python -m scripts.setup            # БД + обучение + обработка отзывов (демо-данные)
uvicorn app.main:app --reload      # http://127.0.0.1:8000  (Swagger: /docs)
```
Демо-входы: buyer@demo.kz / buyer123, seller@demo.kz / seller123 (кабинет продавца), admin@demo.kz / admin123.

## Реальные данные
```
python -m scripts.setup --csv data/reviews.csv        # обучить тональность на вашем CSV (text, label)
python -m scripts.import_dataset --csv data/reviews.csv --max 3000   # загрузить отзывы в магазин
python -m scripts.train_rubert --csv data/reviews.csv  # RuBERT (нужны torch, transformers; на Mac может идти долго)
```
После обучения RuBERT сервер сам использует `models/rubert_sentiment`, иначе работает опорная модель TF-IDF+LogReg.

## Честные пометки
- Отзывы и обучающие примеры по умолчанию СИНТЕТИЧЕСКИЕ. Метрики на них (≈1.0) для отчёта не годятся; берите метрики из `models/metrics.json` после обучения на реальном CSV.
- Код RuBERT (`scripts/train_rubert.py`) и FastAPI-адаптер (`app/main.py`) писались в среде без PyPI/Hugging Face и там не запускались. Логика обработчиков проверена тестами через Flask-шим.
- Тесты: `python -m unittest tests.test_all -v` (нужен flask только для шима).

## Структура
`ai/` NLP-движок · `app/` API, БД, чат, каталог · `web/` интерфейс · `scripts/` подготовка/обучение · `tests/` тесты · `data/` демо-корпус и БД
