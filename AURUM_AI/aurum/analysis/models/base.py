"""Shared helpers for Module 03 result models."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


def plain(value: Any) -> Any:
    """Convert dataclasses / numpy / pandas values into JSON-friendly Python values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.generic):
        v = value.item()
        return None if isinstance(v, float) and np.isnan(v) else v
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


class Serializable:
    def to_dict(self) -> dict[str, Any]:
        return plain(self)


def ts(value: Any) -> datetime:
    return pd.Timestamp(value).to_pydatetime()
