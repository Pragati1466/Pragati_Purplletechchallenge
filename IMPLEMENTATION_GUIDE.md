# Implementation Guide - Next Steps

## 🎯 You Have a Complete Foundation

All documentation, architecture, and infrastructure is ready. This guide helps you implement the remaining code efficiently.

---

## What's Already Done ✅

1. **Complete Documentation** (7,000+ words)
   - README.md with Purplle context
   - DESIGN.md with architecture and AI decisions
   - CHOICES.md with 3 key technical decisions
   - QUICKSTART.md for reviewers

2. **Infrastructure** (Production-Ready)
   - Docker Compose configuration
   - Database schema with triggers
   - FastAPI application structure
   - Pydantic models for validation

3. **Sample Code**
   - Complete API main.py with middleware
   - All Pydantic schemas
   - Sample test file with AI documentation

---

## Implementation Priority Order

### Phase 1: Detection Pipeline (Critical - 8 hours)

#### File 1: `pipeline/detect.py`
**Purpose:** Main detection script using YOLOv8

**Key Components:**
```python
from ultralytics import YOLO
import cv2

def detect_persons(video_path, model_path='yolov8n.pt'):
    """
    Detect persons in video frames
    Returns: List of detections with bounding boxes
    """
    model = YOLO(model_path)
    cap = cv2.VideoCapture(video_path)
    
    detections = []
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        
        # Detect persons (class 0)
        results = model.predict(frame, classes=[0], conf=0.5)
        # Process results...
    
    return detections
```

**AI Prompt to Use:**
> "Generate a Python script using YOLOv8 (ultralytics) to detect persons in retail CCTV footage. Input: video file path. Output: list of detections with bounding boxes, confidence scores, and frame numbers. Handle 1080p 15fps video efficiently."

#### File 2: `pipeline/tracker.py`
**Purpose:** ByteTrack multi-object tracking

**Key Components:**
```python
from bytetrack import BYTETracker

def track_persons(detections, fps=15):
    """
    Track persons across frames using ByteTrack
    Returns: Tracks with consistent IDs
    """
    tracker = BYTETracker(
        track_thresh=0.5,
        track_buffer=30,
        match_thresh=0.8,
        frame_rate=fps
    )
    # Track detections...
```

**AI Prompt to Use:**
> "Generate a Python script using ByteTrack to track persons across video frames. Input: list of detections from YOLOv8. Output: tracks with consistent track IDs. Handle occlusions and re-appearances."

#### File 3: `pipeline/reid.py`
**Purpose:** Re-identification for cross-session tracking

**Key Components:**
```python
def extract_appearance_features(bbox_region):
    """Extract 512-dim embedding from person region"""
    # Use ResNet50 or similar
    pass

def is_same_visitor(track1, track2, threshold=0.7):
    """
    Determine if two tracks are the same person
    Uses appearance + trajectory + time gap
    """
    appearance_sim = cosine_similarity(track1.embedding, track2.embedding)
    time_gap = abs(track1.last_seen - track2.first_seen)
    
    if appearance_sim > threshold and time_gap < 300:
        return True
    return False
```

**AI Prompt to Use:**
> "Generate a Python script for person re-identification in retail CCTV. Extract appearance features from bounding box regions. Compare tracks using cosine similarity. Handle re-entry detection (same person returning after 5 minutes)."

#### File 4: `pipeline/emit.py`
**Purpose:** Generate structured events

**Key Components:**
```python
from app.models import EventSchema, EventType
import uuid

def emit_entry_event(visitor_id, store_id, camera_id, timestamp, confidence):
    """Generate ENTRY event"""
    return EventSchema(
        event_id=uuid.uuid4(),
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=visitor_id,
        event_type=EventType.ENTRY,
        timestamp=timestamp,
        zone_id=None,
        dwell_ms=0,
        is_staff=False,
        confidence=confidence,
        metadata={"session_seq": 1}
    )
```

**AI Prompt to Use:**
> "Generate a Python script to emit structured events from tracking data. Input: tracks with IDs, timestamps, zones. Output: EventSchema objects (ENTRY, EXIT, ZONE_DWELL, etc.) in JSONL format. Follow the schema in app/models.py."

