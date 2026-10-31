"""Operator endpoints (operator or master admin): report inbox, per-bus density, alerts."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_engine, require_operator
from app.engine import TransitEngine
from app.models import (
    Alert, AlertStatus, BusDensity, CongestionLevel, Identity, Report, ReportStatus, ReportUpdateIn,
)

router = APIRouter(prefix="/api/operator", tags=["operator"], dependencies=[Depends(require_operator)])


@router.get("/reports", response_model=list[Report])
def reports(status: Optional[ReportStatus] = Query(None), limit: int = Query(100, ge=1, le=500),
            engine: TransitEngine = Depends(get_engine)):
    return engine.repo.list_reports(status, limit=limit)


@router.get("/reports/summary")
def reports_summary(engine: TransitEngine = Depends(get_engine)):
    counts = engine.repo.count_reports()
    return {s.value: counts.get(s.value, 0) for s in ReportStatus}


@router.patch("/reports/{report_id}", response_model=Report)
async def update_report(report_id: int, payload: ReportUpdateIn, identity: Identity = Depends(require_operator),
                        engine: TransitEngine = Depends(get_engine)):
    """Manually handle a report: OPEN -> IN_PROGRESS -> RESOLVED (never closed automatically)."""
    return await engine.update_report(report_id, payload, identity)


@router.get("/density", response_model=list[BusDensity])
def bus_density(level: Optional[CongestionLevel] = Query(None),
                reported_only: bool = Query(False, description="Only buses with a density estimate"),
                limit: int = Query(50, ge=1, le=1000), engine: TransitEngine = Depends(get_engine)):
    """Crowdedness of individual buses (passenger reports + GPS telemetry), most crowded first."""
    return engine.density_ranking(level, reported_only, limit)


@router.get("/alerts", response_model=list[Alert])
def alerts(status: Optional[AlertStatus] = Query(None), limit: int = Query(50, ge=1, le=200),
           engine: TransitEngine = Depends(get_engine)):
    return engine.repo.list_alerts(status, limit)


@router.post("/alerts/{alert_id}/acknowledge", response_model=Alert)
async def acknowledge(alert_id: int, identity: Identity = Depends(require_operator),
                      engine: TransitEngine = Depends(get_engine)):
    return await engine.set_alert_status(alert_id, AlertStatus.ACKNOWLEDGED, identity)


@router.post("/alerts/{alert_id}/resolve", response_model=Alert)
async def resolve(alert_id: int, identity: Identity = Depends(require_operator),
                  engine: TransitEngine = Depends(get_engine)):
    return await engine.set_alert_status(alert_id, AlertStatus.RESOLVED, identity)


@router.post("/anomaly/run", response_model=list[Alert])
async def run_anomaly_scan(engine: TransitEngine = Depends(get_engine)):
    """Sample hybrid density and run the anomaly job now (it also runs on a schedule)."""
    engine.record_density_samples(engine.clock.now())
    return await engine.monitor.run_once()
