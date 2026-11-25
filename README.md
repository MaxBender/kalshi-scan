# kalshi-scan

Read-only scanner for Kalshi pricing inconsistencies. Runs twice a day on GitHub Actions and pushes alerts to your phone via [ntfy](https://ntfy.sh).

## What it flags
- **Bracket arb**: mutually exclusive outcomes whose YES asks sum below $1 (or bids above $1) after estimated fees.
- **Ladder inversion**: a higher "above X" strike bid above a lower strike's ask.

Only flags with ≥3¢ estimated edge are sent, top 5 per run. Nothing is sent when nothing qualifies.

## Alerts
Each run writes `results/latest.txt`. A Claude scheduled task reads it ~15 minutes later and sends a push notification through the Claude app if anything is flagged. ntfy (below) is optional.

## Optional ntfy setup
1. Install the ntfy app and subscribe to a long, random topic name.
2. In this repo: Settings → Secrets and variables → Actions → New repository secret: `NTFY_TOPIC` = that topic name.
3. Actions tab → "Kalshi scan" → Run workflow → tick "Send a test notification".

## Caveats
- Top-of-book prices only; depth may be tiny.
- Fee model is an approximation of Kalshi's taker fee.
- Bracket flags assume the listed outcomes are exhaustive; check the event rules.
- GitHub pauses scheduled workflows after 60 days without repo activity.
- Data flags, not trading advice. The scanner never places orders.

Run locally: `pip install requests && python kalshi_scan.py --min-edge 3 --no-spreads`