---

### Phase 2: API Endpoints (Critical - 6 hours)

#### File 1: `app/database.py`
**Purpose:** PostgreSQL async connection

**AI Prompt to Use:**
> "Generate a Python module for async PostgreSQL connection using SQLAlchemy 2.0 and asyncpg. Include: connection pool, session management, transaction support. Use DATABASE_URL from environment."

#### File 2: `app/ingestion.py`
**Purpose:** POST /events/ingest endpoint

**Key Requirements:**
- Idempotent (use event_id as primary key)
- Batch support (up to 500 events)
- Partial success (return errors for invalid events)
- Structured logging

**AI Prompt to Use:**
> "Generate a FastAPI router for event ingestion. Endpoint: POST /events/ingest. Accept batch of EventSchema objects (max 500). Insert into PostgreSQL with idempotency (ON CONFLICT DO NOTHING). Return success count and error list. Include structured logging with trace_id."

#### File 3: `app/metrics.py`
**Purpose:** GET /stores/{id}/metrics endpoint

**Key Metrics:**
- unique_visitors (exclude is_staff=true)
- conversion_rate (from sessions + POS correlation)
- avg_dwell_per_zone
- queue_depth_current
- abandonment_rate

**AI Prompt to Use:**
> "Generate a FastAPI router for store metrics. Endpoint: GET /stores/{store_id}/metrics?date=YYYY-MM-DD. Compute: unique visitors, conversion rate, avg dwell per zone, queue depth, abandonment rate. Use Redis cache (30s TTL). Exclude staff from metrics."

#### File 4: `app/funnel.py`
**Purpose:** GET /stores/{id}/funnel endpoint

**Funnel Stages:**
1. Entry (all sessions)
2. Zone Visit (at least one ZONE_ENTER)
3. Billing Queue (BILLING_QUEUE_JOIN)
4. Purchase (POS transaction within 5-min window)

**AI Prompt to Use:**
> "Generate a FastAPI router for conversion funnel. Endpoint: GET /stores/{store_id}/funnel?date=YYYY-MM-DD. Compute 4-stage funnel: Entry → Zone Visit → Billing Queue → Purchase. Calculate drop-off percentages. Handle re-entry (no double-counting)."

---

### Phase 3: Testing (Important - 4 hours)

Use the pattern from `tests/test_metrics.py`:

1. Add AI prompt at top of each test file
2. Document changes made after AI generation
3. Include Purplle-specific edge cases
4. Test idempotency, empty stores, all-staff clips

**AI Prompt Template:**
> "Generate pytest tests for [module]. Cover: happy path, edge cases (empty input, invalid data, re-entry), error handling (database failure). Use async fixtures. Include performance benchmarks (p95 latency < 50ms)."

---

### Phase 4: Dashboard (Bonus - 6 hours)

#### Simple Terminal Dashboard (Faster)
Use `rich` library for terminal UI:

```python
from rich.live import Live
from rich.table import Table

def create_metrics_table(metrics):
    table = Table(title="Store Metrics - Live")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="magenta")
    
    table.add_row("Unique Visitors", str(metrics.unique_visitors))
    table.add_row("Conversion Rate", f"{metrics.conversion_rate:.1%}")
    # ...
    
    return table
```

**AI Prompt to Use:**
> "Generate a Python script for a terminal dashboard using the 'rich' library. Display store metrics in a live-updating table. Fetch metrics from API every 5 seconds. Show: visitors, conversion rate, queue depth."

#### Web Dashboard (Better, More Time)
React + WebSocket for real-time updates

**AI Prompt to Use:**
> "Generate a React dashboard for retail analytics. Components: MetricsCard (visitors, conversion), FunnelChart (4-stage funnel), Heatmap (zone visits). Use WebSocket for real-time updates. Fetch from API: http://localhost:8000"

---

## Time-Saving Tips

