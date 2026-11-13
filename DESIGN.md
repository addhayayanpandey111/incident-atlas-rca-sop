# Design and engineering decisions

## Objective and invariants

An incident responder must know which document they received, which version it is, whether it is current, and whether it explains an incident or prescribes action. The primary safety boundary is identity and evidence, not answer fluency.

1. An explicit missing ID never falls back to a similar document.
2. A retrieval operation is filtered by RCA/SOP before answer construction.
3. Ambiguity is surfaced; chat asks for an ID rather than selecting a near tie.
4. SOP answers reproduce every stored source section and numbered step in order. They never use an LLM to rewrite the procedure.
5. Only active current versions support new answers. Context binds IDs, never cached source text.
6. Quarantined sources cannot become suggestions by similarity alone.
7. Repeated/concurrent ingestion cannot create duplicate identities or versions.
8. Secret detection runs before persistence and before optional external inference.

## Architecture choice

A modular monolith and one polling worker are sufficient for the initial assignment corpus. PostgreSQL is the deployment store; SQLite WAL allows a fully local demonstration. SQLAlchemy Core uses parameterized statements shared by both adapters. Services are separated by deployment role, not by every function.

There is no broker, vector service, agent planner, tool executor or distributed cache. Each adds lifecycle races and operational cost before retrieval quality has been established. The UI uses vanilla browser modules because its interactions do not require a framework build pipeline.

## Data model

| Table | Responsibility |
| --- | --- |
| `sources` | Stable upstream key, sanitized source hash, monotonic revision, derived document IDs, deletion state |
| `documents` | Public identity, RCA/SOP kind, service, owner, environment, current version, review status, freshness, heuristic confidence |
| `versions` | Append-only sanitized content and ordered section map, exact content hash, creation time |
| `aliases` | Alternate public ID to canonical document identity |
| `document_owners` | Source authorized to advance a canonical identity; ownership transfers on source removal |
| `messages` | Session sequence, redacted text, intent kind and bound document ID |
| `request_metrics` | Route template, handler latency, model usage, token cost estimate, status, correlation ID |

Source-to-document links live in a small JSON list in `sources`. A normalized link table and foreign keys are an appropriate follow-up; current lifecycle mutations are centralized and transactional. This first schema uses `CREATE TABLE IF NOT EXISTS`, not a production migration history. Schema evolution needs versioned migrations before deployment with persistent customer data.

## Ingestion and version correctness

The content hash is computed on redacted source text. Parsing recognizes flat metadata and Markdown headings. Brownfield headings provide classification evidence; missing metadata, contradictory declarations, mixed content or incomplete procedures create review issues. Inferred titles preserve the first level-one heading.

A source update obtains the write transaction before checking the source hash or current version. PostgreSQL uses a shared advisory lock across ingestion, review, conversation and metrics mutations; SQLite uses an immediate write transaction. This intentionally favors predictable correctness over write throughput. Database unique keys protect source key, document ID, alias and version identity.

Extraction, version creation, current-version activation and source linkage commit atomically. There is no index that can publish only half an update. The same source hash does not add a version. Explicit source revisions prevent delayed edits from overwriting newer input. Unversioned input is accepted in arrival order; ordering cannot be reconstructed without upstream revision information.

Identical content of the same type/service is canonicalized. Alias sources are read-only so an old duplicate cannot revert a canonical edit on the next folder sync. They can be intentionally forked by deletion and a fresh identity. Near-duplicate detection uses result-time Jaccard similarity at 0.82; no destructive merge follows that heuristic. This is limited for synonym-heavy paraphrases.

Deletion removes source links logically. A document is removed only when no live source references it. Old versions remain in the database for audit, but are not exposed through search, document detail, new chat answers or alias lookup. Physical erasure and already-delivered client snapshots are separate retention problems and are not claimed as implemented.

## Classification and review

