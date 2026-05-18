# Sniper Bot — Root Cause Analysis & Fixes

## Problem 1: Score floor of 4 silently kills most candidates

**What's happening:**
Any candidate that survives the liquidity check (spread ≤ 1.5%, vol ≥ $5k, depth ≥ $1k)
automatically gets 2 momentum points — because momentum fires when `ask ≥ 0.78 AND spread ≤ 1.5%`,
and both are already guaranteed by the liquidity check + `should_buy()`.

So scoring in practice looks like this:

| Market type | Score | Passes MIN_MARKET_SCORE=4? |
|---|---|---|
| vol $5k–$10k, no keyword match | 2 | ❌ NO |
| vol ≥ $10k, no keyword match | 4 | ✓ yes |
| vol $5k–$10k, keyword match | 5 | ✓ yes |
| vol ≥ $10k, keyword match | 7 | ✓ yes |

**Markets with $5k–$10k daily volume and no keyword in the question are always blocked.**
This is a massive swath of real Polymarket activity (sports, crypto price levels, misc events).

**Fix — lower MIN_MARKET_SCORE to 2:**
```
MIN_MARKET_SCORE=2
```
Score 2 = momentum-only, which fires for every liquid candidate that passes the existing
filters. This effectively makes scoring a tiebreaker for ranking, not a gate.
Volume spike and catalyst still boost rank but don't gate entry.

---

## Problem 2: 4.5% profit target is mathematically impossible for high-priced entries

**The math:**
```
target_exit = entry_ask * (1 + 0.045) = entry_ask * 1.045

If entry_ask = 0.95 → target = 0.9928  (barely works)
If entry_ask = 0.96 → target = 1.0032  (IMPOSSIBLE — above $1.00)
If entry_ask = 0.97 → target = 1.0137  (IMPOSSIBLE)
```

**PRICE_THRESHOLD=0.78 allows entries up to 0.99. But PROFIT_TARGET_PCT=4.5% means
entries above 0.957 can NEVER hit the profit target.** The bot buys these, holds them,
and they either resolve at $1.00 (lucky) or get stopped out at -3.5%.

**REQUIRE_PROFIT_ROOM=false** (the current default) means there is zero guard against this.

**Fix — enable profit room check and set the ceiling:**
```
REQUIRE_PROFIT_ROOM=true
PROFIT_ROOM_PRICE_BUFFER=0.03
MAX_OUTCOME_PRICE=1.0
```

With these values:
```
exit_cap = 1.0 - 0.03 = 0.97
max_entry_ask = 0.97 / 1.045 = 0.9282
```
The bot will only enter if `entry_ask ≤ 0.928`, guaranteeing the 4.5% target is reachable.

---

## Problem 3: 168-hour window defeats the "sniper" strategy

**The bot's edge is last-minute price compression** — high-probability outcomes repricing
toward $1.00 in the final hours before resolution. Entering 5–7 days out means:

- Much more time for the market to move against you
- Stop losses fire on normal market noise, not directional moves
- You're not sniping compression — you're holding a position through uncertainty

**Fix — tighten the window:**
```
MAX_HOURS_TO_RESOLUTION=24
MIN_HOURS_TO_RESOLUTION=0.25
```
This targets markets resolving within 24 hours (best sweet spot for late-stage compression)
and excludes markets already in settlement (<15 min).

---

## Problem 4: Volume spike multiplier doubles the effective volume floor

```
vol_line = MIN_24H_VOLUME * UPSIDE_VOLUME_SPIKE_MULTIPLIER = 5000 * 2.0 = $10,000
```

You need $10k daily volume just to get the volume spike points (2 pts). Combined with
`MIN_MARKET_SCORE=4` and no keywords, this means the real effective volume floor for
score-4 markets is $10,000 — not the $5,000 that `MIN_24H_VOLUME` implies.

**Fix — lower the multiplier:**
```
UPSIDE_VOLUME_SPIKE_MULTIPLIER=1.5
```
Volume spike now fires at $7,500, bringing more markets into the scoring range.

---

## Problem 5: Spread filter (1.5%) is too tight outside final hours

