"""/api/v1/ai-review/* - optional advisory AI review (off by default; no trading endpoints).

GET  /ai-review/status     whether it is on, and this month's budget (never returns the key)
GET  /ai-review/reviews    stored reviews, newest first (?symbol=&limit=)
POST /ai-review/reviews    admin: review one instrument now. Spends budget, so it needs X-AURUM-Admin-Key.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from aurum.ai_review.service import AiReviewService, AiReviewUnavailable, BudgetExceeded, NoMarketData, ReviewFailed
from aurum.analysis.services.analysis_service import UnknownInstrumentError as AnalysisUnknownInstrument
from aurum.api.app import AdminDep, AppState, camelize
from aurum.market_intelligence.service import UnknownInstrumentError as IntelligenceUnknownInstrument


class ReviewRequest(BaseModel):
    symbol: str


def build_router(state: AppState, service: AiReviewService | None = None, intelligence: Any = None,
                 analysis: Any = None) -> APIRouter:
    r = APIRouter()
    svc = service or AiReviewService(state.settings, state.engine, intelligence, analysis)

    @r.get("/status")
    def status() -> dict[str, Any]:
        return camelize(svc.status())

    @r.get("/reviews")
    def reviews(symbol: str | None = None, limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
        return camelize(svc.history(symbol, limit))

    @r.post("/reviews", dependencies=[AdminDep])
    def create(body: ReviewRequest) -> dict[str, Any]:
        try:
            return camelize(svc.run(body.symbol))
        except (AnalysisUnknownInstrument, IntelligenceUnknownInstrument) as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except AiReviewUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except NoMarketData as exc:
            raise HTTPException(409, str(exc)) from exc
        except BudgetExceeded as exc:
            raise HTTPException(402, str(exc)) from exc
        except ReviewFailed as exc:
            raise HTTPException(502, str(exc)) from exc

    return r
