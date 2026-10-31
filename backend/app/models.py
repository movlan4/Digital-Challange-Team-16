"""Domain types and API schemas."""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def bus_index(bus_id: str) -> str:
    """Human-friendly bus number within its line: '88-02' -> '2' (leading dashes / zeros stripped)."""
    index = bus_id.rsplit("-", 1)[-1].lstrip("-0")
    return index or "0"


class CongestionLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def score(self) -> int:
        return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}[self.value]

    @classmethod
    def from_score(cls, score: float) -> "CongestionLevel":
        if score >= 2.5:
            return cls.HIGH
        if score >= 1.5:
            return cls.MEDIUM
        return cls.LOW


class ReportStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class AlertStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


# ---------- Static network ----------

class Stop(BaseModel):
    id: str
    name: str
    lat: float
    lng: float


class Route(BaseModel):
    id: str
    number: str
    name: str
    color: str
    stop_count: int
    length_m: float
    closed: bool


class StopOnRoute(Stop):
    offset_m: float


class RouteDetail(Route):
    stops: list[StopOnRoute]
    geometry: list[tuple[float, float]]


class RouteLine(BaseModel):
    id: str
    number: str
    color: str
    geometry: list[tuple[float, float]]


class StopDetail(Stop):
    routes: list[Route]


class SearchResult(BaseModel):
    stops: list[Stop]
    routes: list[Route]


# ---------- Live data ----------

class BusPosition(BaseModel):
    bus_id: str
    route_id: str
    route_number: str
    lat: float
    lng: float
    speed_kmh: float
    next_stop_id: Optional[str]
    dwelling: bool                     # standing at a stop right now
    level: Optional[CongestionLevel]   # hybrid: passenger reports + GPS telemetry
    density_source: Optional[str]      # "reports+gps" | "reports" | "gps" | None
    report_count: int
    updated_at: Optional[datetime]


class Arrival(BaseModel):
    bus_id: str
    route_id: str
    route_number: str
    stop_id: str
    eta_seconds: int
    eta_minutes: float
    distance_m: int
    dwelling: bool
    level: Optional[CongestionLevel]
    density_source: Optional[str]
    report_count: int


class BusDensity(BaseModel):
    """Crowdedness of one bus: passenger reports fused with GPS telemetry."""
    bus_id: str
    route_id: str
    route_number: str
    level: Optional[CongestionLevel]
    score: Optional[float]
    source: Optional[str]
    report_score: Optional[float]      # passenger consensus; None until the threshold is reached
    report_count: int                  # all reports in the window
    last_report_at: Optional[datetime]
    reporters: int                     # distinct passengers (one vote each)
    consensus_reached: bool
    reporters_needed: int              # consensus threshold (distinct passengers)
    report_confidence: float           # 0..1 influence of the consensus over the GPS estimate
    outlier_reports: int               # votes ignored as outliers
    gps_score: Optional[float]
    avg_dwell_s: Optional[float]       # GPS: mean stop duration (simulated seconds)
    moving_speed_kmh: float            # GPS: smoothed speed between stops
    gap_ratio: Optional[float]         # GPS: gap to the bus ahead / even spacing
    dwelling: bool
    high_streak_minutes: float
    anomaly_threshold_minutes: float


class RouteDensity(BaseModel):
    """Line-level crowding, aggregated from the hybrid density of its buses."""
    route_id: str
    route_number: str
    route_name: str
    level: Optional[CongestionLevel]
    score: Optional[float]
    bus_count: int
    high_bus_count: int
    report_count: int
    avg_dwell_s: Optional[float]


# ---------- Passenger reports ----------

class ReportIn(BaseModel):
    bus_id: str = Field(..., min_length=1, max_length=32)
    level: CongestionLevel
    stop_id: Optional[str] = Field(None, max_length=32)
    comment: Optional[str] = Field(None, max_length=280)
    # Client-generated idempotency key: retries / double clicks of the same
    # submission are stored exactly once.
    submission_id: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


class Report(BaseModel):
    id: int
    passenger_email: str
    bus_id: str
    route_id: str
    route_number: str
    stop_id: Optional[str]
    level: CongestionLevel
    comment: Optional[str]
    status: ReportStatus
    created_at: datetime
    updated_at: datetime
    handled_by: Optional[str]
    resolution_note: Optional[str]


class ReportReceipt(BaseModel):
    report: Report
    duplicate: bool


class ReportUpdateIn(BaseModel):
    status: ReportStatus
    note: Optional[str] = Field(None, max_length=500)


# ---------- Alerts ----------

class Alert(BaseModel):
    id: int
    route_id: str
    bus_id: str
    message: str
    status: AlertStatus
    high_since: datetime
    created_at: datetime
    updated_at: datetime
    handled_by: Optional[str]


# ---------- Users, roles & auth ----------

class Role(str, Enum):
    PASSENGER = "passenger"
    OPERATOR = "operator"
    ADMIN = "admin"  # master administrator: user & role management + operator dashboard


def _normalize_email(v: str) -> str:
    v = v.strip().lower()
    if not EMAIL_RE.match(v):
        raise ValueError("Enter a valid email address")
    return v


class EmailIn(BaseModel):
    email: str = Field(..., max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        return _normalize_email(v)


class LoginIn(EmailIn):
    password: str = Field(..., min_length=1, max_length=128)


class RegisterIn(EmailIn):
    password: str = Field(..., min_length=8, max_length=128)


class PasswordResetIn(EmailIn):
    code: str = Field(..., pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8, max_length=128)


class Identity(BaseModel):
    user_id: int
    email: str
    role: Role


class SessionInfo(Identity):
    home: str  # the dashboard this role is routed to after login


class UserOut(BaseModel):
    id: int
    email: str
    role: Role
    created_at: datetime


class RoleUpdateIn(BaseModel):
    role: Role
