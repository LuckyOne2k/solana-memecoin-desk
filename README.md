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
| Market data (DexPaprika pools, GeckoTerminal price / FDV / reported market cap) | active |
| Check chain (`desk/security_scan.py`) | active, single check chain |
| Token classification (`desk/token_classification.py`) | active: stablecoins and staking tokens excluded by verified mint address |
| Liquidity cross-check (`desk/data_quality.py`) | active: DexPaprika vs. RugCheck for the same pool |
| Holder distribution (`desk/holders.py`) | active: owner aggregation, pool/burn exclusion, confirmation rules |
| Market cap (`desk/market_cap.py`) | active: reported vs. estimated, FDV separate |
| `desk/rules.py`, `desk/risk.py`, `desk/debate.py` | kept, not wired yet |
| Paper trading, strategy, AI agents, app | not implemented |

## Check rules (phase 1)
A pool only reaches `ready_for_full_risk_screen` when every check is
confirmed. Missing, contradictory or invalid data never passes a check; it
leaves the pool `incomplete`.

- **Non-memecoins:** stablecoins and staking/yield tokens are excluded only by
  exact mint address (list verified via GeckoTerminal on 2026-11-09). Tokens
  that merely use such a symbol or name are marked `token_classification`
  unclear (possible impersonation).
- **Liquidity:** DexPaprika pool liquidity is compared with RugCheck's
  liquidity for the same pool. Deviation above
  `data_quality.max_liquidity_deviation_pct` → unclear. If both agree, the
  lower value must reach `reject_liquidity_below_usd`.
- **Holders:** the 20 largest token accounts (Solana RPC) are mapped to their
  owners and aggregated per owner. Excluded are only pool accounts (RugCheck
  pool vaults, market addresses, accounts labelled AMM, the DexPaprika pool
  address) and provable burn owners (incinerator, system program). Exchange
  and locker accounts are shown separately but still counted; unknown
  accounts are always counted. Shares are relative to total supply minus
  provable burns. The top-10 share is confirmed only if all accounts are
  mapped, RugCheck labels are complete, and at least 10 counted owners are in
  the sample (or the sample contains all accounts). Exceeding 25 % rejects
  even when unconfirmed.
- **Developer share:** creator from RugCheck. If the creator is not among the
  20 largest accounts, the smallest sampled account is used as upper bound.
- **Market cap:** price (GeckoTerminal, cross-checked with RugCheck within
  `data_quality.max_price_deviation_pct`) times supply.
  - `reported_circulating_supply`: market cap reported by GeckoTerminal
    (preferred; marked unclear as implausible if it exceeds the value of
    all non-burned tokens).
  - `estimate_total_supply`: price × on-chain total supply minus provable
    burns. This is an upper bound of the real market cap and is always
    marked as estimate. Below the minimum → rejected; above the maximum →
    unclear; in range → accepted only if
    `scanner.market_cap_allow_total_supply_estimate` is true.
  - FDV (DexPaprika, GeckoTerminal, on-chain) is logged separately and never
    used as market cap.

Known limitations: wallets of the same person cannot be linked; holdings of
an owner outside the 20 largest accounts are not seen; tokens still on the
pump.fun bonding curve have no RugCheck market data and stay `incomplete`.

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
