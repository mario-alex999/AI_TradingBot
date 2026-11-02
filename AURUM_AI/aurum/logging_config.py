"""Structured logging: JSON lines to ``logs/aurum.log`` (rotating) + readable Rich console output.
A redaction filter masks configured secrets if they ever appear in a log message."""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from rich.logging import RichHandler

_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; ``extra={...}`` fields are included as top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in vars(record).items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class RedactSecretsFilter(logging.Filter):
    """Replace any configured secret value with ``***`` before the record is emitted."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 3]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            msg = record.getMessage()
            for s in self.secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, None
        return True


def setup_logging(level: str = "INFO", log_dir: Path | None = None, secrets: Iterable[str] = ()) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    redact = RedactSecretsFilter(secrets)
    console = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    console.setLevel(level)
    console.addFilter(redact)
    root.addHandler(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "aurum.log", maxBytes=10_000_000, backupCount=5,
                                                  encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        fh.addFilter(redact)
        root.addHandler(fh)
    for noisy in ("sqlalchemy.engine", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
