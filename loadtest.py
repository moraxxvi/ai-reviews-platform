"""Нагрузочный и функциональный тест сайта (только стандартная библиотека Python).
Запуск:  python3 loadtest.py https://ai-reviews-platform.onrender.com
Результат печатается и сохраняется в loadtest_result.txt (его нужно прислать для отчёта)."""
import json, sys, time, statistics, threading, urllib.request, urllib.error
from datetime import datetime

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
OUT = []


def log(s=""):
    print(s); OUT.append(s)


def call(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(); code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    return code, (time.perf_counter() - t0) * 1000


TESTS = [
    ("Главная страница", "GET", "/", None),
    ("Каталог (30 товаров)", "GET", "/api/products", None),
    ("Карточка товара", "GET", "/api/products/1", None),
    ("Разбор отзыва", "POST", "/api/analyze", {"text": "Камера отличная, но батарея садится к обеду"}),
    ("Чат: вопрос о товаре", "POST", "/api/chat", {"message": "Как батарея у iPhone 15 Pro?"}),
    ("Чат: сравнение брендов", "POST", "/api/chat", {"message": "Сравни Xiaomi и Samsung"}),
    ("Чат: подбор", "POST", "/api/chat", {"message": "Смартфон до 300 000 ₸ с хорошей камерой"}),
    ("Чат: несуществующая модель", "POST", "/api/chat", {"message": "Как батарея у iPhone 18 Pro Max?"}),
]


def pct(v, p):
    v = sorted(v); return v[min(len(v) - 1, int(len(v) * p))]


log(f"Нагрузочное тестирование {BASE}  ({datetime.now():%Y-%m-%d %H:%M})")
log("Прогрев сервера (на бесплатном хостинге первый запрос может занять до минуты)...")
code, ms = call("GET", "/api/products", timeout=150)
log(f"Первый запрос: код {code}, {ms/1000:.1f} с\n")

log("1) Последовательно: 20 запросов на каждую функцию")
log(f"{'Функция':32}{'ошибок':>8}{'среднее, мс':>13}{'p95, мс':>10}{'макс, мс':>10}")
for name, m, p, b in TESTS:
    res = [call(m, p, b) for _ in range(20)]
    errs = sum(1 for c, _ in res if c != 200)
    ts = [t for _, t in res]
    log(f"{name:32}{errs:>8}{statistics.mean(ts):>13.0f}{pct(ts, .95):>10.0f}{max(ts):>10.0f}")

log("\n2) Параллельно: 10 пользователей одновременно, каждый по 10 запросов (чат + каталог)")
lock, results = threading.Lock(), []


def worker():
    for i in range(10):
        m, p, b = (("POST", "/api/chat", {"message": "Как камера у Samsung Galaxy S24 Ultra?"}) if i % 2 else ("GET", "/api/products", None))
        r = call(m, p, b)
        with lock: results.append(r)


t0 = time.perf_counter()
th = [threading.Thread(target=worker) for _ in range(10)]
[t.start() for t in th]; [t.join() for t in th]
dur = time.perf_counter() - t0
ts = [t for _, t in results]
errs = sum(1 for c, _ in results if c != 200)
log(f"Запросов: {len(results)}, ошибок: {errs}, общее время: {dur:.1f} с, {len(results)/dur:.1f} запр/с")
log(f"Время ответа: среднее {statistics.mean(ts):.0f} мс, p95 {pct(ts, .95):.0f} мс, максимум {max(ts):.0f} мс")

log("\n3) Некорректные запросы (сервер не должен падать)")
checks = [("Пустое сообщение в чат", "POST", "/api/chat", {"message": "  "}, 400),
          ("Слишком длинное сообщение", "POST", "/api/chat", {"message": "а" * 1001}, 400),
          ("Несуществующий товар", "GET", "/api/products/99999", None, 404),
          ("Кабинет продавца без входа", "GET", "/api/seller/analytics", None, 401),
          ("Неверный пароль", "POST", "/api/auth/login", {"email": "buyer@demo.kz", "password": "x"}, 401)]
for name, m, p, b, want in checks:
    c, ms = call(m, p, b)
    log(f"{name:32} ожидали {want}, получили {c}  {'OK' if c == want else 'ОШИБКА'}")

open("loadtest_result.txt", "w", encoding="utf-8").write("\n".join(OUT))
log("\nГотово. Результат сохранён в loadtest_result.txt, пришли его содержимое.")
