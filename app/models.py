"""
Pydantic Models for Store Intelligence API
Event schema validation and API request/response models
"""

from pydantic import BaseModel, Field, UUID4, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """Valid event types"""
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventSchema(BaseModel):
    """
    Core event schema - must match detection pipeline output
    
    This schema is the contract between detection pipeline and API.
    All events must validate against this schema.
    """
    event_id: UUID4 = Field(..., description="Globally unique event identifier")
    store_id: str = Field(..., min_length=1, max_length=50, description="Store identifier")
    camera_id: str = Field(..., min_length=1, max_length=50, description="Camera identifier")
    visitor_id: str = Field(..., min_length=1, max_length=50, description="Visitor session identifier")
    event_type: EventType = Field(..., description="Type of event")
    timestamp: datetime = Field(..., description="Event timestamp (ISO-8601 UTC)")
    zone_id: Optional[str] = Field(None, max_length=50, description="Zone identifier (null for ENTRY/EXIT)")
    dwell_ms: int = Field(default=0, ge=0, description="Dwell duration in milliseconds")
    is_staff: bool = Field(default=False, description="Whether visitor is staff member")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence score")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Event-specific metadata")
    
    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v):
        """Allow up to 60 s of clock skew; reject timestamps far in the future."""
        from datetime import timedelta, timezone as tz
        # Always compare timezone-aware datetimes
        now = datetime.now(tz.utc)
        # Make v timezone-aware if it isn't already
        if v.tzinfo is None:
            v = v.replace(tzinfo=tz.utc)
        if v > now + timedelta(seconds=60):
            raise ValueError("Timestamp is more than 60 seconds in the future")
        return v
    
    @field_validator('zone_id')
    @classmethod
    def validate_zone_id(cls, v, info):
        """Validate zone_id based on event_type"""
        event_type = info.data.get('event_type')
        if event_type in [EventType.ENTRY, EventType.EXIT, EventType.REENTRY]:
            if v is not None:
                raise ValueError(f"zone_id must be null for {event_type} events")
        elif event_type in [EventType.ZONE_ENTER, EventType.ZONE_EXIT, EventType.ZONE_DWELL]:
            if v is None:
                raise ValueError(f"zone_id is required for {event_type} events")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "550e8400-e29b-41d4-a716-446655440000",
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_ENTRY_01",
                "visitor_id": "VIS_c8a2f1",
                "event_type": "ZONE_DWELL",
                "timestamp": "2026-03-03T14:22:10Z",
                "zone_id": "SKINCARE",
                "dwell_ms": 8400,
                "is_staff": False,
                "confidence": 0.91,
                "metadata": {
                    "sku_zone": "MOISTURISER",
                    "session_seq": 5
                }
            }
        }


class EventBatch(BaseModel):
    """Batch of events for ingestion"""
    events: List[EventSchema] = Field(..., min_length=1, max_length=500)
    
    class Config:
        json_schema_extra = {
            "example": {
                "events": [
                    {
                        "event_id": "550e8400-e29b-41d4-a716-446655440000",
                        "store_id": "STORE_BLR_002",
                        "camera_id": "CAM_ENTRY_01",
                        "visitor_id": "VIS_c8a2f1",
                        "event_type": "ENTRY",
                        "timestamp": "2026-03-03T14:22:10Z",
                        "zone_id": None,
                        "dwell_ms": 0,
                        "is_staff": False,
                        "confidence": 0.91,
                        "metadata": {"session_seq": 1}
                    }
                ]
            }
        }


class IngestResponse(BaseModel):
    """Response from event ingestion"""
    success: int = Field(..., description="Number of successfully ingested events")
    errors: List[Dict[str, Any]] = Field(default_factory=list, description="List of errors for failed events")
    trace_id: str = Field(..., description="Request trace ID for debugging")


