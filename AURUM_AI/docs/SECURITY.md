# Module 05 security

- **No execution.** The module has no route, CLI command or function that places, changes or closes a trade.
  `ReadOnlyMT5` is unchanged, and the source scan covers `aurum/decision/`.
- **DEMO only.** Nothing here relaxes the existing demo-account checks.
- **Keys** stay in `.env`. They are never logged, returned or committed, and the AI audit is scrubbed.
- **Admin key.** Required for approve, reject, reopen, runs and config changes. The frontend keeps it in memory only, and
  sends it as a header. UI gating is a convenience: the backend authorises every call.
- **Prompt injection.** Headlines and other outside text are passed as data, and the dossier excludes news text. Output
  must match a strict schema with no extra fields, and prices the prompt did not contain are rejected
  (`unsupported_claim`).
- **LLM limits.** It can agree, caution or reject. It cannot create a candidate, move a level, raise confidence or
  increase a size.
- **Spending.** Claude uses an atomic monthly budget ledger and an hourly limit. Retries default to 0. The other
  providers are not budget-capped, and stay off until selected.
- **Fail safe.** Any AI failure leaves the candidate published with `aiReview.status = "unavailable"`, capped and not
  promoted.
- **Look-ahead.** See [TESTING.md](TESTING.md).
