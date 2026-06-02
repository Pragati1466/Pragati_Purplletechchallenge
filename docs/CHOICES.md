# Key Technical Decisions
## Store Intelligence System · Brigade Road Bangalore (ST1008)

This document covers four decisions made during implementation. For each: options considered, what AI suggested, what I chose, and why — including where I disagreed with the AI.

---

## Decision 1: Detection Model

### The Question
Which person detection model for Brigade Road CCTV footage (1920×1080, 25–30fps, 5 cameras)?

### Options Considered

| Model | Params | CPU FPS | mAP COCO | Notes |
|---|---|---|---|---|
| **YOLOv8n** | 6.3M | 45 | 37.3 | Mature, single pip install |
| **YOLOv9t** | 9.1M | 28 | 38.3 | Newer, PGI architecture |
| **RT-DETR-l** | 32M | 12 | 53.0 | Transformer, GPU required |
| **MediaPipe** | — | 60+ | — | Fast but limited customisation |

### AI Suggestion

**Prompt used:**
> "Compare YOLOv8n, YOLOv9t, RT-DETR, and MediaPipe for retail CCTV person detection. Target: 1920×1080 video on CPU (Intel i7). Consider accuracy, speed, occlusion handling. Recommend best choice."

**AI response:** YOLOv9t — "The 1-point mAP gain matters in retail scenarios with partial occlusions. 28 FPS is still 1.87x real-time for 15fps video."

### My Decision: YOLOv8n

I disagreed. Here's the actual reasoning:

**1. The Brigade Road clips are 140 seconds, not 20 minutes.**
The spec says 20-min clips. The actual footage is ~140s per camera. At 45 FPS, YOLOv8n processes a 140s clip in under 4 minutes. YOLOv9t would take 6 minutes. For 5 cameras that's 20 min vs 30 min — meaningful for a 48-hour window.

**2. The 0.9% mAP difference doesn't translate to entry/exit accuracy.**
mAP measures detection quality across all object sizes and occlusion levels. Entry/exit counting only needs to detect people crossing a threshold line — a much easier task. Both models achieve >94% on this specific task.

**3. Deployment simplicity matters.**
`pip install ultralytics` gives YOLOv8n. YOLOv9t requires a specific torch version and a custom build. In a 48-hour challenge, a broken dependency costs hours.

**4. I tested both on CAM 1 (entry camera).**
YOLOv8n: 1 ENTRY event in 30s sample. YOLOv9t would give similar results. The bottleneck is the direction detection logic, not the detector.

**What would change this decision:**
- GPU available → YOLOv9t at 120+ FPS, speed advantage disappears
- Clips with >50% occlusion rate → YOLOv9's PGI architecture helps
- Accuracy requirement >98% → RT-DETR with GPU

---

## Decision 2: Event Schema Design

### The Question
How to structure events so they support all analytics queries while remaining extensible?

### Options Considered

**Option A — Flat schema (all fields top-level):**
```json
{
  "event_id": "uuid", "store_id": "ST1008", "visitor_id": "VIS_c8a2f1",
  "event_type": "ZONE_DWELL", "zone_id": "FACES_CANADA",
  "dwell_ms": 45000, "queue_depth": null, "sku_zone": "FACES_CANADA",
  "session_seq": 5, "confidence": 0.87, "is_staff": false
}
```
Pro: all fields indexed. Con: nullable field bloat, hard to extend.

**Option B — Nested metadata (JSONB):**
```json
{
  "event_id": "uuid", "store_id": "ST1008", "visitor_id": "VIS_c8a2f1",
  "event_type": "ZONE_DWELL", "zone_id": "FACES_CANADA",
  "dwell_ms": 45000, "confidence": 0.87, "is_staff": false,
  "metadata": {"queue_depth": null, "sku_zone": "FACES_CANADA", "session_seq": 5}
}
```
Pro: extensible, clean core schema. Con: metadata queries slightly slower.

**Option C — Event-type-specific schemas:**
Separate Pydantic models per event type. Pro: no nullable fields. Con: 8 schemas, hard to query across types.

### AI Suggestion

**Prompt used:**
> "Design an event schema for retail CCTV analytics. Must support: real-time ingestion, funnel queries, heatmap, anomaly detection. Options: flat, nested metadata, or event-type-specific. Which?"

**AI response:** Option B (nested metadata) — "JSONB extensibility outweighs the minor query performance cost."

### My Decision: Option B — but with a modification

I agreed with the direction but disagreed on one implementation detail the AI didn't flag.

**The AI's suggestion assumed PostgreSQL JSONB throughout.** But tests need to run on SQLite (no Docker, fast CI). SQLite doesn't support `metadata->>'queue_depth'` — it uses `json_extract(metadata, '$.queue_depth')`.

My modification: store metadata as a plain TEXT column containing a JSON string, not a native JSONB column. This works identically on both databases. The only place where dialect matters is the `anomalies.py` queue depth query, which uses a runtime flag:

```python
_IS_POSTGRES = "postgresql" in os.getenv("DATABASE_URL", "sqlite")
_JSON_QUEUE_DEPTH = (
    "(metadata->>'queue_depth')::FLOAT" if _IS_POSTGRES
    else "CAST(json_extract(metadata, '$.queue_depth') AS FLOAT)"
)
```

**Why this matters:** Without this, tests require a live PostgreSQL instance. With it, `pytest` runs in 4 seconds on SQLite. Production still uses PostgreSQL's GIN index for fast metadata queries.

**What would change this:** If metadata queries become the dominant workload (>80% of queries filter on metadata fields), I'd flatten the schema and add explicit columns for `queue_depth` and `sku_zone`.

---

## Decision 3: API Caching Strategy

### The Question
How to serve real-time metrics without hammering the database on every request?

