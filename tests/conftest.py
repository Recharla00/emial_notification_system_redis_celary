import os
import tempfile
from pathlib import Path

import pytest

_temp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_temp.name) / "test.db")
os.environ["API_KEY"] = "test-secret-key-at-least-24-characters"

from app.database import Base, engine
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def close_database():
    yield
    engine.dispose()
    _temp.cleanup()


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def headers():
    return {"X-API-Key": os.environ["API_KEY"], "Idempotency-Key": "test-request"}


@pytest.fixture
def payload():
    return {"recipient": "person@example.com", "subject": "Welcome", "text": "Hello!"}
