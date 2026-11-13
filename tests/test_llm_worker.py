import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

from backend.app.chat import Chat
from backend.app.llm import EvidenceSelector
from backend.app.retrieval import Retriever
from backend.app.worker import sync
from backend.app.ingestion import Ingestor

ROOT = Path(__file__).resolve().parents[1]


def test_model_selects_only_existing_evidence(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("LLM_OUTPUT_USD_PER_MILLION", "2")
    payload = {"choices":[{"message":{"content":json.dumps({"sections":["Root cause"]})}}],
        "usage":{"prompt_tokens":100,"completion_tokens":10}}
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
        sections, usage, error = EvidenceSelector().select("why", {"Root cause":"Source evidence"})
    assert sections == ["Root cause"] and error is None
    assert usage["model_calls"] == 1 and usage["input_tokens"] == 100
    assert abs(usage["cost_usd"] - .00012) < 1e-10


def test_invented_model_evidence_is_rejected(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    payload = {"choices":[{"message":{"content":'{"sections":["invented section"]}'}}]}
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
        selected, usage, warning = EvidenceSelector().select("why", {"Root cause":"Evidence"})
    assert selected is None and warning and usage["model_calls"] == 1


def test_model_failure_preserves_grounded_answer(seeded, monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        result = Chat(seeded, Retriever(seeded)).answer("Why RCA-101?")
    assert result["outcome"] == "answered"
    assert "degraded" in result and result["citations"]
    assert result["usage"]["model_calls"] == 1


def test_sop_never_calls_model(seeded, monkeypatch):
    monkeypatch.setenv("ENABLE_LLM", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    with patch("urllib.request.urlopen", side_effect=AssertionError("SOP must not call a model")):
        result = Chat(seeded, Retriever(seeded)).answer("SOP-101 steps")
    assert result["outcome"] == "answered" and result["usage"]["model_calls"] == 0


def test_folder_sync_change_delete_and_outage(store, tmp_path):
    folder = tmp_path / "knowledge"
    folder.mkdir()
    source = folder / "rca.md"
    source.write_text((ROOT / "sample_data/greenfield/payments-rca.md").read_text())
    assert sync(store, folder)["files"] == 1
    r = Retriever(store)
    assert r.get("RCA-101")["current_version"] == 1
    sync(store, folder)
    assert r.get("RCA-101")["current_version"] == 1
    # An unavailable mount does not imply deletion.
    sync(store, folder / "unavailable")
    assert r.get("RCA-101")
    source.write_text(source.read_text().replace("18%", "20%"))
    sync(store, folder)
    assert r.get("RCA-101")["current_version"] == 2
    source.unlink()
    sync(store, folder)
    assert r.get("RCA-101") is None


def test_removed_source_cannot_be_resurrected_at_same_revision(store):
    import pytest
    i = Ingestor(store)
    content = (ROOT / "sample_data/greenfield/payments-rca.md").read_text()
    i.ingest("a", content, 1)
    i.remove("a", 2)
    with pytest.raises(ValueError):
        i.ingest("a", content, 2)
