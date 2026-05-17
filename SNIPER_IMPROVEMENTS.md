# Sniper Bot — Profit & Pool Improvements

Suggestions for increasing edge, hit rate, and per-trade profitability.
Each section has a **Priority** tag: 🔴 High / 🟡 Medium / 🟢 Low.

---

## 1. Pool Selection Improvements

### 1.1 Add a Resolution-Probability Weighted Score 🔴

**Problem:** The bot scores on momentum, volume, and catalyst keywords but does not weight by how likely the outcome is to resolve at $1.00 within the window.

**Fix:** Add a resolution certainty factor:
```
resolution_score = (price - PRICE_THRESHOLD) / (1.0 - PRICE_THRESHOLD)
```
Multiply this into the upside score so markets at 0.95 rank above 0.80 even when volume is comparable. High-certainty outcomes have less gap risk and faster compression.

**.env:**
```
UPSIDE_POINTS_RESOLUTION=3
UPSIDE_RESOLUTION_WEIGHT=1.5
```

---

### 1.2 Prefer Markets with Accelerating Volume 🔴

**Problem:** `MIN_24H_VOLUME` is a flat threshold. A market with $5k volume at hour 0 and $5k at hour 23 looks identical, but the second is cooling while the first may be accelerating.

**Fix:** Pull two Gamma volume snapshots (current 24h vs prior 6h extrapolated) and compute:
```
volume_acceleration = (volume_6h * 4) / volume_24h  # > 1 = accelerating
```
Boost score by 2 pts if `volume_acceleration > 1.3`.

**.env:**
```
UPSIDE_VOLUME_ACCEL_THRESHOLD=1.3
UPSIDE_POINTS_VOLUME_ACCEL=2
```

---

### 1.3 Filter Out Markets with Wide Historical Bid Swings 🔴

**Problem:** The bot only looks at the instantaneous spread. A market with a tight spread right now but historically volatile bids will trigger stop losses randomly.

**Fix:** Track `bid_history` per position (cheap: just previous scan bid). Reject candidates where:
```
|current_bid - last_bid| / last_bid > BID_STABILITY_THRESHOLD
```
**.env:**
```
BID_STABILITY_THRESHOLD=0.03   # reject if bid moved >3% since last scan
```

---

### 1.4 Add a CLOB-Only High-Volume Pool Tier 🟡

**Problem:** The Gamma pool (sorted by 24h volume) mixes binary and multi-outcome markets. Multi-outcome markets at 80% are lower certainty because they can stay unresolved while the field narrows.

**Fix:** Create a separate "blue chip" tier: CLOB markets where 24h volume > `BLUE_CHIP_VOLUME_THRESHOLD` AND the question is binary (exactly 2 outcomes). Give these a 1-point score bonus and allow a slightly tighter PRICE_THRESHOLD (e.g., 0.77 instead of 0.80) since liquidity compensates for price risk.

**.env:**
```
BLUE_CHIP_VOLUME_THRESHOLD=50000
BLUE_CHIP_PRICE_THRESHOLD=0.77
UPSIDE_POINTS_BLUE_CHIP=1
```

---

### 1.5 Blacklist Repeat Stop-Loss Markets 🟡

**Problem:** The same market can re-enter the pool after a stop loss is triggered, wasting capital on consistently bad trades.

**Fix:** When a stop-loss fires, write `condition_id` to a `sniper_blacklist.json` with a TTL (e.g., 4 hours). Skip any blacklisted market during pool assembly.

**.env:**
```
SL_BLACKLIST_TTL_HOURS=4
SL_BLACKLIST_FILE=sniper_blacklist.json
```

---

### 1.6 Time-Decay Adjusted Entry Threshold 🟡

**Problem:** At 12h to resolution an 80% outcome has meaningful room to drop. At 1h to resolution the same outcome is nearly certain. The same PRICE_THRESHOLD treats both identically.

**Fix:** Scale entry threshold dynamically:
```
hours_left = (end_date - now).total_seconds() / 3600
adjusted_threshold = PRICE_THRESHOLD + (0.05 * (1 - hours_left / MAX_HOURS_TO_RESOLUTION))
```
This progressively raises the bar for late entries, ensuring you only enter in the final hours when the market is already ≥ 85–90%.

**.env:**
```
DYNAMIC_PRICE_THRESHOLD=true
DYNAMIC_THRESHOLD_MAX_BOOST=0.10
```

---

## 2. Profit Improvements

### 2.1 Trailing Stop Instead of Fixed Stop Loss 🔴

**Problem:** Fixed stop loss at -3% from entry bid means you exit at the same absolute level whether the position has appreciated 1% or 4% since entry. You give back large gains.

