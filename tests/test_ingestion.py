from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from backend.app.db import one, rows
from backend.app.ingestion import Ingestor, redact
from backend.app.retrieval import Retriever

ROOT = Path(__file__).resolve().parents[1]
RCA = (ROOT / "sample_data/greenfield/payments-rca.md").read_text()
SOP = (ROOT / "sample_data/greenfield/payments-sop.md").read_text()


def test_repeated_and_concurrent_ingest_is_idempotent(store):
    ingestor = Ingestor(store)
    def run(_):
        return ingestor.ingest("wiki/payments", RCA)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(24)))
    assert sum(r["outcome"] == "created" for r in results) == 1
    assert ingestor.ingest("wiki/payments", RCA)["outcome"] == "unchanged"
    with store.transaction() as conn:
        assert one(conn, "SELECT COUNT(*) AS n FROM documents")["n"] == 1
        assert one(conn, "SELECT COUNT(*) AS n FROM versions")["n"] == 1


def test_versions_stable_id_and_removed_content_not_retrievable(store):
    ingestor, search = Ingestor(store), Retriever(store)
    ingestor.ingest("wiki/payments", RCA, 1)
    ingestor.ingest("wiki/payments", RCA.replace("18%", "19%"), 2)
    doc = search.get("RCA-101")
    assert doc["current_version"] == 2
    assert "19%" in doc["content"] and "18%" not in doc["content"]
    ingestor.remove("wiki/payments", 3)
    assert search.get("RCA-101") is None
    assert not search.search("payments")["results"]
    with pytest.raises(ValueError):
        ingestor.ingest("wiki/payments", RCA, 1)


def test_same_revision_different_content_rejected(store):
    i = Ingestor(store)
    i.ingest("wiki/a", RCA, 3)
    with pytest.raises(ValueError):
        i.ingest("wiki/a", RCA + "\nchanged", 3)


def test_exact_duplicate_different_name_is_alias(store):
    i = Ingestor(store)
    i.ingest("wiki/a", RCA)
    result = i.ingest("wiki/b", RCA.replace("RCA-101", "RCA-999"))
    assert result["document_ids"] == ["RCA-101"]
    assert Retriever(store).get("RCA-999")["id"] == "RCA-101"
    i.remove("wiki/a")
    assert Retriever(store).get("RCA-101") is not None
    i.remove("wiki/b")
    assert Retriever(store).get("RCA-999") is None


def test_mixed_document_splits_and_quarantines(store):
    content = (ROOT / "sample_data/brownfield/mixed-redis.md").read_text()
    result = Ingestor(store).ingest("legacy/mixed", content)
    assert len(result["document_ids"]) == 2
    assert not Retriever(store).search("redis")["results"]
    docs = [Retriever(store).get(i, True) for i in result["document_ids"]]
    rca, sop = [next(d for d in docs if d["kind"] == k) for k in ["RCA", "SOP"]]
    assert "Steps" not in rca["sections"]
    assert "Root cause" not in sop["sections"]


def test_secrets_never_persist(store):
    secret = "dangerous-credential-123"
    Ingestor(store).ingest("a", RCA + "\napi_key=" + secret)
    with store.transaction() as conn:
        for table in ["documents", "versions", "sources", "aliases"]:
            assert secret not in str(rows(conn, "SELECT * FROM " + table))
    assert "10.0.0.1" not in redact("host=10.0.0.1 https://me:password@db.internal")


def test_missing_or_reordered_sop_steps_quarantined(store):
    i = Ingestor(store)
    i.ingest("a", SOP.replace("2. Check", "9. Check"))
    assert Retriever(store).get("SOP-101") is None
    assert "sequentially" in str(Retriever(store).get("SOP-101", True)["issues"])


def test_conflicting_identity_rolls_back(store):
    i = Ingestor(store)
    i.ingest("a", RCA)
    with pytest.raises(ValueError):
        i.ingest("b", RCA.replace("18%", "21%"))
    assert "18%" in Retriever(store).get("RCA-101")["content"]


def test_update_reindexes_without_old_results(store):
    i, r = Ingestor(store), Retriever(store)
    i.ingest("a", RCA + "\nunicornmarker")
    assert r.search("unicornmarker")["results"]
    i.ingest("a", RCA)
    assert not r.search("unicornmarker")["results"]


def test_duplicate_source_cannot_overwrite_canonical(store):
    i = Ingestor(store)
    i.ingest("a", RCA)
    i.ingest("b", RCA.replace("RCA-101", "RCA-999"))
    with pytest.raises(ValueError):
        i.ingest("b", RCA.replace("RCA-101", "RCA-999").replace("18%", "99%"))
    assert "18%" in Retriever(store).get("RCA-101")["content"]
    i.remove("a")
    i.ingest("b", RCA.replace("RCA-101", "RCA-999").replace("18%", "20%"))
    assert "20%" in Retriever(store).get("RCA-101")["content"]
