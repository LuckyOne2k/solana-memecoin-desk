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
| Token-2022 extensions | active: proven risky extensions reject; unknown, unreadable or not classified safe keep the pool `incomplete` |
| Mint/freeze authority cross-check | active: RPC (`fetch_mint_info`) vs. RugCheck; disagreement or missing value keeps the pool `incomplete` |
| Market cap (`desk/market_cap.py`) | active: point value passes only with reported circulating supply; otherwise only the on-chain range can confirm; upper bound can only reject; FDV separate |
| On-chain market cap range (`desk/circulating_bounds.py`) | implemented, **on by default** (`market_cap_onchain_bounds.allow_onchain_bounds: true`; `false` switches it off) |
| `desk/risk.py`, `desk/debate.py` | kept, not wired yet |
| Paper trading, strategy, AI agents, app | not implemented |

## Check rules (phase 1)
A pool only reaches `ready_for_full_risk_screen` when every check is
confirmed. Missing, contradictory or invalid data never passes a check; it
leaves the pool `incomplete`.

- **Check order (fair rotation, `desk/check_queue.py`):** at most
  `scanner.max_security_checks_per_run` (10) tokens are checked per run.
  Two groups, each ordered by waiting time (longest first), read from the
  recent scans in `events.jsonl`: (1) tokens whose last check attempt
  (completed or error) did *not* end `rejected` – never checked, last
  incomplete/error, last ready; (2) tokens whose last attempt ended
  `rejected` (kept at the back, as before). Waiting time = scans since the
  last attempt; for never-checked tokens scans since first appearance + 1
  (brand new = 1). Ties: never checked, then incomplete, then ready, then
  DexPaprika order. So new tokens go ahead of everything checked in the last
  scan, a ready token does not take a slot every run, and no non-rejected
  token is skipped permanently. This only changes *which* tokens get the
  budget: every selected token is checked fresh, old results are never
  reused, unselected pools stay `incomplete` (also a previously ready one).
  Missing, damaged or incomplete logs fall back to the plain DexPaprika
  order. Each pool logs `check_queue_position`, `queue_reason`
  (`never_checked_in_recent_scans`, `recheck_after_incomplete`,
  `recheck_after_ready`, `recheck_after_rejection`) and
  `previous_rejection_at`; the summary logs `check_order_source` and
  `deprioritized_tokens` (number of tokens rechecked after a rejection).
- **Non-memecoins:** stablecoins and staking/yield tokens are excluded only by
  exact mint address (list verified via GeckoTerminal on 2026-11-09). Tokens
  that merely use such a symbol or name are marked `token_classification`
  unclear (possible impersonation).
