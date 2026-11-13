"""Poll a mounted folder. Only delete sources this worker has previously imported."""
import json
import os
import time
from pathlib import Path

from .db import Store, rows
from .ingestion import Ingestor


def sync(store, directory):
    root = Path(directory)
    if not root.is_dir():
        return {"status": "source unavailable; deletion skipped"}
    ingestor = Ingestor(store)
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in (".md", ".txt") and p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root.resolve()))
    present = set()
    errors = 0
    for path in files:
        key = "folder/" + path.relative_to(root).as_posix()
        present.add(key)
        try:
            if path.stat().st_size > 500_000:
                errors += 1
                continue
            ingestor.ingest(key, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            errors += 1
    with store.transaction() as conn:
        known = rows(conn, "SELECT source_key FROM sources WHERE source_key LIKE 'folder/%' AND removed=0")
    for source in known:
        if source["source_key"] not in present:
            ingestor.remove(source["source_key"])
    return {"files": len(files), "errors": errors}


def main():
    store = Store()
    while True:
        try:
            result = sync(store, os.getenv("WATCH_DIR", "knowledge"))
            print(json.dumps(result), flush=True)
        except Exception:
            print('{"status":"sync unavailable; retrying"}', flush=True)
        time.sleep(max(2, int(os.getenv("SYNC_INTERVAL_SECONDS", "10"))))


if __name__ == "__main__":
    main()
