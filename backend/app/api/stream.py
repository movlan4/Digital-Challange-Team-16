"""Server-Sent Events: a public bus feed and an operator-only feed."""
import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_engine, require_operator, require_signed_in
from app.engine import TransitEngine
from app.services.events import OPERATOR, PUBLIC, format_sse

router = APIRouter(prefix="/api", tags=["realtime"])

KEEPALIVE_SECONDS = 15


def _sse(request: Request, engine: TransitEngine, audiences: frozenset[str], snapshot: dict) -> StreamingResponse:
    queue = engine.events.subscribe(audiences)

    async def event_source():
        try:
            yield format_sse("snapshot", snapshot)
            while not await request.is_disconnected():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            engine.events.unsubscribe(queue)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_source(), media_type="text/event-stream", headers=headers)


@router.get("/stream", dependencies=[Depends(require_signed_in)])
async def public_stream(request: Request, engine: TransitEngine = Depends(get_engine)):
    """Live bus positions, per-bus and per-line crowdedness."""
    return _sse(request, engine, frozenset({PUBLIC}), engine.live_payload())


@router.get("/operator/stream", dependencies=[Depends(require_operator)])
async def operator_stream(request: Request, engine: TransitEngine = Depends(get_engine)):
    """Buses and line density plus passenger reports and alerts the moment they happen."""
    return _sse(request, engine, frozenset({PUBLIC, OPERATOR}), engine.operator_snapshot())