For markets 12–24h from resolution, spreads of 2–3% are normal on Polymarket.
A 1.5% spread requirement essentially filters to only the most liquid final-hour markets.
Combined with the 168h window from Problem 3, you're scanning 7-day-out markets with
a filter calibrated for final-hour markets — almost nothing passes.

**Fix — loosen spread to match the window:**
```
MIN_BID_ASK_SPREAD=0.025
```
2.5% spread allowed. Still rejects illiquid junk (5–10% spreads) but admits the
normal-volume markets that are 6–24h out.

---

## Combined Fix — Updated .env.max-profit

Replace the current settings with:

```ini
# Timing — true sniper window
MIN_HOURS_TO_RESOLUTION=0.25
MAX_HOURS_TO_RESOLUTION=24

# Entry price
PRICE_THRESHOLD=0.78

# Profit and risk
PROFIT_TARGET_PCT=3.0
STOP_LOSS_PCT=2.5
STOP_LOSS_GRACE_SECONDS=90

# Enforce that TP target is physically reachable
REQUIRE_PROFIT_ROOM=true
PROFIT_ROOM_PRICE_BUFFER=0.03
MAX_OUTCOME_PRICE=1.0

# Liquidity — calibrated for 24h window (not 7-day)
MIN_BID_ASK_SPREAD=0.025
MIN_24H_VOLUME=3000
MIN_ORDER_BOOK_DEPTH=750

# Scoring — use as ranker, not hard gate
MIN_MARKET_SCORE=2
REQUIRE_MARKET_MOMENTUM=false
UPSIDE_VOLUME_SPIKE_MULTIPLIER=1.5

# Keywords — broad enough to catch most events
UPSIDE_CATALYST_KEYWORDS=fed,fomc,election,vote,court,ruling,earnings,cpi,nfp,payroll,gdp,inflation,rate,btc,eth,crypto,nba,nfl,mlb,nhl,world cup,approval,announcement,decision,impeach,tariff,treaty,summit,primary,senate,house,ipo,merger,acquisition
```

**What this fixes:**
| Problem | Before | After |
|---|---|---|
| Score kills liquid markets | Score 4 required, ~50% of volume range blocked | Score 2 required, any liquid market qualifies |
| Impossible TP targets | Entries at 0.97 with 4.5% target (impossible) | Max entry capped at 0.928 for 3% TP |
| 7-day window vs sniper filters | 168h window, 1.5% spread = almost nothing | 24h window, 2.5% spread = real candidates |
| Volume spike too high | Requires $10k for volume pts | Requires $7.5k ($3k × 1.5×) |

---

## Quick sanity check: what does a valid candidate look like now?

```
Market:  "Will BTC close above $105k today?"
Hours:   8h to resolution
Ask:     0.83
Bid:     0.81
Spread:  2.4%   ✓ ≤ 2.5%
Volume:  $6,200 ✓ ≥ $3,000
Depth:   $900   ✓ ≥ $750
Score:   ask≥0.78 + spread≤1.5%? No (2.4%)→ 0 momentum pts
         vol ≥ $4,500? Yes → +2 pts
         "btc" in question → +3 pts
         Total: 5 pts ✓ ≥ 2

Entry:   $0.83
TP:      $0.83 × 1.03 = $0.855 ≤ exit_cap(0.97) ✓
SL:      $0.81 × (1 - 0.025) = $0.790
```

---

## One more: stop loss is too wide relative to the profit target

With PROFIT_TARGET_PCT=4.5% and STOP_LOSS_PCT=3.5%:
```
Risk:Reward at entry 0.85:
  target = 0.888, SL floor = 0.820
  Reward: +$0.038 per share
  Risk:   -$0.030 per share
  R:R = 1.27
```

With the fix (3.0% TP, 2.5% SL):
```
Risk:Reward at entry 0.85:
  target = 0.876, SL floor = 0.831
  Reward: +$0.026 per share
  Risk:   -$0.019 per share
  R:R = 1.37
```
Better ratio. Tighter stops also mean faster exits when the market is wrong.
