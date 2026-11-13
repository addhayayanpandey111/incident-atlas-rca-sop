"""Reproducible, small synthetic benchmark. Holdout is a partition, not an independent audit."""
import argparse
import json
import math
import os
import platform
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".deps"), str(ROOT)]
from fastapi.testclient import TestClient
from backend.app.chat import route
from backend.app.db import Store
from backend.app.main import create_app


def wilson(passed, total):
    if not total:
        return [0, 1]
    z, p = 1.96, passed / total
    center = (p + z*z/(2*total))/(1+z*z/total)
    half = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/(1+z*z/total)
    return [round(center-half, 4), round(center+half, 4)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "holdout"], default="development")
    args = parser.parse_args()
    os.environ.update(SEED_DEMO="true", ENABLE_LLM="false", ADMIN_TOKEN="")
    cases = [json.loads(line) for line in (ROOT / "evaluation/datasets" / (args.split + ".jsonl")).read_text().splitlines() if line]
    db_path = ROOT / "data" / ("evaluation-" + uuid.uuid4().hex + ".db")
    store = Store("sqlite:///" + db_path.as_posix())
    outputs = []
    with TestClient(create_app(store)) as client:
        # Snapshot fixtures identify reviewed legacy records without rewriting sources.
        pending = client.get("/api/reviews").json()
        refs = {}
        for doc in pending:
            if "DNS" in doc["title"]:
                refs["$dns"] = doc["id"]
            elif "Redis" in doc["title"]:
                refs["$redis_" + doc["kind"].lower()] = doc["id"]
        reviewed = False
        # Execute pre-review cases before simulated fixture-owner approval.
        cases.sort(key=lambda c: bool(c.get("reviewed")))
        for case in cases:
            if case.get("reviewed") and not reviewed:
                for doc_id in refs.values():
                    response = client.post("/api/reviews/" + doc_id, json={"decision":"approve", "expected_version":1,
                        "note":"Synthetic fixture owner approval for evaluation; not an automated production approval"})
                    assert response.status_code == 200, response.text
                reviewed = True
            start = time.perf_counter()
            op, query = case["operation"], case["query"]
            if op == "intent":
                actual = {"intent": route(query)[0]}
                passed = actual["intent"] == case["expected_intent"]
            elif op == "search":
                params = {"q":query}
                if case.get("kind"):
                    params["kind"] = case["kind"]
                actual = client.get("/api/search", params=params).json()
                expected = [refs.get(i, i) for i in case["expected_ids"]]
                ids = [d["id"] for d in actual["results"]]
                passed = all(i in ids[:3] for i in expected) if expected else not ids
                if case.get("kind"):
                    passed = passed and all(d["kind"] == case["kind"] for d in actual["results"])
            elif op == "chat":
                actual = client.post("/api/chat", json={"message":query}).json()
                passed = actual["outcome"] == case["expected_outcome"]
                passed = passed and (not case.get("expected_kind") or actual["kind"] == case["expected_kind"])
                passed = passed and (not case.get("contains") or case["contains"] in actual["answer"])
                if actual["outcome"] == "answered":
                    passed = passed and bool(actual["citations"]) and all(c["document_id"].startswith(actual["kind"] + "-") for c in actual["citations"])
            else:
                actual = client.post("/api/suggestions", json={"message":query}).json()
                passed = True
                for key, expected_key in [("rcas", "expected_rca"), ("sops", "expected_sop")]:
                    expected = refs.get(case.get(expected_key), case.get(expected_key))
                    passed = passed and (any(d["id"] == expected for d in actual[key][:3]) if expected else not actual[key])
            elapsed = (time.perf_counter()-start)*1000
            outputs.append({"case":case, "passed":bool(passed), "latency_ms":round(elapsed,3), "actual":actual})
        metrics = client.get("/api/metrics").json()
    groups = defaultdict(list)
    for output in outputs:
        groups[output["case"]["origin"] + "/" + output["case"]["operation"]].append(output)
    def summarize(group):
        passed = sum(o["passed"] for o in group)
        latency = sorted(o["latency_ms"] for o in group)
        return {"passed":passed,"total":len(group),"accuracy":round(passed/len(group),4),
            "wilson_95":wilson(passed,len(group)),"p95_ms":latency[min(len(group)-1,int(len(group)*.95))]}
    summary = {"split":args.split, "platform":platform.platform(), "python":platform.python_version(),
        "dataset_kind":"author-created synthetic cases; reused regression partition, not independently blinded. Initial holdout results preserved separately.",
        "database":"SQLite", "model_mode":"disabled", "overall":summarize(outputs),
        "groups":{k:summarize(v) for k,v in sorted(groups.items())}, "request_metrics":metrics["summary"],
        "limitations":["Small fixture corpus; not production accuracy or a load-test SLA.",
            "Brownfield retrieval measured after explicit simulated owner review; pre-review quarantine measured separately.",
            "No real model/provider measurement in this run.", "Wilson intervals assume independent Bernoulli cases; related synthetic cases weaken that assumption."]}
    directory = ROOT / "evaluation/results"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (args.split + "-raw.jsonl")).write_text("\n".join(json.dumps(o) for o in outputs)+"\n", encoding="utf-8")
    (directory / (args.split + "-summary.json")).write_text(json.dumps(summary,indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary,indent=2))
    failed = [o["case"]["id"] for o in outputs if not o["passed"]]
    if failed:
        print("Failed cases:", ", ".join(failed))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
