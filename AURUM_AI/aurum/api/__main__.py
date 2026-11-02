"""python -m aurum.api - start the AURUM AI backend API (uvicorn)."""
from __future__ import annotations

import uvicorn

from aurum.cli import _settings
from aurum.api.app import create_app


def main() -> None:
    s = _settings()   # also configures logging with secret masking
    uvicorn.run(create_app(s), host=s.api_host, port=s.api_port, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
