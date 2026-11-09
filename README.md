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
| Holder distribution (`desk/holders.py`) | active: full holder list via Helius (`fetch_full_holder_list`), top-20 sample only as fallback (can never confirm) |
| Token-2022 extensions | active: permanent delegate and any extension not classified safe keep the pool `incomplete` |
| Market cap (`desk/market_cap.py`) | active: passes only with reported circulating supply; upper bound can only reject; FDV separate |
| `desk/rules.py`, `desk/risk.py`, `desk/debate.py` | kept, not wired yet |
| Paper trading, strategy, AI agents, app | not implemented |

## Check rules (phase 1)
A pool only reaches `ready_for_full_risk_screen` when every check is
confirmed. Missing, contradictory or invalid data never passes a check; it
leaves the pool `incomplete`.

- **Check order (fair rotation, `desk/check_queue.py`):** at most
  `scanner.max_security_checks_per_run` (10) tokens are checked per run.
  Tokens whose last *completed* security check in earlier scans
  (`events.jsonl`) ended `rejected` move to the back of the queue (oldest
  rejection first); all other tokens go first in DexPaprika order. This only
  changes *which* tokens get the budget: every selected token is checked
  fresh, old results are never reused, unselected pools stay `incomplete`,
  and rejected tokens come back once newer ones were rejected. Missing,
  damaged or incomplete logs fall back to the plain DexPaprika order. Each
  pool logs `check_queue_position`, `queue_reason` and
  `previous_rejection_at`; the summary logs `check_order_source`.
- **Non-memecoins:** stablecoins and staking/yield tokens are excluded only by
  exact mint address (list verified via GeckoTerminal on 2026-11-09). Tokens
  that merely use such a symbol or name are marked `token_classification`
  unclear (possible impersonation).
- **Liquidity:** DexPaprika pool liquidity is compared with RugCheck's
  liquidity for the same pool. Deviation above
  `data_quality.max_liquidity_deviation_pct` → unclear. If both agree, the
  lower value must reach `reject_liquidity_below_usd`.
- **Holders:** primary source is the complete list of all token accounts
  (`fetch_full_holder_list`, Helius `getProgramAccountsV2`, limits in
  `config.yaml` → `holders.max_pages` = 30, `holders.max_slot_span` = 150).
  It is accepted only if pagination, slots, supply before/after, page
  `count`, duplicates and the exact sum against the mint supply all check
  out; otherwise a fixed reason is logged (`full_holder_list_error`) and the
  scan falls back to the largest token accounts (up to 20). That fallback is
  a *sample*, never reported as the full owner distribution, and can only
  reject or stay unclear – it never confirms (`sample_cannot_confirm`).
  The sum check is not an atomic snapshot: opposite transfers of equal size
  between pages can cancel out. Accounts are
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
  Lower bound above 25 % → rejected. Upper bound at most 25 % with the full
  holder list (unseen amount 0), complete owner mapping and complete RugCheck
  labels → confirmed. Otherwise the check stays `incomplete`.
- **Token-2022:** `permanent_delegate_absent` and `token_extensions_safe` are
  mandatory checks. A permanent delegate (address logged), an unknown or
  unreadable extension, or any extension not explicitly classified safe
  (only metadata/group extensions are) keeps the pool `incomplete`. No
  rejection rule yet (separate decision).
- **Developer share:** creator from RugCheck. Lower bound = creator holdings in
  the sample, upper bound = lower bound + unseen amount. Same decision rule
  with the 3 % limit.
- **Market cap:** price from GeckoTerminal, cross-checked with RugCheck
  (`data_quality.max_price_deviation_pct`). Conflicting or missing prices
  always leave the check unclear, even if a market cap is reported.
  - Upper bound = price × (on-chain total supply − provable burns). With two
    agreeing price sources the higher price is used; the log shows
    `price_used_usd`, `price_used_source` and `price_used_basis`. The upper
    bound can only reject (below the minimum); it never passes the check.
  - The 10 % tolerance applies only to the comparison of the two price
    sources, never to the comparison of a reported market cap with the upper
    bound.
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
