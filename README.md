# Solana Memecoin Desk — Paper Trading MVP

**Research and paper trading only. No wallet connection, signing, or live orders.**

## Strategy configuration
All thresholds live in `config.yaml`, the single source of truth. It is
loaded and validated by `desk/config.py` at startup. Missing or implausible
values stop the program; `mode` must be `paper`.

- Pair age: 30–72 hours
- Market cap: $300,000–$20,000,000
- Reject if mint authority is not revoked
- Reject if liquidity is below $40,000
- Reject if LP is not verifiably burned/locked (RugCheck ≥ 99.99 %)
- Reject if Top 10 holders exceed 25%
- Reject if developer holdings exceed 3%
- Bear: at least 3 concrete on-chain objections before every simulated entry
- Analyst: must rebut each objection with sourced evidence; unresolved objection vetoes
- Paper position size: max 8% of portfolio
- Initial stop: −6%; trailing stop activates at +12%
- All scans, debates, paper entries and exits are logged

> Important: these thresholds are risk filters, not proof of safety. Holder attribution is imperfect, lock contracts can expire, and API data may be stale or manipulated. The project is not financial advice.

## Project status
| Part | Status |
|---|---|
| Market data (DexPaprika, GeckoTerminal market cap) | active |
| Security chain (`desk/security_scan.py`: RugCheck + Solana RPC holders) | active, single check chain |
| `desk/rules.py` (full risk screen incl. market cap range) | kept, not wired yet (planned for phase 1) |
| `desk/risk.py` (position size, stops) | kept, not wired yet (planned for paper broker, phase 2) |
| `desk/debate.py` (formal debate check) | kept, not wired yet; may only veto, never approve |
| Paper trading, strategy, AI agents, app | not implemented |

Known limitations (to be fixed in phase 1): no pool currently reaches
`ready_for_full_risk_screen`, because the top-10 and developer shares are
never fully verified; GeckoTerminal rarely returns a market cap; the
market cap range is not yet applied in the scan.

## Install locally
1. Python 3.11+
2. `python -m venv .venv`
3. Activate the environment (`.venv\Scripts\activate` on Windows)
4. `pip install -r requirements.txt`
5. Copy `.env.example` to `.env` (no secrets are needed for the demo)
6. `python -m desk` (run the scan), `python -m pytest` (run the tests)
7. Optional: `python -m desk.market_data` (connection diagnostics without security checks)

## GitHub Actions
- **Tests** (`.github/workflows/tests.yml`): runs on every pull request and on
  `main`. Read-only, no secrets, no network calls to market APIs.
- **Solana Memecoin Desk** (`.github/workflows/scan.yml`): started manually.
  Runs the tests first, then one scan. The event log is written to the
  separate branch **`desk-data`** (`events.jsonl`), never to `main`.
  On the first run the branch is created and the old log from
  `data/events.jsonl` is copied over. The old file on `main` stays
  unchanged as an archive.
- The scan code runs without GitHub write credentials; the token is only
  used in the final step that pushes the log.

## Safety
- Never place API keys, seed phrases, private keys, or wallet secrets in repository files.
- Keep the repository public only if you are comfortable exposing all source code and logs.
- Live trading is intentionally not implemented.
