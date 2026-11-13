import re
import uuid

from .db import execute, rows
from .ingestion import now, redact
from .llm import EvidenceSelector
from .retrieval import tokens


def route(message, prior_kind=None):
    value = message.lower()
    explicit_rca = bool(re.search(r"\brca\b|root cause|why|caused|impact|timeline|kyun", value))
    explicit_sop = bool(re.search(r"\bsop\b|how to|steps|prerequisite|rollback|procedure|kaise", value))
    if explicit_rca and explicit_sop:
        return "mixed", None
    ids = re.findall(r"\b(?:RCA|SOP)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", message.upper())
    if ids and (re.search(r"\b(show|open|display|dikhao|kholo)\b", value) or message.strip().upper() in ids):
        return "document_lookup", ids[0].split("-")[0]
    if explicit_sop:
        return "sop_question", "SOP"
    if explicit_rca:
        return "rca_question", "RCA"
    if re.search(r"\b(?:RCA|SOP)-[A-Z0-9-]+\b", message.upper()):
        return "document_lookup", "SOP" if "SOP-" in message.upper() else "RCA"
    if re.search(r"error|exception|alert|stack|failing|timeout|503", value):
        return "incident_suggestions", None
    if prior_kind:
        return prior_kind.lower() + "_question", prior_kind
    return "clarification_needed", None


class Chat:
    def __init__(self, store, retriever):
        self.store, self.retriever = store, retriever
        self.selector = EvidenceSelector()

    def answer(self, message, session_id=None, document_id=None):
        session_id = session_id or str(uuid.uuid4())
        message = redact(message)
        with self.store.transaction() as conn:
            history = rows(conn, "SELECT * FROM messages WHERE session_id=:id ORDER BY sequence DESC LIMIT 4", id=session_id)
        prior = next((h for h in history if h["role"] == "assistant"), {})
        intent, kind = route(message, prior.get("kind"))
        usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        response = {"session_id": session_id, "intent": intent, "kind": kind, "citations": [], "usage": usage, "document_id": None}
        if intent in ("mixed", "clarification_needed"):
            response.update(answer="Do you want an incident explanation (RCA) or an operating procedure (SOP)?", outcome="clarification")
        elif intent == "incident_suggestions":
            response.update(answer="Related incidents and procedures are listed separately.", outcome="suggestions",
                rcas=self.retriever.search(message, "RCA")["results"], sops=self.retriever.search(message, "SOP")["results"])
        else:
            explicit = re.findall(r"\b(?:RCA|SOP)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", message.upper())
            bound_id = document_id or (prior.get("document_id") if prior.get("kind") == kind else None)
            if bound_id and not explicit:
                bound = self.retriever.get(bound_id)
                query_terms = tokens(message) - {"rca", "sop", "cause", "root", "incident", "outage", "steps", "procedure", "impact", "rollback", "prerequisites"}
                if bound:
                    bound_terms = tokens(bound["title"] + " " + bound["service"])
                    # A newly named service/topic overrides old conversational binding.
                    alternatives = self.retriever.list()
                    if not query_terms & bound_terms and any(query_terms & tokens(d["title"] + " " + d["service"]) for d in alternatives if d["kind"] == kind and d["id"] != bound["id"]):
                        bound_id = None
            if explicit:
                found = self.retriever.search(message, kind)
            elif bound_id:
                doc = self.retriever.get(bound_id)
                found = {"results": [doc] if doc and doc["kind"] == kind else [], "ambiguous": False}
            else:
                found = self.retriever.search(message, kind)
            if not found["results"]:
                response.update(answer="The active documents do not support this request. Specify another document or service.", outcome="unsupported")
            elif found.get("ambiguous") or len(explicit) > 1:
                response.update(answer="Several documents match. Choose an ID before I answer.", outcome="clarification", candidates=found["results"])
            else:
                doc = found["results"][0]
                sections = doc["sections"]
                chosen = list(sections)
                if kind == "SOP" and intent != "document_lookup":
                    procedural = bool(re.search(r"\b(steps|procedure|prerequisites?|rollback|verification|warnings?|how|kaise)\b", message.lower()))
                    substantive = tokens(re.sub(r"\b(?:RCA|SOP)-[A-Z0-9-]+\b", "", message, flags=re.I)) - {"sop", "rca"}
                    if not procedural and not substantive & tokens(doc["content"]):
                        chosen = []
                if kind == "RCA" and intent != "document_lookup":
                    question_tokens = tokens(message)
                    # Map question vocabulary to section names; unsupported fields abstain.
                    fields = []
                    for words, names in [({"why", "cause", "caused", "kyun"}, ["root cause"]),
                                         ({"impact", "big", "affected"}, ["impact"]),
                                         ({"timeline", "when"}, ["timeline"]),
                                         ({"fixed", "prevention", "prevent", "changed"}, ["prevention", "resolution"])]:
                        if question_tokens & words or any(w in message.lower().split() for w in words):
                            fields += names
                    if fields:
                        chosen = [k for k in sections if any(name in k.lower() for name in fields)]
                    else:
                        chosen = [k for k, v in sections.items() if tokens(k + " " + v) & (question_tokens - {"rca", "sop"})]
                    model_chosen, usage, warning = self.selector.select(message, {k: sections[k] for k in chosen}) if chosen else (None, usage, None)
                    response["usage"] = usage
                    if warning:
                        response["degraded"] = warning
                    if model_chosen is not None:
                        chosen = model_chosen
                if not chosen:
                    response.update(answer="This document does not contain evidence for that question.", outcome="unsupported")
                else:
                    # SOP always includes full original procedure, prerequisites and rollback.
                    # Never summarize, reorder, complete, or generate a missing step.
                    response.update(answer="\n\n".join(f"## {k}\n{sections[k]}" for k in chosen),
                        outcome="answered", document_id=doc["id"],
                        citations=[{"document_id": doc["id"], "version": doc["current_version"], "section": k} for k in chosen],
                        freshness=doc["freshness"])
        with self.store.transaction(write=True) as conn:
            seq = rows(conn, "SELECT COALESCE(MAX(sequence),0) AS n FROM messages WHERE session_id=:id", id=session_id)[0]["n"]
            for i, (role, content) in enumerate([("user", message), ("assistant", response["answer"])], 1):
                execute(conn, "INSERT INTO messages VALUES (:id,:seq,:role,:content,:kind,:doc,:created)",
                    id=session_id, seq=seq+i, role=role, content=content, kind=kind,
                    doc=response.get("document_id"), created=now())
        return response