**Fix:** Implement a trailing stop:
```
high_water_mark = max(high_water_mark, current_bid)
trail_floor = high_water_mark * (1 - TRAILING_STOP_PCT / 100)
if current_bid < trail_floor and time_since_entry > STOP_LOSS_GRACE_SECONDS:
    sell(reason="trailing_stop")
```
Start trailing only once the position is profitable (bid > entry_price).

**.env:**
```
TRAILING_STOP=true
TRAILING_STOP_PCT=1.5       # trail 1.5% below high water mark
TRAILING_STOP_TRIGGER=0.01  # only activate after +1% gain
```

---

### 2.2 Tiered Profit Taking 🔴

**Problem:** All-or-nothing exit at PROFIT_TARGET_PCT leaves money on the table when markets run to resolution ($1.00). A 2.5% exit on an 0.85 entry is $0.0212 gain; a full resolution would yield $0.15.

**Fix:** Sell half at PROFIT_TARGET_PCT, hold remainder with a raised stop:
```
if bid >= target_exit_price:
    sell_partial(qty=position.qty * PARTIAL_EXIT_RATIO)
    position.qty -= sold_qty
    position.stop_loss_floor = entry_price  # raise SL to breakeven on remainder
```
**.env:**
```
PARTIAL_EXIT=true
PARTIAL_EXIT_RATIO=0.5         # sell 50% at target
PARTIAL_EXIT_RESIDUAL_SL=0.0   # residual stop = entry price (break-even)
```

---

### 2.3 Dynamic Profit Target Based on Entry Price 🟡

**Problem:** PROFIT_TARGET_PCT = 2.5% is applied uniformly. But an entry at 0.80 has 20 cents of ceiling; an entry at 0.95 has only 5 cents. The same % target is proportionally much harder to hit for high-priced entries.

**Fix:** Cap the target price at `MAX_OUTCOME_PRICE - PROFIT_ROOM_PRICE_BUFFER` but also set a minimum profit in cents:
```
target_pct_exit = entry * (1 + PROFIT_TARGET_PCT/100)
target_min_exit = entry + MIN_PROFIT_CENTS
target_exit = min(MAX_OUTCOME_PRICE - PROFIT_ROOM_PRICE_BUFFER, max(target_pct_exit, target_min_exit))
```
For entry at 0.95 with 2.5% target: 0.95 × 1.025 = 0.974 — perfectly valid. But MIN_PROFIT_CENTS ensures you always capture at least N cents.

**.env:**
```
MIN_PROFIT_CENTS=0.015   # always target at least 1.5 cents gain
```

---

### 2.4 Re-Entry After Profit on Same Market 🟡

**Problem:** After taking profit at 0.872 on a market, if price retraces to 0.85 and is still 6h from resolution, this is a fresh valid opportunity. Currently the bot does not re-enter positions it has traded.

**Fix:** Remove the implicit "one trade per token" constraint and allow re-entry if:
- The market is not in the SL blacklist
- Time to resolution > MIN_HOURS_TO_RESOLUTION
- All other entry filters pass
- You have not already held this token at a loss

**.env:**
```
ALLOW_REENTRY=true
REENTRY_COOLDOWN_MINUTES=15   # minimum gap between closes and re-entries
```

---

### 2.5 Market-Resolved Fast Exit 🟡

**Problem:** When a market resolves YES, the best bid jumps to $1.00. The bot waits for the next SNIPER_SLEEP_SECONDS cycle to detect and sell. On a 60-second loop this can miss $0.10+ in realized value per position.

**Fix:** On each loop, also check `market.closed` / `market.resolvedBy` from Gamma API for any held positions. If the market is closed and resolved, immediately sell at market.

**.env:**
```
CHECK_RESOLUTION_ON_HOLD=true
RESOLUTION_EXIT_PRICE=0.99   # assume $1 - fees
```

---

### 2.6 Adjust Position Size for Entry Quality 🟢

**Problem:** All entries get the same POSITION_SIZE_USD regardless of how tight the spread is, how deep the book is, or how close to resolution. High-confidence entries should get larger stakes.

**Fix:** Apply a quality multiplier:
```
quality_multiplier = 1.0
if spread_pct < 0.5: quality_multiplier += 0.3
if volume_24h > MIN_24H_VOLUME * 5: quality_multiplier += 0.2
if hours_left < 2: quality_multiplier += 0.25
final_size = POSITION_SIZE_USD * min(quality_multiplier, MAX_POSITION_QUALITY_MULT)
```
**.env:**
```
DYNAMIC_POSITION_SIZING=true
MAX_POSITION_QUALITY_MULT=1.75
SIZE_BOOST_TIGHT_SPREAD=0.3
SIZE_BOOST_HIGH_VOLUME=0.2
SIZE_BOOST_NEAR_RESOLUTION=0.25
```

---

## 3. Better Filter Tuning (`.env` Changes to Test First)

