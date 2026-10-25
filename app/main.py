"""Точка входа сервера: FastAPI-адаптер к обработчикам из app/routes.py.

Запуск:  uvicorn app.main:app --reload     ->  http://127.0.0.1:8000
Документация API (Swagger): http://127.0.0.1:8000/docs
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import db
from .core import ApiError, Ctx
from .routes import ROUTES

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

@asynccontextmanager
async def lifespan(_app):
    from .bootstrap import ensure_ready
    await run_in_threadpool(ensure_ready)
    yield


app = FastAPI(title="AI-консультант интернет-магазина", version="1.0 (СРО3)", lifespan=lifespan,
              description="Учебный проект: анализ отзывов (NLP) и AI-консультант. Данные в БД - демонстрационные.")


def _make_endpoint(handler):
    async def endpoint(request: Request):
        params = {**request.path_params, **dict(request.query_params)}
        body = {}
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
        ctx = Ctx(params=params, body=body, headers=dict(request.headers))
        try:
            return JSONResponse(await run_in_threadpool(handler, ctx))
        except ApiError as e:
            return JSONResponse({"error": e.message}, status_code=e.status)
        except Exception as e:  # непредвиденная ошибка: не раскрываем детали клиенту
            print(f"[api] необработанная ошибка в {handler.__name__}: {e!r}")
            return JSONResponse({"error": "Внутренняя ошибка сервера"}, status_code=500)

    endpoint.__name__ = handler.__name__
    return endpoint


for method, path, handler in ROUTES:
    app.add_api_route(path, _make_endpoint(handler), methods=[method], name=handler.__name__)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
