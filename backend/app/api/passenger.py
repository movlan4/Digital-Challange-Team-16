"""Passenger-only endpoints: crowdedness reports about a specific bus."""
from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_engine, require_passenger
from app.engine import TransitEngine
from app.models import Identity, Report, ReportIn, ReportReceipt

router = APIRouter(prefix="/api/passenger", tags=["passenger"])


@router.post("/reports", response_model=ReportReceipt, status_code=status.HTTP_201_CREATED)
async def submit_report(payload: ReportIn, response: Response, identity: Identity = Depends(require_passenger),
                        engine: TransitEngine = Depends(get_engine)):
    """Report how crowded a bus is (LOW / MEDIUM / HIGH).

    Idempotent per ``submission_id``: re-sending the same submission returns the
    original report with ``duplicate: true`` and HTTP 200 - it is never stored twice.
    """
    receipt = await engine.submit_report(identity, payload)
    if receipt.duplicate:
        response.status_code = status.HTTP_200_OK
    return receipt


@router.get("/reports", response_model=list[Report])
def my_reports(identity: Identity = Depends(require_passenger), engine: TransitEngine = Depends(get_engine)):
    """The signed-in passenger's own reports and their handling status."""
    return engine.my_reports(identity)
