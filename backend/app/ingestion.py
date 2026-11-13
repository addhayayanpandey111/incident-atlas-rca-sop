"""Conservative extraction: uncertainty quarantines a document instead of inventing steps."""
import hashlib
import json
import re
from datetime import datetime, timezone

from .db import execute, one, rows


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def redact(content):
    patterns = [
        (r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]"),
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "[REDACTED_AWS_KEY]"),
        (r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
        (r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@"),
        (r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b", "[PRIVATE_IP]"),
        (r"\b[a-zA-Z0-9.-]+\.(?:internal|local|corp)\b", "[INTERNAL_HOST]"),
    ]
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)
    return content


def parse(content):
    metadata, body = {}, content.replace("\r\n", "\n").strip()
    if body.startswith("---\n"):
        pieces = body.split("\n---", 1)
        if len(pieces) == 2:
            for line in pieces[0][4:].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip().lower()] = value.strip().strip('"\'')
            body = pieces[1].lstrip("\n")
    sections, heading, buffer = {}, "Overview", []
    for line in body.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if match:
            if line.startswith("# ") and "title" not in metadata:
                metadata["title"] = match.group(1)
            if buffer:
                sections[heading] = (sections.get(heading, "") + "\n" + "\n".join(buffer)).strip()
            heading, buffer = match.group(1), []
        else:
            buffer.append(line)
    if buffer:
        sections[heading] = (sections.get(heading, "") + "\n" + "\n".join(buffer)).strip()
    return metadata, body, sections


def classify(metadata, sections):
    names = " ".join(sections).lower()
    rca = any(term in names for term in ["root cause", "timeline", "impact", "prevention"])
    sop = any(term in names for term in ["steps", "procedure", "prerequisite", "rollback"])
    declared = metadata.get("type", "").upper()
    if rca and sop:
        return ["RCA", "SOP"], .75, ["Mixed document split into linked records; verify extraction."]
    inferred = "RCA" if rca else "SOP" if sop else None
    if declared in ("RCA", "SOP") and inferred and declared != inferred:
        return [inferred], .4, ["Declared type conflicts with extracted sections."]
    if declared in ("RCA", "SOP"):
        return [declared], .98 if inferred else .65, [] if inferred else ["Declared type has no supporting section structure."]
    if inferred:
        return [inferred], .8, ["Legacy type inferred; human verification required."]
    return ["RCA"], .2, ["Type unresolved: provisional RCA, excluded from search pending review."]


def selected_sections(sections, kind, mixed=False):
    if not mixed:
        return sections
    rca_terms = ["root cause", "timeline", "impact", "prevention", "resolution", "incident"]
    sop_terms = ["steps", "procedure", "prerequisite", "rollback", "verification", "checks", "warning"]
    terms = rca_terms if kind == "RCA" else sop_terms
    return {key: value for key, value in sections.items() if any(t in key.lower() for t in terms)}


def validation_issues(kind, sections):
    issues = []
    names = " ".join(sections).lower()
    if kind == "SOP":
        for required in ("prerequisite", "rollback"):
            if required not in names:
                issues.append(f"Missing {required} section.")
        steps = "\n".join(v for k, v in sections.items() if "steps" in k.lower() or "procedure" in k.lower())
        numbers = [int(n) for n in re.findall(r"(?m)^\s*(\d+)[.)]\s+", steps)]
        if not numbers or numbers != list(range(1, len(numbers) + 1)):
            issues.append("SOP steps must be complete, sequentially numbered from 1.")
        if "[REDACTED" in steps or "[PRIVATE_IP]" in steps or "[INTERNAL_HOST]" in steps:
            issues.append("Procedure was redacted; verify safe placeholders before approval.")
    elif "root cause" not in names:
        issues.append("Missing root cause section.")
    return issues


