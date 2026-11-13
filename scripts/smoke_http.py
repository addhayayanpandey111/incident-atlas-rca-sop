"""Real HTTP lifecycle checks; suitable for Compose/PostgreSQL and local SQLite."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

BASE = os.getenv("ATLAS_URL", "http://127.0.0.1:8000").rstrip("/")


def request(path, body=None, method="GET"):
    headers = {"Content-Type":"application/json"}
    if os.getenv("ADMIN_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["ADMIN_TOKEN"]
    req = urllib.request.Request(BASE + "/api" + path,
        json.dumps(body).encode() if body is not None else None, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def main():
    token = uuid.uuid4().hex[:12].upper()
    doc_id, source = "RCA-SMOKE-" + token, "smoke/" + token
    content = f"""---
id: {doc_id}
type: RCA
title: HTTP smoke fixture {token}
service: smoke-{token}
owner: smoke-test
environment: test
last_reviewed: 2026-11-13
---
## Impact
Synthetic fixture only.
## Root cause
smokemarker{token.lower()} caused a synthetic connection failure.
"""
    assert request("/health")[0] == 200
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            result = list(pool.map(lambda _:request("/ingest", {"source_key":source,"content":content,"revision":1}, "POST"), range(16)))
        assert all(status == 200 for status, _ in result), result
        assert sum(body["outcome"] == "created" for _, body in result) == 1
        assert request("/documents/" + doc_id)[1]["current_version"] == 1
        new = content.replace("synthetic connection failure", "synthetic corrected failure")
        assert request("/ingest", {"source_key":source,"content":new,"revision":2}, "POST")[0] == 200
        assert request("/documents/" + doc_id)[1]["current_version"] == 2
        assert request("/ingest", {"source_key":source,"content":content,"revision":1}, "POST")[0] == 409
        answer = request("/chat", {"message":"Why " + doc_id + "?"}, "POST")[1]
        assert answer["outcome"] == "answered" and answer["citations"][0]["version"] == 2
    finally:
        status, _ = request("/sources?" + urllib.parse.urlencode({"source_key":source,"revision":3}), method="DELETE")
        assert status == 200
    assert request("/documents/" + doc_id)[0] == 404
    assert not request("/search?q=" + doc_id)[1]["results"]
    print("PASS: real HTTP health, 16 concurrent imports, single version, edit, stale-write rejection, cited answer, deletion and exact-ID non-substitution.")


if __name__ == "__main__":
    main()
