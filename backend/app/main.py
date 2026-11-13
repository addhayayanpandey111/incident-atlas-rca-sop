import io
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from .chat import Chat
from .db import Store, execute, one, rows
from .ingestion import Ingestor, now, redact, validation_issues
from .retrieval import Retriever

ROOT = Path(__file__).resolve().parents[2]


class IngestRequest(BaseModel):
    source_key: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=500_000)
    revision: int | None = Field(default=None, ge=1)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    session_id: str | None = Field(default=None, max_length=100)
    document_id: str | None = Field(default=None, max_length=100)


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=5, max_length=1000)


def create_app(store=None):
    @asynccontextmanager
    async def lifespan(app):
        if not getattr(app.state, "store", None):
            app.state.store = Store()
        app.state.retriever = Retriever(app.state.store)
        app.state.ingestor = Ingestor(app.state.store)
        app.state.chat = Chat(app.state.store, app.state.retriever)
        if os.getenv("SEED_DEMO", "true").lower() == "true":
            for path in sorted((ROOT / "sample_data").rglob("*.md")):
                app.state.ingestor.ingest("demo/" + path.relative_to(ROOT / "sample_data").as_posix(), path.read_text(encoding="utf-8"))
        yield

    app = FastAPI(title="Incident Atlas", version="1.0.0", lifespan=lifespan)
    if store:
        app.state.store = store

    def admin(request: Request):
        expected = os.getenv("ADMIN_TOKEN")
        if expected:
            import hmac
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(supplied, expected):
                raise HTTPException(401, "Admin token required")

    @app.middleware("http")
    async def measure(request, call_next):
        start = time.perf_counter()
        request_id = str(uuid.uuid4())
        if int(request.headers.get("content-length", "0") or "0") > 2_000_000:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed:.2f}"
        if request.url.path.startswith("/api/") and request.url.path != "/api/metrics":
            usage = getattr(request.state, "usage", {})
            try:
                with app.state.store.transaction(write=True) as conn:
                    # Paths contain no raw queries, document bodies or secrets.
                    route = request.scope.get("route")
                    execute(conn, "INSERT INTO request_metrics VALUES (:id,:op,:latency,:calls,:inputs,:outputs,:cost,:outcome,:created)",
                        id=request_id, op=route.path if route else "unknown", latency=elapsed,
                        calls=usage.get("model_calls", 0), inputs=usage.get("input_tokens", 0),
                        outputs=usage.get("output_tokens", 0), cost=usage.get("cost_usd", 0),
                        outcome=str(response.status_code), created=now())
            except SQLAlchemyError:
                response.headers["X-Telemetry-Degraded"] = "database unavailable"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request, exc):
        return JSONResponse({"detail": "Knowledge store unavailable; retry shortly. No document answer can be verified."}, status_code=503)

    @app.get("/api/health")
    def health():
        with app.state.store.transaction() as conn:
            execute(conn, "SELECT 1")
        return {"status": "ok", "mode": "local-extractive", "llm_enabled": app.state.chat.selector.enabled,
            "admin_auth": bool(os.getenv("ADMIN_TOKEN")), "retrieval": "lexical + operational concepts"}

    @app.get("/api/documents")
    def documents():
        return app.state.retriever.list()

    @app.get("/api/documents/{document_id}")
    def document(document_id: str):
        doc = app.state.retriever.get(document_id)
        if not doc:
            raise HTTPException(404, "Active document not found")
        return doc

    @app.post("/api/ingest", dependencies=[Depends(admin)])
    def ingest(body: IngestRequest):
        try:
            return app.state.ingestor.ingest(redact(body.source_key), body.content, body.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/upload", dependencies=[Depends(admin)])
    async def upload(request: Request, filename: str, revision: int | None = None):
        if len(filename) > 250 or revision is not None and revision < 1:
            raise HTTPException(422, "Invalid filename or revision")
        data = await request.body()
        if len(data) > 2_000_000:
            raise HTTPException(413, "File too large; maximum 2 MB")
        suffix = Path(filename).suffix.lower()
        try:
            if suffix == ".pdf":
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data))
                if len(reader.pages) > 100:
                    raise ValueError("Too many pages")
                content = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            elif suffix in (".md", ".txt"):
                content = data.decode("utf-8")
            else:
                raise HTTPException(415, "Upload Markdown, UTF-8 text or a text-based PDF")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "Cannot extract this file; scanned PDFs need OCR outside this demo") from exc
        if not content.strip() or len(content) > 500_000:
            raise HTTPException(422, "Empty or oversized extracted content")
        try:
            return app.state.ingestor.ingest("upload/" + redact(Path(filename).name), content, revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/sources", dependencies=[Depends(admin)])
    def remove_source(source_key: str, revision: int | None = None):
        try:
            return app.state.ingestor.remove(source_key, revision)
        except KeyError as exc:
            raise HTTPException(404, "Source not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/search")
    def search(q: str = "", kind: Literal["RCA", "SOP"] | None = None, service: str | None = None):
        if len(q) > 20000:
            raise HTTPException(422, "Query too long")
        return app.state.retriever.search(redact(q), kind, service)

    @app.post("/api/suggestions")
    def suggestions(body: ChatRequest):
        message = redact(body.message)
        return {"rcas": app.state.retriever.search(message, "RCA")["results"],
            "sops": app.state.retriever.search(message, "SOP")["results"],
            "message": "Suggestions are evidence matches, not permission to execute a procedure."}

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request):
        result = app.state.chat.answer(body.message, body.session_id, body.document_id)
        request.state.usage = result["usage"]
        return result

    @app.get("/api/reviews", dependencies=[Depends(admin)])
    def reviews():
        return app.state.retriever.list("review")

    @app.post("/api/reviews/{document_id}", dependencies=[Depends(admin)])
    def review(document_id: str, body: ReviewRequest):
        with app.state.store.transaction(write=True) as conn:
            doc = one(conn, "SELECT * FROM documents WHERE id=:id AND status='review'", id=document_id)
            if not doc:
                raise HTTPException(404, "Pending review not found")
            if doc["current_version"] != body.expected_version:
                raise HTTPException(409, "Document changed; review the current version")
            version = one(conn, "SELECT sections FROM versions WHERE document_id=:id AND version=:v", id=document_id, v=doc["current_version"])
            hard_errors = validation_issues(doc["kind"], json.loads(version["sections"]))
            if body.decision == "approve" and hard_errors:
                raise HTTPException(409, {"message": "Correct the source and re-ingest before approval", "issues": hard_errors})
            audit = json.loads(doc["issues"]) + [f"Review {body.decision}: {redact(body.note)}"]
            execute(conn, "UPDATE documents SET status=:status, reviewed_at=:date, issues=:issues WHERE id=:id",
                status="active" if body.decision == "approve" else "rejected", date=now(), issues=json.dumps(audit), id=document_id)
        return {"outcome": body.decision}

    @app.get("/api/metrics", dependencies=[Depends(admin)])
    def metrics():
        with app.state.store.transaction() as conn:
            metrics = rows(conn, "SELECT * FROM request_metrics ORDER BY created_at DESC LIMIT 1000")
            counts = rows(conn, "SELECT status, COUNT(*) AS count FROM documents GROUP BY status")
        latency = sorted(m["latency_ms"] for m in metrics)
        return {"requests": metrics, "document_counts": counts,
            "summary": {"requests": len(metrics), "p95_ms": latency[min(len(latency)-1, int(len(latency)*.95))] if latency else 0,
                "model_calls": sum(m["model_calls"] for m in metrics), "estimated_cost_usd": sum(m["cost_usd"] for m in metrics)},
            "cost_note": "Token costs only; configure model prices. Failed calls may be unbilled or have unknown usage. Infrastructure cost excluded."}

    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "frontend" / "index.html")

    return app


app = create_app()
