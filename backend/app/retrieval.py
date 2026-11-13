"""Exact lookup first, then bounded lexical + operational-concept retrieval.

No nearest-neighbour result is accepted without overlapping evidence. This small
corpus implementation deliberately does not pretend to have a trained embedder.
"""
import json
import math
import re
from collections import Counter

from .db import one, rows

STOP = set("a an the is are was were to of for in on and or with it this that me show find give what how why do did does can you i my please about happened tell hai ka ki ke ko ye kya kaise mujhe wala last during then its from at as has have had not".split())
GROUPS = [
    {"database", "db", "postgres", "postgresql"},
    {"oom", "outofmemory", "memory", "oomkilled"},
    {"lag", "backlog", "behind"},
    {"latency", "slow", "slowness"},
    {"connection", "connections", "pool"},
    {"rollback", "revert", "restore"},
    {"unavailable", "outage", "503"},
]


def tokens(value):
    # Stack frame repetition must not dominate scoring.
    lines = list(dict.fromkeys(value.lower().splitlines()))
    useful = [line for line in lines if not re.match(r"^\s*(?:at\s+|file\s+\"|\^+\s*$)", line)]
    words = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", " ".join(useful))
    return set(w for w in words if len(w) > 1 and w not in STOP and not w.isdigit()) | ({"503"} if "503" in words else set())


def expand(words):
    result = set(words)
    for group in GROUPS:
        if words & group:
            result |= group
    return result


def document_dict(row):
    result = dict(row)
    for key in ("sections", "issues"):
        if key in result and isinstance(result[key], str):
            result[key] = json.loads(result[key])
    result["freshness"] = "unknown" if not result.get("reviewed_at") else result["reviewed_at"]
    result["confidence_method"] = "heuristic score, not a calibrated probability"
    return result


SELECT = """SELECT d.*, v.content, v.sections, v.content_hash FROM documents d
    JOIN versions v ON d.id=v.document_id AND d.current_version=v.version"""


class Retriever:
    def __init__(self, store):
        self.store = store

    def get(self, doc_id, include_review=False):
        with self.store.transaction() as conn:
            alias = one(conn, "SELECT document_id FROM aliases WHERE alias=:id", id=doc_id.upper())
            doc_id = alias["document_id"] if alias else doc_id.upper()
            row = one(conn, SELECT + " WHERE d.id=:id AND d.status != 'removed'", id=doc_id)
        if not row or (row["status"] != "active" and not include_review):
            return None
        return document_dict(row)

    def list(self, status="active"):
        with self.store.transaction() as conn:
            result = rows(conn, SELECT + " WHERE d.status=:status ORDER BY d.id", status=status)
        return [document_dict(row) for row in result]

    def search(self, query, kind=None, service=None, limit=8):
        ids = re.findall(r"\b(?:RCA|SOP)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", query.upper())
        if ids:
            result = []
            missing = []
            for doc_id in dict.fromkeys(ids):
                doc = self.get(doc_id)
                if doc and (not kind or doc["kind"] == kind) and (not service or doc["service"] == service):
                    result.append(self._result(doc, 1.0, [f"Exact ID: {doc_id}"]))
                else:
                    missing.append(doc_id)
            return {"mode": "exact", "results": result, "missing_ids": missing,
                "ambiguous": False, "message": "Requested document unavailable" if missing else None}
        candidates = [d for d in self.list() if (not kind or d["kind"] == kind) and (not service or d["service"] == service)]
        title_query = query.strip().strip('"').casefold()
        exact_titles = [d for d in candidates if d["title"].casefold() == title_query]
        if exact_titles:
            return {"mode": "title", "results": [self._result(d, 1, ["Exact title"]) for d in exact_titles],
                "ambiguous": len(exact_titles) > 1, "missing_ids": [], "message": "Multiple documents share this title; choose an ID." if len(exact_titles) > 1 else None}
        query_words = tokens(query)
        meaningful = query_words - {"rca", "sop", "incident", "steps", "procedure", "cause", "root"}
        if not meaningful:
            return {"mode": "search", "results": [], "ambiguous": True, "missing_ids": [], "message": "Specify a service, document ID, error or symptom."}
        expanded = expand(meaningful)
        corpus = [tokens(d["title"] + " " + d["service"] + " " + d["content"]) for d in candidates]
        counts = Counter(word for words in corpus for word in words)
        total = len(corpus)
        results = []
        for doc, words in zip(candidates, corpus):
            overlap = expanded & words
            direct = meaningful & words
            if not overlap or not direct:
                continue
            generic = {"database", "db", "connection", "connections", "memory", "timeout", "error", "errors", "old"}
            if len(direct) == 1 and direct <= generic and len(meaningful) > 1:
                continue
            # Require >= 25% original signal coverage; bounded top evidence.
            coverage = len(direct) / len(meaningful)
            if coverage < .25:
                continue
            weighted = sum(math.log(1 + (total + 1) / (counts[w] + 1)) for w in direct)
            title_matches = meaningful & tokens(doc["title"] + " " + doc["service"])
            score = min(.99, .45 * coverage + .25 * min(1, weighted / 3) + .2 * min(1, len(title_matches) / 2) + .1 * min(1, len(overlap - direct) / 2))
            if score < .27:
                continue
            reasons = ["Matching evidence: " + ", ".join(sorted(direct)[:8])]
            if overlap - direct:
                reasons.append("Related operational concepts: " + ", ".join(sorted(overlap - direct)[:5]))
            results.append(self._result(doc, round(score, 4), reasons))
        results.sort(key=lambda x: (-x["score"], x["id"]))
        results = results[:limit]
        # Near-duplicates remain visible with a canonical suggestion, never silently merged.
        for i, item in enumerate(results):
            left = tokens(item["content"])
            for earlier in results[:i]:
                right = tokens(earlier["content"])
                if item["kind"] == earlier["kind"] and len(left & right) / max(1, len(left | right)) >= .82:
                    item["near_duplicate_of"] = earlier["id"]
                    break
        ambiguous = len(results) > 1 and results[0]["score"] - results[1]["score"] < .08
        return {"mode": "search", "results": results[:limit], "ambiguous": ambiguous,
            "missing_ids": [], "message": "Several documents match; choose a document for a precise answer." if ambiguous else None if results else "No relevant document found."}

    def _result(self, doc, score, reasons):
        return {**doc, "score": score, "reasons": reasons,
            "citation": {"document_id": doc["id"], "version": doc["current_version"]}}
