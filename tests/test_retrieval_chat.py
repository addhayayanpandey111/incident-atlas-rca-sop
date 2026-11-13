from backend.app.chat import Chat, route
from backend.app.ingestion import Ingestor
from backend.app.retrieval import Retriever


def test_exact_missing_id_never_substituted(seeded):
    r = Retriever(seeded)
    assert r.search("RCA-101")["results"][0]["id"] == "RCA-101"
    assert not r.search("RCA-999 payments")["results"]
    assert r.search("RCA-999 payments")["missing_ids"] == ["RCA-999"]
    assert not r.search("RCA-101", "SOP")["results"]


def test_isolation_and_abstention(seeded):
    r = Retriever(seeded)
    for kind in ["RCA", "SOP"]:
        result = r.search("payments database connection", kind)["results"]
        assert result and all(d["kind"] == kind for d in result)
    assert not r.search("astronaut lunar telescope")["results"]


def test_noise_does_not_change_ranking(seeded):
    r = Retriever(seeded)
    clean = "payments connection timeout"
    noisy = clean + "\n" + "\n".join(["    at java.lang.Object.wait(Unknown Source)"] * 100)
    assert [d["id"] for d in r.search(clean)["results"]] == [d["id"] for d in r.search(noisy)["results"]]


def test_ambiguous_search_is_visible(seeded):
    result = Retriever(seeded).search("payments")
    assert result["ambiguous"] and len(result["results"]) == 2


def test_sop_exact_complete_and_ordered(seeded):
    r = Retriever(seeded)
    result = Chat(seeded, r).answer("SOP-101 steps")
    doc = r.get("SOP-101")
    for heading, value in doc["sections"].items():
        assert f"## {heading}\n{value}" in result["answer"]
    assert all(c["document_id"] == "SOP-101" for c in result["citations"])


def test_followup_switch_and_deleted_context(seeded):
    r, c = Retriever(seeded), Chat(seeded, Retriever(seeded))
    first = c.answer("Why did RCA-101 happen?")
    assert first["outcome"] == "answered"
    second = c.answer("What was the impact?", first["session_id"])
    assert "18%" in second["answer"]
    third = c.answer("SOP-101 steps", first["session_id"])
    assert third["kind"] == "SOP"
    assert all(x["document_id"].startswith("SOP-") for x in third["citations"])
    Ingestor(seeded).remove("payments-sop.md")
    fourth = c.answer("rollback", first["session_id"])
    assert fourth["outcome"] == "unsupported"
    assert "kubectl" not in fourth["answer"]


def test_unsupported_field_does_not_get_answer(seeded):
    result = Chat(seeded, Retriever(seeded)).answer("What was the CEO salary?", document_id="RCA-101")
    assert result["outcome"] in ("unsupported", "clarification")


def test_mixed_request_asks_clarification(seeded):
    assert Chat(seeded, Retriever(seeded)).answer("RCA and SOP for payments")["outcome"] == "clarification"


def test_session_isolation(seeded):
    c = Chat(seeded, Retriever(seeded))
    c.answer("Why RCA-101?", "session-a")
    result = c.answer("impact?", "session-b")
    assert result["outcome"] == "clarification"


def test_route_cases():
    for query, expected in [("why payments failed", "rca_question"), ("rollback steps", "sop_question"),
                            ("RCA and SOP", "mixed"), ("help", "clarification_needed"),
                            ("error connection timeout", "incident_suggestions")]:
        assert route(query)[0] == expected


def test_open_specific_document_is_lookup(seeded):
    result = Chat(seeded, Retriever(seeded)).answer("Show RCA-101")
    assert result["intent"] == "document_lookup"
    assert result["outcome"] == "answered"


def test_new_named_topic_overrides_followup_binding(seeded):
    c = Chat(seeded, Retriever(seeded))
    first = c.answer("Why did RCA-101 happen?")
    second = c.answer("Why did Kafka fail?", first["session_id"], "RCA-101")
    assert second["document_id"] == "RCA-102"
    assert "concurrency" in second["answer"]


def test_unsupported_sop_field_abstains(seeded):
    result = Chat(seeded, Retriever(seeded)).answer("SOP-101 CEO salary")
    assert result["outcome"] == "unsupported"
