"""Model prices and the monthly spending cap.

The cap is enforced by one conditional UPDATE, so concurrent reviews from the API and the CLI cannot both take the last
of the budget. A review reserves its worst-case cost before calling the API and is settled to the real cost afterwards.
Amounts are integer millionths of a dollar. A review that never settles (a crash) keeps its reservation, which errs on
the side of spending less.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aurum.database.connection import session_scope
from aurum.database.models import utcnow

from .db_models import AiReviewBudgetMonth

MICRO = 1_000_000
#: Sizes reservations: real text does not go below this many characters per token, so the input estimate stays above
#: the real count. JSON full of numbers runs at about 2.5-3.
CHARS_PER_TOKEN_FLOOR = 2


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


#: First-party Anthropic list prices, USD per million tokens. A model that is not listed is refused rather than guessed
#: at, because its cost could not be capped.
PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(4.00, 20.00),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00),
    "claude-haiku-5-5": ModelPrice(0.10, 0.50),
}


def month_key(at: datetime | None = None) -> str:
    return (at or utcnow()).strftime("%Y-%m")


def usd_to_micro(usd: float) -> int:
    return math.floor(usd * MICRO)


def micro_to_usd(micro: int) -> float:
    return round(micro / MICRO, 6)


def token_cost_micro(model: str, input_tokens: int, output_tokens: int, cache_write_tokens: int = 0,
                     cache_read_tokens: int = 0) -> int:
    """Cost in micro-USD, rounded up. This service does not request caching, so cache tokens should be zero; if any
    appear they are charged at the full input rate or above (cache writes cost 1.25x), keeping the ledger on the safe
    side."""
    p = PRICES[model]
    input_equiv = input_tokens + cache_read_tokens + 1.25 * cache_write_tokens
    return math.ceil(input_equiv * p.input_per_mtok + output_tokens * p.output_per_mtok)


def worst_case_micro(model: str, prompt_chars: int, max_tokens: int) -> int:
    return token_cost_micro(model, math.ceil(prompt_chars / CHARS_PER_TOKEN_FLOOR), max_tokens)


def committed_micro(engine: Engine, month: str) -> int:
    with session_scope(engine) as s:
        return s.execute(select(AiReviewBudgetMonth.committed_microusd).where(AiReviewBudgetMonth.month == month)
                         ).scalar_one_or_none() or 0


def ensure_month(engine: Engine, month: str) -> None:
    """Create the month's row. Two processes may race here; the loser's duplicate insert is harmless."""
    try:
        with session_scope(engine) as s:
            if s.get(AiReviewBudgetMonth, month) is None:
                s.add(AiReviewBudgetMonth(month=month, committed_microusd=0))
    except IntegrityError:
        pass


def try_reserve(session: Session, month: str, amount: int, cap: int) -> bool:
    """Atomically add ``amount`` if the month stays within ``cap``. The row must exist (see :func:`ensure_month`)."""
    col = AiReviewBudgetMonth.committed_microusd
    result = session.execute(update(AiReviewBudgetMonth)
                             .where(AiReviewBudgetMonth.month == month, col + amount <= cap)
                             .values(committed_microusd=col + amount, updated_at=utcnow()))
    return result.rowcount == 1


def adjust(session: Session, month: str, delta: int) -> None:
    """Settle a reservation: ``delta`` is real cost minus the amount reserved."""
    col = AiReviewBudgetMonth.committed_microusd
    session.execute(update(AiReviewBudgetMonth).where(AiReviewBudgetMonth.month == month)
                    .values(committed_microusd=col + delta, updated_at=utcnow()))
