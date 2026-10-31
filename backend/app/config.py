"""Application settings, overridable through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

_APP_DIR = os.path.dirname(__file__)


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Data
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "transit.db"))
    network_path: str = field(
        default_factory=lambda: os.getenv("NETWORK_PATH", os.path.join(_APP_DIR, "data", "network.json"))
    )

    # Master admin account (seeded at start-up). If no password is configured a
    # random one is generated and written to the log once.
    admin_email: str = field(default_factory=lambda: os.getenv("ADMIN_EMAIL", "admin@transit.az").strip().lower())
    admin_password: Optional[str] = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD") or None)
    secure_cookies: bool = field(default_factory=lambda: _env_bool("SECURE_COOKIES", False))

    # Outgoing mail for password reset codes. Without SMTP_HOST codes are written to the server log.
    smtp_host: Optional[str] = field(default_factory=lambda: os.getenv("SMTP_HOST") or None)
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    smtp_username: Optional[str] = field(default_factory=lambda: os.getenv("SMTP_USERNAME") or None)
    smtp_password: Optional[str] = field(default_factory=lambda: os.getenv("SMTP_PASSWORD") or None)
    smtp_starttls: bool = field(default_factory=lambda: _env_bool("SMTP_STARTTLS", True))
    mail_from: str = field(default_factory=lambda: os.getenv("MAIL_FROM", "Baku Smart Transit <no-reply@transit.az>"))

    # Anomaly detection ("HIGH for a continuous 10-minute period", per bus)
    anomaly_window_minutes: float = field(default_factory=lambda: _env_float("ANOMALY_WINDOW_MINUTES", 10))
    anomaly_bucket_minutes: float = field(default_factory=lambda: _env_float("ANOMALY_BUCKET_MINUTES", 2))
    anomaly_high_ratio: float = field(default_factory=lambda: _env_float("ANOMALY_HIGH_RATIO", 0.6))
    anomaly_check_interval_seconds: float = field(default_factory=lambda: _env_float("ANOMALY_CHECK_INTERVAL_SECONDS", 10))

    # Passenger reports newer than this are fused with GPS telemetry into per-bus density
    density_window_minutes: float = field(default_factory=lambda: _env_float("DENSITY_WINDOW_MINUTES", 15))
    # Consensus threshold: reports change a bus's status only once this many different passengers
    # reported it inside the window, with enough recent weight (a fresh report weighs 1.0).
    report_consensus_min_reporters: int = field(default_factory=lambda: _env_int("REPORT_CONSENSUS_MIN_REPORTERS", 3))
    report_consensus_min_weight: float = field(default_factory=lambda: _env_float("REPORT_CONSENSUS_MIN_WEIGHT", 1.5))
    # Vote weight at which the consensus reaches full influence over the GPS estimate
    report_full_confidence_weight: float = field(default_factory=lambda: _env_float("REPORT_FULL_CONFIDENCE_WEIGHT", 4.0))

    # Anti-spam: one report per passenger per bus within this many seconds
    report_cooldown_seconds: int = field(default_factory=lambda: _env_int("REPORT_COOLDOWN_SECONDS", 60))

    # GPS/AVL feed simulator (positions, speeds, stop dwell times - crowdedness itself is inferred, never injected)
    simulation_tick_seconds: float = field(default_factory=lambda: _env_float("SIMULATION_TICK_SECONDS", 2))
    simulation_speed_multiplier: float = field(default_factory=lambda: _env_float("SIMULATION_SPEED_MULTIPLIER", 3))
    # Rush-hour demand & traffic in the GPS simulator: auto (Baku 07:30-09:30, 17:30-19:30 weekdays) | always | never
    rush_hour_mode: str = field(default_factory=lambda: os.getenv("SIM_RUSH_HOUR", "auto").strip().lower())

    # Background workers (disabled in tests)
    enable_background_tasks: bool = field(default_factory=lambda: _env_bool("ENABLE_BACKGROUND_TASKS", True))

    # Static frontend served by the API when running without Docker/nginx
    frontend_dir: str = field(
        default_factory=lambda: os.getenv("FRONTEND_DIR", os.path.join(_APP_DIR, "..", "..", "frontend"))
    )