class StoreMetrics(BaseModel):
    """Store metrics for a given date"""
    store_id: str
    date: str
    unique_visitors: int = Field(..., description="Total unique visitors (excluding staff)")
    conversion_rate: float = Field(..., ge=0.0, le=1.0, description="Purchase conversion rate")
    avg_dwell_per_zone: Dict[str, int] = Field(..., description="Average dwell time per zone (ms)")
    queue_depth_current: int = Field(..., ge=0, description="Current billing queue depth")
    abandonment_rate: float = Field(..., ge=0.0, le=1.0, description="Billing queue abandonment rate")
    
    class Config:
        json_schema_extra = {
            "example": {
                "store_id": "STORE_BLR_002",
                "date": "2026-03-03",
                "unique_visitors": 127,
                "conversion_rate": 0.23,
                "avg_dwell_per_zone": {
                    "SKINCARE": 45000,
                    "MAKEUP": 38000,
                    "FRAGRANCE": 22000
                },
                "queue_depth_current": 3,
                "abandonment_rate": 0.12
            }
        }


class FunnelStage(BaseModel):
    """Single stage in conversion funnel"""
    stage: str = Field(..., description="Stage name")
    count: int = Field(..., ge=0, description="Number of visitors at this stage")
    drop_off_pct: float = Field(..., ge=0.0, le=100.0, description="Drop-off percentage from previous stage")


class ConversionFunnel(BaseModel):
    """Conversion funnel with all stages"""
    store_id: str
    date: str
    stages: List[FunnelStage]
    
    class Config:
        json_schema_extra = {
            "example": {
                "store_id": "STORE_BLR_002",
                "date": "2026-03-03",
                "stages": [
                    {"stage": "entry", "count": 127, "drop_off_pct": 0},
                    {"stage": "zone_visit", "count": 98, "drop_off_pct": 22.8},
                    {"stage": "billing_queue", "count": 45, "drop_off_pct": 54.1},
                    {"stage": "purchase", "count": 29, "drop_off_pct": 35.6}
                ]
            }
        }


class ZoneHeatmapData(BaseModel):
    """Heatmap data for a single zone"""
    zone_id: str
    visit_frequency: int = Field(..., ge=0, description="Number of visits to this zone")
    avg_dwell_ms: int = Field(..., ge=0, description="Average dwell time in milliseconds")
    normalized_score: int = Field(..., ge=0, le=100, description="Normalized score (0-100)")


class StoreHeatmap(BaseModel):
    """Store heatmap with all zones"""
    store_id: str
    date: str
    zones: List[ZoneHeatmapData]
    data_confidence: bool = Field(..., description="True if sufficient data (20+ sessions)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "store_id": "STORE_BLR_002",
                "date": "2026-03-03",
                "zones": [
                    {
                        "zone_id": "SKINCARE",
                        "visit_frequency": 85,
                        "avg_dwell_ms": 45000,
                        "normalized_score": 92
                    }
                ],
                "data_confidence": True
            }
        }


class AnomalySeverity(str, Enum):
    """Anomaly severity levels"""
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class Anomaly(BaseModel):
    """Single anomaly detection"""
    type: str = Field(..., description="Anomaly type")
    severity: AnomalySeverity
    detected_at: datetime
    current_value: float
    baseline_value: float
    suggested_action: str = Field(..., description="Recommended action to resolve")


class StoreAnomalies(BaseModel):
    """All active anomalies for a store"""
    store_id: str
    anomalies: List[Anomaly]
    
    class Config:
        json_schema_extra = {
            "example": {
                "store_id": "STORE_BLR_002",
                "anomalies": [
                    {
                        "type": "BILLING_QUEUE_SPIKE",
                        "severity": "WARN",
                        "detected_at": "2026-03-03T15:42:00Z",
                        "current_value": 8.0,
                        "baseline_value": 3.0,
                        "suggested_action": "Deploy additional billing counter staff"
                    }
                ]
            }
        }


class StoreHealth(BaseModel):
    """Health status for a single store"""
    last_event: Optional[datetime] = Field(None, description="Timestamp of last event")
    lag_seconds: Optional[int] = Field(None, description="Seconds since last event")
    status: str = Field(..., description="Status: active, stale, or inactive")


class HealthResponse(BaseModel):
    """Overall system health"""
    status: str = Field(..., description="Overall status: healthy, degraded, or unhealthy")
    database: str = Field(..., description="Database connection status")
    stores: Dict[str, StoreHealth] = Field(..., description="Per-store health status")
    warnings: List[str] = Field(default_factory=list, description="Active warnings")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "database": "connected",
                "stores": {
                    "STORE_BLR_002": {
                        "last_event": "2026-03-03T15:45:12Z",
                        "lag_seconds": 8,
                        "status": "active"
                    }
                },
                "warnings": []
            }
        }
