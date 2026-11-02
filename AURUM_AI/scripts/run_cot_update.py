"""Weekly COT update runner (used by the "AURUM AI COT Update" scheduled task; safe to run by hand).

Runs ``python -m aurum.cli cot update`` in-process and appends its console output to logs/cot_update.txt.
The update is idempotent and catches up on every report missed while the PC was off. MT5 is not needed.
Exit code: 0 success, 2 partial, 1 failure (also written to the log file).
"""
from __future__ import annotations

import contextlib
import io
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from typer.testing import CliRunner   # runs the Typer app in-process and captures its output
    from aurum.cli import app
    log = ROOT / "logs" / "cot_update.txt"
    log.parent.mkdir(exist_ok=True)
    res = CliRunner().invoke(app, ["cot", "update"], catch_exceptions=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n===== cot update {datetime.now().astimezone().isoformat(timespec='seconds')} "
                f"exit={res.exit_code} =====\n{res.output}")
        if res.exception and not isinstance(res.exception, SystemExit):
            f.write(f"EXCEPTION: {type(res.exception).__name__}: {res.exception}\n")
    return res.exit_code


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()):
        code = main()
    sys.exit(code)