These are conservative parameter changes to test in dry-run before going live:

| Parameter | Current Default | Suggested | Rationale |
|---|---|---|---|
| `PRICE_THRESHOLD` | 0.80 | **0.82** | Narrower uncertainty band, faster resolution |
| `MIN_24H_VOLUME` | 1,000 | **2,500** | Reduces illiquid traps |
| `MIN_ORDER_BOOK_DEPTH` | 500 | **1,000** | Ensures clean fills on $100+ positions |
| `MAX_SPREAD_PCT` | 2.0 | **1.5** | Tighter spread = lower slippage cost |
| `PROFIT_TARGET_PCT` | 2.5 | **3.5** | More room above fee drag (~0.5%) |
| `STOP_LOSS_PCT` | 3.0 | **2.0** | Cut losers faster; use trailing stop for winners |
| `MAX_HOURS_TO_RESOLUTION` | 12 | **6** | Sweet spot: most last-minute compression |
| `MIN_HOURS_TO_RESOLUTION` | 0 | **0.5** | Avoid markets already in settlement |
| `MIN_MARKET_SCORE` | 0 | **3** | Require some signal before entry |
| `STOP_LOSS_GRACE_SECONDS` | 120 | **60** | Tighter grace to cut fast-moving losers |

---

## 4. Scoring Formula Overhaul

The current scoring is purely additive and capped at 10. Consider replacing with a weighted multiplicative formula that penalizes bad signals:

```python
def compute_score(spread_pct, volume_24h, hours_left, price, catalyst):
    base = (price - 0.80) / 0.20 * 4           # 0–4 pts, higher price = better
    vol_score = min(volume_24h / 10000, 2)       # 0–2 pts, volume saturation
    spread_score = max(0, 2 - spread_pct * 4)    # 0–2 pts, tighter spread = better
    time_score = max(0, 2 - hours_left / 3)      # 0–2 pts, closer = better
    catalyst_bonus = 1 if catalyst else 0

    raw = base + vol_score + spread_score + time_score + catalyst_bonus  # max ~11
    return min(10, raw)
```

This naturally rewards entries that score well on **all** dimensions rather than compensating a bad spread with a volume spike.

---

## 5. Infrastructure & Operational Wins

### 5.1 Reduce Loop Latency 🔴
`SNIPER_SLEEP_SECONDS=60` means up to 60s to detect a profitable exit. Set to **15–20s** during active trading windows (2h before resolution). This requires monitoring CPU and rate-limit headroom.

### 5.2 Parallel Order Book Fetches 🟡
Currently order books are fetched sequentially per outcome. Use `asyncio` or `ThreadPoolExecutor` to fetch all order books in parallel — this can cut scanning time by 5–10x on large pools.

### 5.3 Cache Gamma Market Metadata 🟡
Gamma metadata (question text, end_date, outcomes) does not change. Cache it with a 5-minute TTL. Only re-fetch CLOB order books every loop. This reduces API calls by ~60%.

### 5.4 Add Fee Model to P&L 🟢
Polymarket charges a maker/taker fee. The current P&L does not account for this, so realized gains look higher than they are. Add:
```python
TAKER_FEE_PCT = 0.0  # Polymarket currently 0 but may change
MAKER_FEE_PCT = 0.0
net_pnl = gross_pnl - (entry_size * TAKER_FEE_PCT) - (exit_size * TAKER_FEE_PCT)
```

---

## 6. High-Confidence Market Categories to Target

Based on Polymarket historical data, these market types tend to resolve fast and cleanly:

| Category | Why Good |
|---|---|
| **Fed / FOMC rate decisions** | Binary, scheduled, resolves same day, high volume |
| **Sports final scores** | Resolves within hours, clear result, no ambiguity |
| **Election night calls** | Massive volume surge as results come in |
| **Economic data releases** (CPI, NFP) | Scheduled, resolves in minutes, very high volume |
| **Crypto price levels** (BTC above $X by end of day) | Real-time data, fast resolution |

Add these to `UPSIDE_CATALYST_KEYWORDS`:
```
UPSIDE_CATALYST_KEYWORDS=fed,fomc,election,earnings,court,cpi,nfp,payroll,inflation,rate,btc,eth,crypto,gdp,unemployment,sports,nba,nfl,mlb,world cup
```

---

## 7. Quick Wins — Implement First

In order of implementation effort vs expected impact:

1. **Trailing stop** (replaces fixed SL) — highest risk-adjusted gain
2. **Tighter filter defaults** (table in §3) — dry-run overnight to confirm
3. **SL blacklist** — prevents re-entering broken markets
4. **Resolution fast exit** — captures last-mile gains without waiting for loop
5. **Parallel order book fetches** — cuts loop latency with no logic change
6. **Partial exit** — captures full resolution upside on winners
