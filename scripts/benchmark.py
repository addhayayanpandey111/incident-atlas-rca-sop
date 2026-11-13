"""Synthetic single-client scale check. Does not estimate production concurrency."""
import argparse
import json
import platform
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".deps"), str(ROOT)]
from backend.app.db import Store, execute
from backend.app.ingestion import digest, now
from backend.app.retrieval import Retriever


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=3000)
    parser.add_argument("--queries", type=int, default=30)
    args = parser.parse_args()
    if args.documents < 1 or args.queries < 1:
        parser.error("Counts must be positive")
    store = Store("sqlite:///" + (ROOT / "data" / ("benchmark-" + uuid.uuid4().hex + ".db")).as_posix())
    timestamp = now()
    with store.transaction(write=True) as conn:
        # Bulk corpus setup; ingestion behavior is exercised by the separate suite.
        for i in range(args.documents):
            kind = "RCA" if i % 2 else "SOP"
            service = ["payments", "events", "catalog"][i % 3]
            doc_id = f"{kind}-BENCH-{i}"
            title = f"{service} connection pool timeout {i}"
            sections = {"Root cause":"Database connections leaked after deployment. " * 8 + f"Incident {i}."} if kind == "RCA" else {
                "Prerequisites":"Read access and approval.",
                "Steps":"1. Check database connection timeout metrics.\n2. Escalate to the service owner.",
                "Rollback":"Stop and restore the previous approved configuration."}
            content = "\n\n".join(f"## {k}\n{v}" for k,v in sections.items())
            execute(conn, "INSERT INTO documents VALUES (:id,:kind,:title,:service,'benchmark','test',1,'active',0.98,'[]',NULL,:date,:date,'greenfield')",
                id=doc_id,kind=kind,title=title,service=service,date=timestamp)
            execute(conn, "INSERT INTO versions VALUES (:id,1,:content,:hash,:sections,:date)",
                id=doc_id,content=content,hash=digest(content),sections=json.dumps(sections),date=timestamp)
    retriever = Retriever(store)
    measurements = {"search":[], "suggestions":[]}
    queries = ["payments connection timeout", "events database pool", "catalog timeout"]
    for i in range(args.queries):
        query = queries[i % len(queries)]
        start = time.perf_counter()
        assert retriever.search(query)["results"]
        measurements["search"].append((time.perf_counter()-start)*1000)
        start = time.perf_counter()
        assert retriever.search(query,"RCA")["results"]
        assert retriever.search(query,"SOP")["results"]
        measurements["suggestions"].append((time.perf_counter()-start)*1000)
    def summary(values):
        ordered = sorted(values)
        return {"p50_ms":round(statistics.median(values),3),"p95_ms":round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],3),
            "max_ms":round(max(values),3),"samples":len(values)}
    result = {"documents":args.documents,"database":"SQLite","python":platform.python_version(),"platform":platform.platform(),
        "mode":"direct service calls, single client, synthetic corpus; no HTTP/provider/400-user concurrency",
        **{k:summary(v) for k,v in measurements.items()}}
    result["targets_met"] = result["search"]["p95_ms"] < 1000 and result["suggestions"]["p95_ms"] < 3000
    path = ROOT / "evaluation/results/scale-summary.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
    store.engine.dispose()
    raise SystemExit(0 if result["targets_met"] else 1)


if __name__ == "__main__":
    main()
