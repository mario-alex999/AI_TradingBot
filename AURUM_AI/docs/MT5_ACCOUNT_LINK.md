# Per-person MT5 account link (Phase 1)

After a person signs in with their AURUM username and password, a **Connect your MT5 account** pop-up asks them to
authorise their own MetaTrader 5 **DEMO** account (account number, broker server, password). AURUM then logs a dedicated
terminal in to that account and mirrors it **read-only** (balance, equity, deals, positions) until they sign out.

## How it works

```
browser ──POST /api/v1/mt5/connect──▶ API ──picks a free slot, inserts mt5_connections row (CONNECTING)
                                         └─ starts: python -m aurum.mt5link.connector  (password on stdin only)
connector ─▶ slot terminal (portable copy) ─▶ MT5Gateway: read-only wrapper + DEMO check
          ─▶ CONNECTED → AccountRecorder(login=…) syncs every MT5_LINK_SYNC_SECONDS
          ─▶ stops when: Disconnect, sign-out, session expiry/idle, user disabled
          ─▶ shutdown, end slot terminal, delete slot's saved logins, row DISCONNECTED
```

* **Several people at once:** the MetaTrader5 Python package drives one terminal per process, so each simultaneously
  linked account uses its own terminal copy (`MT5_SLOTS_DIR/slotN`). When all slots are taken the pop-up says so.
* **Data never mixes:** `account_deals` and `trade_records` carry `login` (keys `(login, ticket)` and `(login, id)`,
  migration `0011`). Existing rows were assigned to the single account in `account_snapshots` (or 0 if ambiguous).
* **Only your accounts:** the login must be in the person's `account_scope` (admins without a scope may link any).
  One account can be linked by one session at a time.
* **No stored broker passwords:** not in the database, files, command lines or logs. Use the investor (read-only)
  password where possible. The slot's `accounts.dat` is deleted on disconnect.
* **DEMO only, read-only:** non-DEMO accounts are refused (`NOT_DEMO`) and nothing is mirrored. No order is ever sent.
* **Crash safety:** a connector that stops sending heartbeats for 90 s (or never connects within
  `MT5_LINK_CONNECT_TIMEOUT_SECONDS`) is marked `LOST`; its process and terminal are ended and the slot is freed.

## Set up

```bash
python -m aurum.cli init-db                       # applies 0011 (adds login columns + mt5_connections)
python -m aurum.cli mt5-slots setup --count 2     # copies C:\Program Files\MetaTrader 5 into C:\AURUM\mt5\slot1..2
# .env: MT5_SLOT_COUNT=2   then restart the API
python -m aurum.cli mt5-slots status
```

Each linked slot uses roughly 200-400 MB of memory (terminal + connector); size `--count` to the machine.

## API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/mt5/status` | slots configured / ready / free |
| GET | `/api/v1/mt5/connection` | this sign-in session's link and the person's account scope |
| POST | `/api/v1/mt5/connect` | `{login, password, server}`; session + CSRF; permission `mt5:link` (people only); 6/min |
| POST | `/api/v1/mt5/disconnect` | ends the link |

Errors: `INVALID_LOGIN`, `INVALID_SERVER`, `INVALID_PASSWORD` (422), `ACCOUNT_NOT_IN_SCOPE` (403), `ACCOUNT_IN_USE`
(409), `ALL_SLOTS_BUSY`, `MT5_LINK_NOT_CONFIGURED` (503). Connector outcomes on the row: `NOT_DEMO`, `CONNECT_FAILED`,
`WRONG_ACCOUNT`, `SYNC_FAILED`, `CONNECTOR_LOST`.

## Not in Phase 1

Trade Management, Risk, Performance and Approvals still read all mirrored accounts together; Phase 2 filters them by the
signed-in person's linked account.
