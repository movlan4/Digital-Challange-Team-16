"""SQLite persistence: users & roles, sessions, password resets, passenger reports, alerts.

Static network data lives in memory (app/data/network.json); bus positions
live in the GPS simulator. Only data that must survive restarts and be
auditable is stored here.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from app.models import Alert, AlertStatus, CongestionLevel, Report, ReportStatus, Role, UserOut

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('passenger', 'operator', 'admin')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON user_sessions(user_id);

-- one active reset code per user; only a hash of the code is stored
CREATE TABLE IF NOT EXISTS password_resets (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    code_hash   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bus_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    passenger_id    INTEGER NOT NULL REFERENCES users(id),
    submission_id   TEXT NOT NULL,
    bus_id          TEXT NOT NULL,
    route_id        TEXT NOT NULL,
    stop_id         TEXT,
    level           TEXT NOT NULL CHECK (level IN ('LOW','MEDIUM','HIGH')),
    comment         TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    handled_by      TEXT,
    resolution_note TEXT,
    -- deduplication: one passenger submission == exactly one row
    UNIQUE (passenger_id, submission_id)
);
CREATE INDEX IF NOT EXISTS ix_bus_reports_bus_time ON bus_reports(bus_id, created_at);
CREATE INDEX IF NOT EXISTS ix_bus_reports_time ON bus_reports(created_at);
CREATE INDEX IF NOT EXISTS ix_bus_reports_status ON bus_reports(status, created_at);

CREATE TABLE IF NOT EXISTS bus_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id    TEXT NOT NULL,
    bus_id      TEXT NOT NULL,
    message     TEXT NOT NULL,
    status      TEXT NOT NULL,
    high_since  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    handled_by  TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_route_status ON bus_alerts(route_id, status);
"""

REPORT_SELECT = """
SELECT r.*, u.email AS passenger_email FROM bus_reports r
JOIN users u ON u.id = r.passenger_id
"""