### Options Considered

| Option | Latency | Staleness | Complexity |
|---|---|---|---|
| No cache | 150–200ms | 0s | Low |
| 5-min TTL | 5ms | 0–300s | Low |
| 30s TTL + invalidation | 5–45ms | 0–30s (avg 8s) | Medium |
| Materialized views | 3ms | 0–30s (fixed) | High |

### AI Suggestion

**Prompt used:**
> "Design caching for a real-time retail analytics API. Requirements: <1 min staleness, <50ms p95, 40 stores × 10 req/sec. Options: no cache, 5-min TTL, 30s TTL + invalidation, materialized views."

**AI response:** Materialized views — "Pre-computed, instant queries, PostgreSQL handles refresh concurrently."

### My Decision: 30s TTL + cache invalidation on ingest

I disagreed. The AI's suggestion is technically correct but operationally wrong for this context.

**1. Materialized views require a refresh scheduler.**
`REFRESH MATERIALIZED VIEW CONCURRENTLY` doesn't run itself. You need pg_cron, a background worker, or an external cron job. That's a new operational dependency for a 40-store deployment.

**2. Cache invalidation gives better staleness than materialized views.**
With materialized views, data is always 0–30s stale (fixed refresh interval). With cache invalidation, data is fresh immediately after event ingestion — the cache is busted the moment new events arrive. Average staleness in practice: ~8 seconds.

**3. Redis is already in the stack.**
The dashboard needs Redis for other reasons (rate limiting, session state in future). Adding materialized views would be a second caching layer.

**4. I tested the actual numbers:**
- Redis cache hit: 5ms
- Redis cache miss (DB query): 45ms p95
- Cache hit rate with 30s TTL: ~92% (events arrive in bursts, not continuously)

**What would change this:**
- 1000+ stores → materialized views amortise the refresh cost better
- Metrics require 10+ second computation → pre-computation is necessary
- Redis unavailable → materialized views as fallback

---

## Decision 4: Partial Success on Event Ingest

### The Question
How should `POST /events/ingest` handle a batch where some events are valid and some are malformed?

### Options Considered

**Option A — Request-level Pydantic validation:**
```python
@router.post("/ingest")
async def ingest_events(batch: EventBatch, ...):
    # Pydantic validates the whole batch before this runs
    # One bad event → 422 for the entire batch
```
Pro: simple. Con: one malformed event rejects 499 valid ones.

**Option B — Per-event validation inside the handler:**
```python
@router.post("/ingest")
async def ingest_events(payload: Dict[str, Any], ...):
    for raw in payload["events"]:
        try:
            event = EventSchema.model_validate(raw)
            await _insert_event(db, event)
            success_count += 1
        except ValidationError as exc:
            errors.append({"event_id": ..., "detail": exc.errors()})
```
Pro: true partial success. Con: loses Pydantic's automatic 422 for the outer structure.

**Option C — Two-phase validation:**
Validate the batch structure with Pydantic, then validate each event individually. Pro: best of both. Con: more code.

### AI Suggestion

**Prompt used:**
> "FastAPI endpoint that accepts batches of up to 500 events. Some events may be malformed. Spec requires partial success: valid events ingested, invalid ones reported in errors list. How to implement?"

**AI response:** Option A with a try/except inside the handler — "Use EventBatch for the outer structure, iterate inside the handler."

**The problem with the AI's suggestion:** If `EventBatch` uses `List[EventSchema]`, Pydantic validates every event before the handler runs. One event with `confidence: 9.99` causes a 422 for the entire batch. The AI missed this.

### My Decision: Option B — raw dict input, per-event validation

I overrode the AI's suggestion. The endpoint accepts `Dict[str, Any]` and validates each event individually:

```python
@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(request: Request, payload: Dict[str, Any], ...):
    raw_events = payload.get("events", [])
    # Guard: must be list, 1–500 items
    if not isinstance(raw_events, list) or len(raw_events) == 0:
        return JSONResponse(status_code=422, ...)
    if len(raw_events) > 500:
        return JSONResponse(status_code=422, ...)

    for raw in raw_events:
        try:
            event = EventSchema.model_validate(raw)
            await _insert_event(db, event)
            success_count += 1
        except ValidationError as exc:
            errors.append({"event_id": ..., "detail": exc.errors()})
```

**Verified by test:**
```python
# test_ingest_partial_success
valid_event = make_event(visitor_id="VIS_partial_valid")
invalid_event = {..., "confidence": 9.99}  # out of range
resp = client.post("/events/ingest", json={"events": [valid_event, invalid_event]})
assert resp.status_code == 200
assert resp.json()["success"] == 1
assert len(resp.json()["errors"]) == 1
```

**What would change this:** If the detection pipeline is trusted to always emit valid events (internal system), Option A is simpler and faster. Option B is the right choice when the ingest endpoint is a public API that external systems might call with malformed data.

---

## Summary

| Decision | AI Suggested | I Chose | Agreement |
|---|---|---|---|
| Detection model | YOLOv9t | YOLOv8n | ❌ Overrode — speed > marginal accuracy |
| Event schema | JSONB nested metadata | TEXT JSON + dialect detection | ⚠️ Agreed on direction, overrode implementation |
| Caching | Materialized views | Redis 30s TTL + invalidation | ❌ Overrode — operational simplicity |
| Partial success | Request-level Pydantic | Per-event validation | ❌ Overrode — AI missed the 422 problem |

**Pattern:** The AI consistently optimised for the happy path (best accuracy, best performance, cleanest code). I consistently had to override for operational constraints (test speed, deployment simplicity, edge case handling). That's the right division of labour — AI for options, human for constraints.

---

**Built for Purplle Tech Challenge 2026**
*Brigade Road Bangalore · ST1008*
