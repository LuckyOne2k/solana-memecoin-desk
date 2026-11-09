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
| Market cap (`desk/market_cap.py`) | active: passes only with reported circulating supply; upper bound can only reject; FDV separate |
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
- **Holders:** the largest token accounts (up to 20, Solana RPC) are a
  *sample*, never reported as the full owner distribution. Accounts are
  mapped to their owners and aggregated per owner. Excluded are only pool
  accounts (RugCheck pool vaults, market addresses, accounts labelled AMM,
  the DexPaprika pool address) and provable burn owners (incinerator,
  system program). Exchange and locker accounts are shown separately but
  still counted; unknown accounts are always counted.
  The unseen amount (total supply minus all sampled accounts) is exact,
  because the total supply comes from the chain. Shares are relative to total
  supply minus provable burns:
  - top-10 lower bound = 10 largest counted owners in the sample,
  - top-10 upper bound = lower bound + entire unseen amount (worst case: all
    unseen tokens belong to top-10 owners, e.g. via further accounts).
  Lower bound above 25 % → rejected. Upper bound at most 25 % with complete
  owner mapping and complete RugCheck labels → confirmed. Otherwise the check
  stays `incomplete`.
- **Developer share:** creator from RugCheck. Lower bound = creator holdings in
  the sample, upper bound = lower bound + unseen amount. Same decision rule
  with the 3 % limit.
- **Market cap:** price from GeckoTerminal, cross-checked with RugCheck
  (`data_quality.max_price_deviation_pct`). Conflicting or missing prices
  always leave the check unclear, even if a market cap is reported.
  - Upper bound = price × (on-chain total supply − provable burns). It can
    only reject (upper bound below the minimum); it never passes the check.
  - Passing requires the market cap reported by GeckoTerminal
    (`reported_circulating_supply`), two agreeing price sources, the on-chain
    supply, and a reported value not above the upper bound.
  - FDV (DexPaprika, GeckoTerminal, on-chain) and the upper bound are logged
    separately and never used as market cap.

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