Rules distinguish incident sections (root cause, impact, timeline, prevention) from operating sections (steps, prerequisites, rollback). Mixed sources produce two stable IDs derived from source key and type, sharing a source linkage. Mixed extraction is always reviewed because section names alone cannot establish that warnings and dependencies were preserved correctly.

Confidence numbers are **uncalibrated heuristic scores**, not estimated correctness probabilities. Review reasons are more useful than a misleading percentage. Unknown type becomes explicitly provisional RCA in quarantine; a missing root-cause section prevents accidental approval. Reclassifying an unresolved source requires corrected source metadata and re-ingestion, not an arbitrary approval click.

SOP validation requires prerequisites, rollback, and numbered steps beginning at one without gaps or reordering. A detected secret redaction in the steps is a hard review failure. This validates structure, not whether the original command is operationally correct. A human must validate the underlying procedure.

## Retrieval and routing

Exact ID/alias lookup runs first. It also reports missing IDs for multi-ID requests. Exact title lookup runs next and preserves all identical-title candidates. The fallback tokenizes unique non-stack-frame lines, applies a small operational synonym lexicon, and combines original-term coverage, corpus rarity, title/service matches and related concepts. At least one original evidence term must overlap; generic one-word evidence cannot satisfy a multi-term query. Results below the relevance gate are excluded.

Close scores trigger ambiguity messaging. The scoring threshold and 0.08 ambiguity margin are explicit tuning choices, not calibrated confidence. The algorithm favors abstention over broad recall. There is no learned semantic retrieval; queries without vocabulary overlap may fail even if a relevant document exists. Full-corpus tokenization is O(corpus size) on each request. The included scale benchmark measures the practical boundary; production should use PostgreSQL full-text indexes and a vetted embedding/reranking model once evaluated.

The router uses explicit words and IDs to distinguish RCA questions, SOP questions, lookup, incident symptoms and clarification. Mixed RCA/SOP requests ask which result type is intended. Follow-ups inherit source type and document binding; explicit new IDs and RCA/SOP signals override history. The implementation handles the sample English/Hinglish triggers; broad multilingual understanding is not claimed.

## Conventional code versus a model

| Decision | Implementation | Evidence / trade-off |
| --- | --- | --- |
| Stable identity, duplicates, revisions | Transactions, unique keys, SHA-256 | Repeated and 24-way concurrent ingestion regression tests; no token cost |
| Exact lookup and type isolation | SQL and deterministic filters | Missing-ID and cross-type negative tests |
| Template parsing | Deterministic parser + validators | Predictable preservation of SOP source text; limited messy formats |
| Brownfield interpretation | Conservative section rules + review | Mixed-source split, unknown type and missing-field cases; less automatic recall |
| Intent routing | Rules + bounded context | Labelled query cases and session-switch tests; ambiguous language abstains |
| Similarity | Lexical/concept score | Query fixtures, noise invariant and scale benchmark; no true semantic recall claim |
| RCA evidence selection | Optional constrained LLM, deterministic fallback | Mocked valid selection, invented-key rejection, outage fallback and token accounting |
| SOP rendering | Exact source sections | Complete procedure equality test; zero model calls even with model enabled |

The optional model receives only candidate RCA sections, cannot return free-form answers, and must select exact known section names. Thus it can omit evidence or be unavailable, but cannot inject invented procedure text. Socket timeout is two seconds; it is not a strict total wall-clock deadline against a streaming/slow-drip adversarial endpoint. A bounded HTTP client with total deadlines and provider retries/circuit breaking is a production follow-up.

No real model was benchmarked locally. Mocked tests prove application contracts, not provider accuracy. An evidence-selector A/B evaluation against the rule baseline, a real embedding model, and a structured legacy extractor must be added before claiming a measured GenAI advantage.

## Cost, latency and failures

