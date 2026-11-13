import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT / ".deps").exists():
    sys.path.insert(0, str(ROOT / ".deps"))

import pytest
from fastapi.testclient import TestClient
from backend.app.db import Store
from backend.app.ingestion import Ingestor
from backend.app.retrieval import Retriever


@pytest.fixture
def store(tmp_path):
    db = Store("sqlite:///" + (tmp_path / "test.db").as_posix())
    yield db
    db.engine.dispose()


@pytest.fixture
def seeded(store):
    ingestor = Ingestor(store)
    for path in sorted((ROOT / "sample_data" / "greenfield").glob("*.md")):
        ingestor.ingest(path.name, path.read_text())
    return store


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("SEED_DEMO", "true")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("ENABLE_LLM", "false")
    from backend.app.main import create_app
    with TestClient(create_app(store)) as client:
        yield client
