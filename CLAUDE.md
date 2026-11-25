# kalshi-scan: handoff notes

Read-only Kalshi mispricing scanner. Never place orders or sign in anywhere.

## How it runs
- `.github/workflows/scan.yml` runs hourly at :37 UTC (minute matches ET), plus manual dispatch.
  - Inputs: `test_alert` (force ntfy test), `selftest` (order-book parsing check only, no results commit).
- `kalshi_scan.py --min-edge 3 --no-spreads --max-days 90 --min-profit 10 --top 10` writes flags; the workflow commits them to `results/latest.txt`.
- A Claude scheduled task ("Kalshi mispricing scan", hourly at :57 ET) reads `results/latest.txt` and push-notifies the owner only for new `SIGNIFICANT` flags, plus a once-only alert if the scan is stale/failing.
- ntfy step is optional (needs `NTFY_TOPIC` secret; not set).

## Scanner logic
- Bracket arb: mutually exclusive events; requires a catch-all leg ("Other", "Someone else", ...) and asks summing >= 85c.
- Ladder inversion: grouped per underlying (ticker after event prefix, trailing strike stripped), distinct strikes only.
- Every flag gets a live review: market open, >= 10 contracts at quoted price, traded in 24h. Profit = edge x executable size. SIGNIFICANT = review passed and >= $10.
- Kalshi API now returns `*_dollars` prices, `*_fp` sizes/volumes, and `orderbook_fp` with `yes_dollars`/`no_dollars` levels. `cents()`, `num()`, `_levels()` handle old and new formats. Run the `selftest` dispatch after any parsing change.

## Status (2026-11-24)
- Self-test passes on live markets; full scans succeed and currently come back clean.
- Repo is private. Owner wants it public; visibility can't be changed from Claude's GitHub proxy, so the owner has to do it in Settings. No secrets in files or history.
- Not yet verified end to end: a real Claude push alert (no SIGNIFICANT flag has occurred yet). One way to test: commit a fake SIGNIFICANT `results/latest.txt` marked TEST, let the :57 check fire, then re-run the workflow to overwrite it.
- Fee model is an approximation of Kalshi's taker fee: ceil(0.07 * C * P * (1-P)).