Local default: zero LLM calls and external token usage for ingestion, search, suggestions, SOPs and extractive RCA chat. Optional RCA selection is capped at one call per request, 150 output tokens and roughly 24 KB request payload. Provider-reported token usage is priced from configured per-million rates. Missing usage and failed-call billing are disclosed; hosting is excluded from the token-cost field.

Request metrics include API handler latency; response transmission and the final telemetry database write are not included in that header measurement. Evaluation client latency includes request overhead. Neither is an end-user network SLA. Cold startup, real concurrency, real provider behavior and production data distributions require separate measurements.

The design targets <1 second typical search and suggestions starting within 2–3 seconds by keeping models off both paths. Suggestions are currently returned in one response, not streamed. Database outages yield a transparent 503; optional inference outages retain local evidence answers. Worker errors retry without logging source content. An absent folder does not mean all source documents were removed. An empty mounted folder does mean its prior files were removed; production connectors need explicit sync cursors/tombstones to distinguish mount faults from legitimate emptiness.

## Security boundaries

Regex redaction is before persistence and inference, with tests for credentials/private addresses. It is incomplete for arbitrary secrets and internal network identifiers. Production requires secret scanning, access controls, encryption, retention limits, request quotas, parser isolation and audit review identities.

An optional admin bearer token gates mutation/review/metrics. Read access is shared across the workspace; conversation IDs partition context but are not authentication credentials. There is no user/tenant authorization. Source content is never HTML-executed or connected to tools. Commands in fixtures are displayed only.

## Evaluation and confidence

Development and holdout JSONL files separate cases by partition. All were authored with the synthetic fixture corpus, so they are not independently blinded. The first holdout run scored 19/22. It found a dropped legacy title, which prevented fixture approval, and a generic database match that incorrectly selected a procedure. The raw failed answers and summary are preserved. After fixes, the same cases are regression gates, and must not be advertised as new unseen performance.

The runner reports greenfield/brownfield and lookup/search/chat/suggestion/intent groups, raw outputs, per-case client timings, actual local token usage and Wilson intervals. Tiny, related synthetic cases mean intervals are descriptive, not a reliable estimate for Opstree's actual corpus. Reviewed brownfield cases simulate source-owner approval without rewriting source documents; quarantine cases verify unsupported behavior before approval.

The quality gate fails on any regression case failure. Unit/integration tests cover lifecycle and isolation separately. CI additionally builds Compose and executes HTTP checks against PostgreSQL. The 3,000-document benchmark is synthetic and single-client; it is not proof of 400-engineer concurrency capacity.

## Cuts and next steps

| Cut from this prototype | Why / next step |
| --- | --- |
| Trained embeddings, pgvector, learned reranker | Avoid model downloads and unmeasured cost; evaluate against a lexical baseline on real labelled queries |
| LLM brownfield field extraction | Current parser quarantines unstructured text; add schema-constrained exact-span extraction with provenance and calibration |
| React/build pipeline | The working UI needs no framework runtime; adopt if interaction scope grows |
| Full source connectors / OCR | File upload and mounted-folder sync demonstrate lifecycle; add wiki/Git APIs with revisions and ACLs |
| SSO, document ACLs, multi-tenant controls | Local assignment deployment only; mandatory before shared production rollout |
| Model-generated summaries | Extractive answers are easier to audit and cheaper; only add summaries with measured grounding checks |
| Distributed queue and retrieval cache | Added failure modes and cost; implement only after measured bottlenecks |
| Production migrations, retention, encryption and backups | Necessary operational follow-up before persistent live data |
| Broad multilingual and deeply ambiguous intent handling | Rule baseline is deliberately conservative; collect pressure-language queries and compare a constrained classifier |

Priority after this assignment: obtain the missing supplied query set and a permissioned representative brownfield sample; have independent engineers label it; freeze a new unseen test set; establish retrieval/abstention and safety baselines; then evaluate whether model extraction, embeddings or routing improve accuracy enough to justify their latency and token cost.