### 1. Use AI Effectively
- Provide the prompts I've written above
- Give AI access to your schema files (models.py, init.sql)
- Ask for "production-ready code with error handling"
- Review and test AI output (don't blindly trust)

### 2. Start with Minimal Viable Implementation
- **Pipeline:** Get basic detection working first (skip Re-ID initially)
- **API:** Implement ingestion + metrics first (skip anomalies initially)
- **Tests:** Cover happy path first, add edge cases later
- **Dashboard:** Terminal dashboard is faster than web UI

### 3. Test Incrementally
```bash
# Test pipeline on single clip first
python pipeline/detect.py --video data/cctv_clips/STORE_BLR_002/entry.mp4

# Test API with sample events
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d @data/sample_events.jsonl

# Test metrics endpoint
curl http://localhost:8000/stores/STORE_BLR_002/metrics?date=2026-03-03
```

### 4. Use Provided Assertions
The challenge provides `assertions.py` - run it frequently:
```bash
python data/assertions.py
```

---

## Debugging Checklist

### Pipeline Not Working?
- [ ] YOLOv8 model downloaded? (`yolov8n.pt`)
- [ ] Video file readable? (check codec)
- [ ] Output directory exists? (`output/events/`)
- [ ] Events validate against schema? (check with Pydantic)

### API Not Working?
- [ ] Database initialized? (`docker compose logs db`)
- [ ] Redis running? (`docker compose ps redis`)
- [ ] Environment variables set? (check `.env`)
- [ ] Migrations applied? (check `init.sql`)

### Tests Failing?
- [ ] Database fixtures working? (check `conftest.py`)
- [ ] Async tests configured? (`pytest-asyncio`)
- [ ] Test data realistic? (use `sample_events.jsonl`)

---

## Final Validation Before Submission

### 1. Acceptance Gate (Must Pass)
```bash
# 1. Docker compose starts
docker compose up -d
docker compose ps  # All services "Up"

# 2. Pipeline produces events
./pipeline/run.sh data/cctv_clips/ data/store_layout.json
ls -lh output/events.jsonl  # File exists and non-empty

# 3. API ingests events
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d @output/events.jsonl

# 4. API responds
curl http://localhost:8000/stores/STORE_BLR_002/metrics

# 5. Documentation exists
ls -lh docs/DESIGN.md docs/CHOICES.md  # Both >250 words
```

### 2. Test Coverage
```bash
pytest --cov=app --cov-report=term
# Target: >70% coverage
```

### 3. Performance Benchmarks
```bash
# API latency
ab -n 1000 -c 10 http://localhost:8000/stores/STORE_BLR_002/metrics
# Target: p95 < 50ms

# Pipeline speed
time python pipeline/detect.py --video data/cctv_clips/STORE_BLR_002/entry.mp4
# Target: 45 FPS (3x real-time)
```

---

## Submission Checklist

### Code
- [ ] Pipeline processes all clips
- [ ] API responds to all 6 endpoints
- [ ] Tests pass with >70% coverage
- [ ] Docker compose up works on clean machine

### Documentation
- [ ] README.md complete (already done ✅)
- [ ] DESIGN.md with AI decisions (already done ✅)
- [ ] CHOICES.md with 3 decisions (already done ✅)
- [ ] Prompt blocks in test files (sample done ✅)

### Validation
- [ ] Provided assertions.py passes
- [ ] Entry/exit count accurate (>90%)
- [ ] Staff excluded from metrics
- [ ] Re-entry handled correctly
- [ ] Idempotency tested

### Bonus (Part E)
- [ ] Dashboard shows live metrics
- [ ] Real-time simulation working
- [ ] WebSocket updates functional

---

## Resources

### YOLOv8 Documentation
- https://docs.ultralytics.com/

### ByteTrack
- https://github.com/ifzhang/ByteTrack

### FastAPI
- https://fastapi.tiangolo.com/

### PostgreSQL + SQLAlchemy
- https://docs.sqlalchemy.org/en/20/

### Testing
- https://docs.pytest.org/

---

## Support

If you get stuck:
1. Check logs: `docker compose logs [service]`
2. Review sample code in this repo
3. Use AI with the prompts provided
4. Test incrementally (don't try to build everything at once)

---

**You have a solid foundation. Focus on implementation, test frequently, and you'll have a standout submission!**

**Built for Purplle Tech Challenge 2026** 🎬✨
