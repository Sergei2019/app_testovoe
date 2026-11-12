import os

import pytest
from fastapi.testclient import TestClient

from app.db import make_engine
from app.main import create_app
from app.models import Base


@pytest.fixture()
def client():
    """По умолчанию — SQLite в памяти. TEST_DATABASE_URL позволяет прогнать
    те же тесты на PostgreSQL (включая тест конкурентного подтверждения)."""
    url = os.getenv("TEST_DATABASE_URL", "sqlite://")
    if not url.startswith("sqlite"):
        engine = make_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()
    app = create_app(url=url, seed_data=True)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def ids(client):
    products = {p["sku"]: p["id"] for p in client.get("/api/products").json()}
    warehouses = {w["name"]: w["id"] for w in client.get("/api/warehouses").json()}
    return products, warehouses