def _iso(dt: datetime) -> str:
    # Fixed-width UTC format so lexicographic order == chronological order in SQL.
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class Repository:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, database_path: str = ":memory:", route_numbers: Optional[dict[str, str]] = None) -> None:
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._route_numbers = route_numbers or {}
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    # ---------- Users ----------

    def create_user(self, email: str, password_hash: str, role: Role, now: datetime) -> Optional[int]:
        try:
            cur = self._write("INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                              (email, password_hash, role.value, _iso(now)))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # email already registered

    def upsert_admin(self, email: str, password_hash: str, now: datetime) -> None:
        self._write(
            "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)"
            " ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash, role = 'admin'",
            (email, password_hash, _iso(now)),
        )

    def user_by_email(self, email: str) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM users WHERE email = ?", (email,))

    def user_by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM users WHERE id = ?", (user_id,))

    def list_users(self, query: Optional[str] = None, role: Optional[Role] = None, limit: int = 200) -> list[UserOut]:
        sql, params = "SELECT * FROM users WHERE 1=1", []
        if query:
            sql += " AND email LIKE ?"
            params.append(f"%{query.lower()}%")
        if role:
            sql += " AND role = ?"
            params.append(role.value)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        return [self.to_user(r) for r in self._all(sql, tuple(params))]

    def set_role(self, user_id: int, role: Role) -> None:
        self._write("UPDATE users SET role = ? WHERE id = ?", (role.value, user_id))

    def set_password(self, user_id: int, password_hash: str) -> None:
        self._write("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    @staticmethod
    def to_user(row: sqlite3.Row) -> UserOut:
        return UserOut(id=row["id"], email=row["email"], role=Role(row["role"]), created_at=_parse(row["created_at"]))

    # ---------- Sessions ----------

    def create_session(self, token_hash: str, user_id: int, expires_at: datetime) -> None:
        self._write("INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                    (token_hash, user_id, _iso(expires_at)))

    def session_user(self, token_hash: str, now: datetime) -> Optional[sqlite3.Row]:
        """The user behind a valid session. The role is read live, so role changes apply at once."""
        return self._one(
            "SELECT u.* FROM user_sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ?",
            (token_hash, _iso(now)),
        )

    def delete_session(self, token_hash: str) -> None:
        self._write("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: int) -> None:
        self._write("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))

    # ---------- Password resets ----------

    def save_reset_code(self, user_id: int, code_hash: str, expires_at: datetime, now: datetime) -> None:
        self._write(
            "INSERT INTO password_resets (user_id, code_hash, expires_at, attempts, created_at) VALUES (?, ?, ?, 0, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET code_hash = excluded.code_hash, expires_at = excluded.expires_at,"
            " attempts = 0, created_at = excluded.created_at",
            (user_id, code_hash, _iso(expires_at), _iso(now)),
        )

    def reset_code(self, user_id: int) -> Optional[sqlite3.Row]:
        return self._one("SELECT * FROM password_resets WHERE user_id = ?", (user_id,))

    def count_reset_attempt(self, user_id: int) -> None:
        self._write("UPDATE password_resets SET attempts = attempts + 1 WHERE user_id = ?", (user_id,))

    def delete_reset_code(self, user_id: int) -> None:
        self._write("DELETE FROM password_resets WHERE user_id = ?", (user_id,))

    # ---------- Passenger reports ----------

    def insert_report_once(
        self, passenger_id: int, submission_id: str, bus_id: str, route_id: str, stop_id: Optional[str],
        level: CongestionLevel, comment: Optional[str], now: datetime,
    ) -> tuple[Report, bool]:
        """Insert a report exactly once per (passenger, submission_id).

        Returns (report, created). A repeated submission (retry, double click,
        network replay) returns the original row with created=False. The UNIQUE
        constraint makes this safe even for concurrent requests.
        """
        cur = self._write(
            "INSERT INTO bus_reports (passenger_id, submission_id, bus_id, route_id, stop_id, level, comment,"
            " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)"
            " ON CONFLICT(passenger_id, submission_id) DO NOTHING",
            (passenger_id, submission_id, bus_id, route_id, stop_id, level.value, comment, _iso(now), _iso(now)),
        )
        return self.find_submission(passenger_id, submission_id), cur.rowcount == 1

    def find_submission(self, passenger_id: int, submission_id: str) -> Optional[Report]:
        row = self._one(REPORT_SELECT + " WHERE r.passenger_id = ? AND r.submission_id = ?",
                        (passenger_id, submission_id))
        return self._row_to_report(row) if row else None

    def last_report_time(self, passenger_id: int, bus_id: str) -> Optional[datetime]:
        row = self._one("SELECT MAX(created_at) AS ts FROM bus_reports WHERE passenger_id = ? AND bus_id = ?",
                        (passenger_id, bus_id))
        return _parse(row["ts"]) if row else None

    def get_report(self, report_id: int) -> Optional[Report]:
        row = self._one(REPORT_SELECT + " WHERE r.id = ?", (report_id,))
        return self._row_to_report(row) if row else None

    def list_reports(self, status: Optional[ReportStatus] = None, passenger_id: Optional[int] = None,
                     limit: int = 100) -> list[Report]:
        sql, params = REPORT_SELECT + " WHERE 1=1", []
        if status:
            sql += " AND r.status = ?"
            params.append(status.value)
        if passenger_id is not None:
            sql += " AND r.passenger_id = ?"
            params.append(passenger_id)
        sql += " ORDER BY r.id DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_report(r) for r in self._all(sql, tuple(params))]

    def reports_since(self, since: datetime) -> list[Report]:
        rows = self._all(REPORT_SELECT + " WHERE r.created_at >= ? ORDER BY r.created_at", (_iso(since),))
        return [self._row_to_report(r) for r in rows]

    def count_reports(self) -> dict[str, int]:
        rows = self._all("SELECT status, COUNT(*) AS n FROM bus_reports GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def update_report(self, report_id: int, status: ReportStatus, operator: str,
                      note: Optional[str], now: datetime) -> Optional[Report]:
        self._write(
            "UPDATE bus_reports SET status = ?, handled_by = ?, resolution_note = COALESCE(?, resolution_note),"
            " updated_at = ? WHERE id = ?",
            (status.value, operator, note, _iso(now), report_id),
        )
        return self.get_report(report_id)

    def _row_to_report(self, row: sqlite3.Row) -> Report:
        return Report(
            id=row["id"], passenger_email=row["passenger_email"], bus_id=row["bus_id"],
            route_id=row["route_id"], route_number=self._route_numbers.get(row["route_id"], row["route_id"]),
            stop_id=row["stop_id"], level=CongestionLevel(row["level"]), comment=row["comment"],
            status=ReportStatus(row["status"]), created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]), handled_by=row["handled_by"],
            resolution_note=row["resolution_note"],
        )

    # ---------- Alerts ----------

    def create_alert(self, route_id: str, bus_id: str, message: str, high_since: datetime, now: datetime) -> Alert:
        cur = self._write(
            "INSERT INTO bus_alerts (route_id, bus_id, message, status, high_since, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (route_id, bus_id, message, AlertStatus.ACTIVE.value, _iso(high_since), _iso(now), _iso(now)),
        )
        return self.get_alert(cur.lastrowid)  # type: ignore[return-value]

    def get_alert(self, alert_id: int) -> Optional[Alert]:
        row = self._one("SELECT * FROM bus_alerts WHERE id = ?", (alert_id,))
        return self._row_to_alert(row) if row else None

    def open_alerts(self) -> list[Alert]:
        rows = self._all("SELECT * FROM bus_alerts WHERE status != ? ORDER BY id DESC", (AlertStatus.RESOLVED.value,))
        return [self._row_to_alert(r) for r in rows]

    def last_resolved_alert_times(self) -> dict[str, datetime]:
        rows = self._all("SELECT route_id, MAX(updated_at) AS ts FROM bus_alerts WHERE status = ? GROUP BY route_id",
                         (AlertStatus.RESOLVED.value,))
        return {r["route_id"]: _parse(r["ts"]) for r in rows}

    def list_alerts(self, status: Optional[AlertStatus] = None, limit: int = 50) -> list[Alert]:
        sql, params = "SELECT * FROM bus_alerts", []
        if status:
            sql += " WHERE status = ?"
            params.append(status.value)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_alert(r) for r in self._all(sql, tuple(params))]

    def set_alert_status(self, alert_id: int, status: AlertStatus, operator: str, now: datetime) -> Optional[Alert]:
        self._write("UPDATE bus_alerts SET status = ?, handled_by = ?, updated_at = ? WHERE id = ?",
                    (status.value, operator, _iso(now), alert_id))
        return self.get_alert(alert_id)

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> Alert:
        return Alert(
            id=row["id"], route_id=row["route_id"], bus_id=row["bus_id"], message=row["message"],
            status=AlertStatus(row["status"]), high_since=_parse(row["high_since"]),
            created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
            handled_by=row["handled_by"],
        )
