"""Точка входа: uvicorn app.main:app"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from accounting import DomainError
from api import router
from db import database_url, make_engine, make_session_factory
from models import Base
from seed import seed


def create_app(url: str | None = None, seed_data: bool = True) -> FastAPI:
    target_url = url or database_url()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Ленивая инициализация: драйвер БД нужен только при старте, не при импорте.
        engine = make_engine(target_url)
        app.state.session_factory = make_session_factory(engine)
        # Прототип: схема создаётся при старте, тестовые данные засеваются один раз.
        Base.metadata.create_all(engine)
        if seed_data:
            with app.state.session_factory() as session:
                seed(session)
        yield
        engine.dispose()

    app = FastAPI(title="CheckStock", version="1.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": exc.code, "message": exc.message, "details": exc.details})

    app.include_router(router)
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
    return app


app = create_app()