from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ui_and_api(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
    assert len(client.get("/api/documents").json()) == 6
    assert client.get("/api/documents/RCA-999").status_code == 404
    result = client.get("/api/search", params={"q": "RCA-101"})
    assert result.json()["results"][0]["id"] == "RCA-101"
    assert "x-request-id" in result.headers


def test_review_and_structural_guard(client):
    reviews = client.get("/api/reviews").json()
    assert len(reviews) == 4
    invalid = next(d for d in reviews if "Old database" in d["title"])
    assert client.post("/api/reviews/" + invalid["id"], json={"decision":"approve","expected_version":1,"note":"Reviewed source"}).status_code == 409
    mixed_sop = next(d for d in reviews if d["kind"] == "SOP")
    result = client.post("/api/reviews/" + mixed_sop["id"], json={"decision":"approve","expected_version":1,"note":"Validated original source and procedure"})
    assert result.status_code == 200
    assert client.get("/api/documents/" + mixed_sop["id"]).status_code == 200


def test_admin_protection(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-secret")
    assert client.get("/api/reviews").status_code == 401
    assert client.get("/api/metrics").status_code == 401
    assert client.post("/api/ingest", json={"source_key":"a","content":"test"}).status_code == 401
    assert client.get("/api/reviews", headers={"Authorization":"Bearer test-secret"}).status_code == 200
    assert client.get("/api/search",params={"q":"payments"}).status_code == 200


def test_upload_and_metrics(client):
    data = (ROOT / "sample_data/greenfield/payments-rca.md").read_bytes()
    assert client.post("/api/upload?filename=copy.md", content=data, headers={"Content-Type":"application/octet-stream"}).status_code == 200
    result = client.post("/api/chat", json={"message":"SOP-101 steps"})
    assert result.json()["outcome"] == "answered"
    metrics = client.get("/api/metrics").json()
    assert metrics["summary"]["requests"] >= 2
    assert metrics["summary"]["model_calls"] == 0


def test_ingest_conflict_not_internal_error(client):
    data = (ROOT / "sample_data/greenfield/payments-rca.md").read_text().replace("18%", "22%")
    response = client.post("/api/ingest", json={"source_key":"collision","content":data})
    assert response.status_code == 409


def test_chat_rejects_invalid_input(client):
    assert client.post("/api/chat", json={"message":""}).status_code == 422
    assert client.get("/api/search",params={"q":"test", "kind":"BOGUS"}).status_code == 422
