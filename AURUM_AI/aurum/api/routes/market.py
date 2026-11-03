"""Module 01 market-data API (read-only): instruments, quotes, candles, specifications, data quality,
collection logs, CSV/Parquet export, and a Server-Sent Events quote stream."""
from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

import anyio
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from aurum.api.market_queries import TIMEFRAMES, MarketQueries
from aurum.database.repository import Repository

STREAM_POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0


async def quote_events(mq: MarketQueries, is_disconnected: Callable[[], Awaitable[bool]],
                       wanted: list[str] | None = None) -> AsyncIterator[str]:
    """SSE frames: a quote whenever a stored quote changes, a keep-alive comment when idle."""
    last: dict[str, tuple[Any, Any, Any]] = {}
    idle = 0.0
    yield "retry: 3000\n\n"
    while not await is_disconnected():
        quotes = await anyio.to_thread.run_sync(lambda: mq.quotes(wanted))
        sent = False
        for q in quotes:
            key = (q["bid"], q["ask"], q["updatedAt"])
            if last.get(q["symbol"]) != key:
                last[q["symbol"]] = key
                yield f"event: quote\ndata: {json.dumps(q)}\n\n"
                sent = True
        idle = 0.0 if sent else idle + STREAM_POLL_SECONDS
        if idle >= HEARTBEAT_SECONDS:
            idle = 0.0
            yield ": keep-alive\n\n"
        await asyncio.sleep(STREAM_POLL_SECONDS)


def build_router(state: Any) -> APIRouter:
    r = APIRouter()
    mq = MarketQueries(state.engine)

    @r.get("/instruments")
    def instruments() -> list[dict[str, Any]]:
        return mq.instruments()

    @r.get("/quotes")
    def quotes(symbols: str | None = Query(None, description="Comma-separated canonical symbols")) -> list[dict[str, Any]]:
        return mq.quotes([s for s in symbols.split(",") if s] if symbols else None)

    @r.get("/candles")
    def candles(symbol: str, timeframe: str = Query(..., pattern="^(D1|H4|H1|M15|M5)$"),
                limit: int = Query(500, ge=1, le=5000)) -> list[dict[str, Any]]:
        try:
            return mq.candles(symbol, timeframe, limit)
        except KeyError:
            raise HTTPException(404, f"Unknown instrument {symbol!r}.") from None

    @r.get("/specifications")
    def specifications() -> list[dict[str, Any]]:
        return mq.specifications()

    @r.get("/data-quality")
    def data_quality() -> dict[str, Any]:
        return mq.data_quality()

    @r.get("/collection-logs")
    def collection_logs(page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=200), search: str | None = None,
                        instrument: str | None = None, timeframe: str | None = None,
                        status: Literal["passed", "warning", "failed"] | None = None) -> dict[str, Any]:
        return mq.collection_logs(page, page_size, search, instrument, timeframe, status)

    @r.get("/export")
    def export(format: Literal["csv", "parquet"] = "csv", symbol: str | None = None, timeframe: str | None = None) -> Response:
        """Stored, validated candles. Without symbol/timeframe every stored series is included."""
        repo = Repository(state.engine)
        series = [(s, tf) for s, tf, _ in repo.available_series()
                  if (not symbol or s == symbol.upper()) and (not timeframe or tf == timeframe.upper())]
        if not series:
            raise HTTPException(404, "No stored candles match. Run a collection first.")
        frames = [repo.load_candles(s, tf) for s, tf in series]
        df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
        cols = [c for c in ("symbol", "timeframe", "ts_utc", "open", "high", "low", "close", "tick_volume", "real_volume",
                            "spread", "quality_status", "quality_flags") if c in df.columns]
        df = df[cols]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        name = f"aurum_{(symbol or 'all').upper()}_{(timeframe or 'all').upper()}_{stamp}"
        if format == "csv":
            body = df.to_csv(index=False).encode()
            media = "text/csv"
        else:
            buf = io.BytesIO()
            df.to_parquet(buf, index=False)
            body = buf.getvalue()
            media = "application/vnd.apache.parquet"
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'})

    @r.get("/stream/quotes")
    async def stream_quotes(request: Request, symbols: str | None = None) -> StreamingResponse:
        """Server-Sent Events: `event: quote` with a Quote JSON whenever a stored quote changes.

        Quotes come from the database, so this works with `aurum.cli stream-ticks` writing ticks from MT5.
        """
        wanted = [s for s in symbols.split(",") if s] if symbols else None
        return StreamingResponse(quote_events(mq, request.is_disconnected, wanted), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return r


__all__ = ["build_router", "TIMEFRAMES"]
