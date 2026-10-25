"""Flask-шим: подключает ТЕ ЖЕ обработчики ROUTES без FastAPI.

Нужен только для проверки API и интерфейса там, где FastAPI не установлен
(в основной работе используется app/main.py). Запуск: python -m tests.dev_flask
"""
import os
import re

from flask import Flask, Response, jsonify, request, send_from_directory

from app.core import ApiError, Ctx
from app.routes import ROUTES

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def create_app():
    app = Flask(__name__, static_folder=None)

    def make(handler):
        def view(**kw):
            body = request.get_json(silent=True) if request.method in ("POST", "PUT") else {}
            ctx = Ctx(params={**kw, **request.args.to_dict()}, body=body if isinstance(body, dict) else {}, headers=dict(request.headers))
            try:
                return jsonify(handler(ctx))
            except ApiError as e:
                return jsonify({"error": e.message}), e.status
            except Exception as e:
                print("[api] ошибка", handler.__name__, repr(e))
                return jsonify({"error": "Внутренняя ошибка сервера"}), 500
        view.__name__ = f"{handler.__name__}_{id(handler)}"
        return view

    for method, path, handler in ROUTES:
        app.add_url_rule(re.sub(r"\{(\w+)\}", r"<\1>", path), view_func=make(handler), methods=[method])

    @app.get("/")
    def index():
        return send_from_directory(WEB, "index.html")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(WEB, p)

    return app


if __name__ == "__main__":
    create_app().run(port=int(os.environ.get("PORT", 8000)), threaded=True)