class Ingestor:
    def __init__(self, store):
        self.store = store

    def ingest(self, source_key, content, revision=None):
        # The raw source is never persisted, indexed or logged.
        safe = redact(content)
        metadata, body, sections = parse(safe)
        content_hash = digest(safe)
        kinds, confidence, issues = classify(metadata, sections)
        timestamp = now()
        with self.store.transaction(write=True) as conn:
            old = one(conn, "SELECT * FROM sources WHERE source_key=:key", key=source_key)
            if old and revision is not None and revision < old["revision"]:
                raise ValueError("Stale source revision rejected")
            if old and old["removed"] and revision is not None and revision <= old["revision"]:
                raise ValueError("Restoring a removed source requires a newer revision")
            if old and revision is not None and revision == old["revision"] and old["content_hash"] != content_hash:
                raise ValueError("Same revision cannot contain different content")
            if old and old["content_hash"] == content_hash and not old["removed"]:
                if revision is not None and revision > old["revision"]:
                    execute(conn, "UPDATE sources SET revision=:rev WHERE source_key=:key", rev=revision, key=source_key)
                return {"outcome": "unchanged", "document_ids": json.loads(old["document_ids"])}
            previous_ids = json.loads(old["document_ids"]) if old else []
            document_ids = []
            for kind in kinds:
                part = selected_sections(sections, kind, len(kinds) > 1)
                extracted = "\n\n".join(f"## {k}\n{v}" for k, v in part.items())
                part_hash = digest(extracted)
                part_issues = issues + validation_issues(kind, part)
                if not metadata.get("service"):
                    part_issues.append("Service metadata missing.")
                if not metadata.get("owner"):
                    part_issues.append("Owner metadata missing.")
                if not metadata.get("last_reviewed"):
                    part_issues.append("Document freshness unknown.")
                existing = next((one(conn, "SELECT * FROM documents WHERE id=:id", id=i)
                                 for i in previous_ids if one(conn, "SELECT kind FROM documents WHERE id=:id", id=i)["kind"] == kind), None)
                if existing:
                    owner = one(conn, "SELECT source_key FROM document_owners WHERE document_id=:id", id=existing["id"])
                    if owner and owner["source_key"] != source_key:
                        raise ValueError("Duplicate alias is read-only; update the canonical source or delete this alias and import with a new document ID")
                supplied_id = metadata.get("id", "").upper()
                requested_id = supplied_id if len(kinds) == 1 and re.fullmatch(r"(?:RCA|SOP)-[A-Z0-9-]+", supplied_id) else f"{kind}-{digest(source_key)[:10].upper()}"
                if not existing:
                    exact = one(conn, """SELECT d.* FROM documents d JOIN versions v
                        ON v.document_id=d.id AND v.version=d.current_version
                        WHERE v.content_hash=:hash AND d.kind=:kind AND d.service=:service
                        AND d.status != 'removed'""", hash=part_hash, kind=kind, service=metadata.get("service", "unknown"))
                    if exact:
                        if exact["id"] != requested_id:
                            conflict = one(conn, "SELECT * FROM aliases WHERE alias=:alias", alias=requested_id)
                            if conflict and conflict["document_id"] != exact["id"]:
                                raise ValueError("Document alias already belongs to another document")
                            if not conflict:
                                execute(conn, "INSERT INTO aliases VALUES (:alias,:id)", alias=requested_id, id=exact["id"])
                        document_ids.append(exact["id"])
                        continue
                doc_id = existing["id"] if existing else requested_id
                if not existing and (one(conn, "SELECT id FROM documents WHERE id=:id", id=doc_id) or one(conn, "SELECT alias FROM aliases WHERE alias=:id", id=doc_id)):
                    raise ValueError("Document ID already belongs to a different source; update its original source")
                if not doc_id.startswith(kind + "-"):
                    raise ValueError("Document ID prefix must match its type")
                version = existing["current_version"] + 1 if existing else 1
                title = metadata.get("title") or next(iter(sections), source_key)
                title = title + (f" [{kind}]" if len(kinds) > 1 else "")
                params = dict(id=doc_id, kind=kind, title=title, service=metadata.get("service", "unknown"),
                    owner=metadata.get("owner", "unknown"), environment=metadata.get("environment", "unspecified"),
                    version=version, status="review" if part_issues else "active", confidence=confidence,
                    issues=json.dumps(part_issues), updated=timestamp, reviewed=metadata.get("last_reviewed"),
                    origin="greenfield" if metadata.get("type") else "brownfield")
                if existing:
                    execute(conn, """UPDATE documents SET title=:title, service=:service, owner=:owner,
                        environment=:environment, current_version=:version, status=:status,
                        confidence=:confidence, issues=:issues, updated_at=:updated,
                        reviewed_at=:reviewed, origin=:origin WHERE id=:id""", **params)
                else:
                    execute(conn, """INSERT INTO documents VALUES (:id,:kind,:title,:service,:owner,
                        :environment,:version,:status,:confidence,:issues,NULL,:updated,:reviewed,:origin)""", **params)
                    execute(conn, "INSERT INTO document_owners VALUES (:id,:source)", id=doc_id, source=source_key)
                execute(conn, "INSERT INTO versions VALUES (:id,:v,:content,:hash,:sections,:created)",
                    id=doc_id, v=version, content=extracted, hash=part_hash, sections=json.dumps(part), created=timestamp)
                document_ids.append(doc_id)
            rev = revision if revision is not None else (old["revision"] + 1 if old else 1)
            if old:
                execute(conn, "UPDATE sources SET content_hash=:hash, revision=:rev, document_ids=:ids, removed=0 WHERE source_key=:key",
                    hash=content_hash, rev=rev, ids=json.dumps(document_ids), key=source_key)
            else:
                execute(conn, "INSERT INTO sources VALUES (:key,:hash,:rev,:ids,0)", key=source_key,
                    hash=content_hash, rev=rev, ids=json.dumps(document_ids))
            self._remove_orphans(conn, set(previous_ids) - set(document_ids))
            return {"outcome": "updated" if old else "created", "document_ids": document_ids}

    def _remove_orphans(self, conn, ids):
        active = rows(conn, "SELECT source_key, document_ids FROM sources WHERE removed=0 ORDER BY source_key")
        linked = {i for source in active for i in json.loads(source["document_ids"])}
        for doc_id in ids - linked:
            execute(conn, "UPDATE documents SET status='removed' WHERE id=:id", id=doc_id)
        for doc_id in ids & linked:
            owner = one(conn, "SELECT source_key FROM document_owners WHERE document_id=:id", id=doc_id)
            if owner and not any(s["source_key"] == owner["source_key"] and doc_id in json.loads(s["document_ids"]) for s in active):
                successor = next(s["source_key"] for s in active if doc_id in json.loads(s["document_ids"]))
                execute(conn, "UPDATE document_owners SET source_key=:source WHERE document_id=:id", source=successor, id=doc_id)

    def remove(self, source_key, revision=None):
        with self.store.transaction(write=True) as conn:
            source = one(conn, "SELECT * FROM sources WHERE source_key=:key", key=source_key)
            if not source:
                raise KeyError(source_key)
            if revision is not None and revision <= source["revision"]:
                raise ValueError("Deletion revision must be newer than the current source revision")
            execute(conn, "UPDATE sources SET removed=1, revision=:rev WHERE source_key=:key",
                key=source_key, rev=revision if revision is not None else source["revision"] + 1)
            self._remove_orphans(conn, set(json.loads(source["document_ids"])))
        return {"outcome": "removed"}