- **LP burn/lock:** decided on RugCheck's token-wide `lpLockedPct`
  (summary, all pools of the token) against `risk_filters.lp_locked_min_pct`
  (99.99). Logged in addition, without any evaluation: `lp.token_lp_locked_pct`,
  per RugCheck market `pool_address`, `market_type`, `lp_locked_pct`,
  `lp_locked_usd`, `is_evaluated_pool` (first 20 markets; the evaluated pool
  is always added, even beyond position 20), `markets_total_count`,
  `markets_truncated`, plus the evaluated pool's own values
  (`evaluated_pool_*`, `evaluated_pool_found_in_markets`,
  `evaluated_pool_market_position`). Example MYWAY
  (scan #57): evaluated pool 100 % locked, token-wide 99.94 % because of two
  tiny unlocked pools → rejected. A pool-based LP rule is a separate decision.
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
  mandatory checks. Rejected only on a proven risk read from the extension
  state (`classify_token_extension` in `desk/security_scan.py`): permanent
  delegate with an address, `nonTransferable`, `pausableConfig` with an
  authority or `paused: true`, `defaultAccountState` `frozen`,
  `transferHook` with a program or an authority, `transferFeeConfig` with a
  fee above 0 or a fee authority. Everything else that is not explicitly
  classified safe (only metadata/group extensions are) – including inactive
  delegate/hook/fee, unknown or unreadable extensions, missing or mistyped
  fields – keeps the pool `incomplete`. After a rejection no holder queries.
- **Mint/freeze authority cross-check:** mandatory check
  `authority_cross_check`. `mintAuthority`/`freezeAuthority` from the RPC
  (`null` = revoked) must match RugCheck for both authorities. Disagreement
  or a missing/unclear value → `incomplete`, never ready.
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
  - GeckoTerminal token requests use bounded retries: network errors,
    HTTP 429 and HTTP 5xx at most 3 attempts (waits 10 s / 30 s, `Retry-After`
    honoured up to 60 s); HTTP 403 at most 2 attempts; other HTTP errors and
    invalid responses are not retried. If the last attempt fails, no
    GeckoTerminal prices are used at all (no stale values). Attempts, HTTP
    status and reason are logged in `market_cap_diagnostics`
    (`request_attempts`, `retries`, `last_http_status`, `error_reason`,
    `request_log`).
- **On-chain market cap range (on by default, `false` switches it off):** only when the
  switch `market_cap_onchain_bounds.allow_onchain_bounds` is `true` and the
  market cap is the *only* open check (all other checks confirmed, mint
  and freeze authority revoked, no permanent delegate, only safe Token-2022
  extensions, full holder list, two agreeing prices).
  - Lower amount L = **heuristic wallet-like lower bound**: tokens held by
    authorities that technically look like normal wallets. It is never an
    official, actual or economically proven circulating supply. It does
    **not** prove that a private key exists or that the tokens are freely
    transferable. Token accounts are first aggregated per
    token authority (bytes 32–63 of the account data, never the technical
    account owner, which is always the token program). Excluded from L:
    the 10 largest authorities, pool vaults and pool addresses (RugCheck
    and all DexPaprika pools of the token), burn addresses, creator,
    exchange and locker labels, off-curve authorities (PDAs, no RPC call)
    and frozen token accounts (one `getProgramAccountsV2` call with a
    state filter). The remaining authorities are checked largest first
    with `getMultipleAccounts` (up to 100 per call, at most
    `max_account_calls_per_token` = 10 calls per token and scan,
    `minContextSlot` = end of the holder list; hard limit 10, a higher
    value stops the program). Only an on-curve address whose account is
    owned by the System Program, not executable, with 0 data bytes and
    SOL > 0 counts as wallet-like. Authorities without an account (`null`), program
    or token accounts, executables and system accounts with data are not
    counted (unknown).
  - **Residual risk:** an on-curve address does not need a known private
    key. Any 32-byte value that happens to be a valid curve point can be a
    token authority and receive SOL (placeholder or vanity addresses, lost
    keys). Such tokens can never move but are counted as wallet-like. Only
    a signature would prove a key; this is not checked and not assumed.
    The log marks this with `lower_bound_kind`,
    `private_key_proven: false`, `free_transferability_proven: false`,
    `economic_circulating_supply_proven: false` and `residual_risk`.
  - Upper amount U = on-chain supply − provable burns (as before).
  - Decision: high price × U < minimum → rejected; low price × L > maximum
    → rejected; low price × L ≥ minimum and high price × U ≤ maximum →
    confirmed (`basis: onchain_bounds`, no point value, `market_cap_usd`
    stays empty); otherwise unclear. Exact comparisons.
  - Any RPC error, invalid response, slot going backwards, slot span from
    the holder list to the last query above `max_slot_span` (150), or the
    call limit reached before the decision → unclear (`incomplete`).
  - Cost: 1 credit for the frozen check plus at most 10 `getMultipleAccounts`
    calls per token; queries stop early once the decision is certain and
    are skipped when the target cannot be reached.

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

## GitHub Actions
- **Tests** (`.github/workflows/tests.yml`): runs on every pull request and on
  `main`. Read-only, no secrets, no network calls to market APIs.
- **Solana Memecoin Desk** (`.github/workflows/scan.yml`): started manually.
  Runs the tests first, then one scan. The event log is written to the
  separate branch **`desk-data`** (`events.jsonl`), never to `main`.
  On the first run the branch is created and the old log from
  `data/events.jsonl` is copied over. The old file on `main` stays
  unchanged as an archive.
- After each scan, `python -m desk.latest` writes a compact summary
  **`latest.json`** next to `events.jsonl` on `desk-data` (same commit).
  It is derived only from the last log line and copies statuses, reasons
  and counts 1:1 (no re-evaluation). `scan_status` is `ok`, `failed`,
  `log_unreadable` or `inconsistent`; in every case except `ok` the lists
  of ready tokens and pools are empty and no older results are reused.
  Pools that were not checked appear only as a count (`pools_not_listed`).
  If the summary could not be generated, an old `latest.json` is removed.
- The scan code runs without GitHub write credentials; the token is only
  used in the final step that pushes the log.
- **App veröffentlichen** (`.github/workflows/pages.yml`): publishes the
  folder `app/` to GitHub Pages after a merge to `main` (or manually).
  Only Pages permissions, no write access to the repository.

## Web app (read-only, Android)
- Address: `https://luckyone2k.github.io/solana-memecoin-desk/`
  (install on Android via the browser menu „Zum Startbildschirm hinzufügen“).
- Plain HTML/CSS/JavaScript in `app/`, no build step, no dependencies.
- Only data source: `latest.json` on `desk-data`
  (`raw.githubusercontent.com`, may lag up to 5 minutes behind a scan).
  No writes, no login, no wallet. Loads on open, on return to the app and
  via the refresh button.
- Shows scan status, scan time and age (yellow warning after 24 hours),
  counts, ready tokens and a detail view per pool with reasons.
- Any status other than `ok`, an HTTP error or a file that fails the
  plausibility checks → clear message, no tokens shown.
- Without a connection the last successfully loaded state may be shown,
  always marked „Offline – Daten möglicherweise veraltet“. The service
  worker stores only the app itself, never scan data.
- One-time setup (repository owner): Settings → Pages → Source:
  „GitHub Actions“.

## Safety
- Never place API keys, seed phrases, private keys, or wallet secrets in repository files.
- Keep the repository public only if you are comfortable exposing all source code and logs.
- Live trading is intentionally not implemented.
