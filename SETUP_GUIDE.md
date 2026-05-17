# Polymarket Late-Stage Sniper Bot - Setup Guide

## Overview
This bot buys Polymarket outcomes that are **80%+ favored** (very likely to happen), then takes a quick **2-5% profit before market resolution**.

The edge: Most traders ignore near-certain outcomes because they're "boring." But in the last 24-48 hours, prices often shift slightly as new information emerges. You catch these small moves, cash out, and move on.

---

## Quick Start (5 minutes)

### 1. Install Python & Dependencies
```bash
# macOS/Linux
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate

# Install packages (includes py-clob-client for live trading)
pip install -r requirements.txt
```

### 2. Credentials (same as **polymarket_copy_bot_v2**)

Live orders use the **Polymarket CLOB** (`py-clob-client`), not a dashboard “API key” alone. Use the **same `.env` / `env.txt`** as your copy bot so you never maintain two signing setups:

- Default: the sniper loads `../polymarket_copy_bot_v2/.env` (or `env.txt` if `.env` is missing), then this folder’s `.env` for overrides (`DRY_RUN`, thresholds, etc.).
- Or set **`COPY_BOT_ENV_PATH`** / **`POLYMARKET_ENV_FILE`** to the full path of that file.
- Or set **`COPY_BOT_DIR`** if your copy bot lives somewhere other than the sibling folder.

Required variables match the copy bot (see `polymarket_copy_bot_v2/env.minimal.txt`): **`PRIVATE_KEY`** (or `PROXY_PRIVATE_KEY`), **`FUNDER_ADDRESS`** (or `PROXY_FUNDER_ADDRESS`), **`SIGNATURE_TYPE`** / `PROXY_SIGNATURE_TYPE`, and for gasless trading **`POLY_BUILDER_API_KEY`**, **`POLY_BUILDER_SECRET`**, **`POLY_BUILDER_PASSPHRASE`**. Optional: **`POLYMARKET_PROXY`**, **`ALCHEMY_POLYGON_RPC_URL`**.

**Before live:** from the copy bot repo run `python3 check_live_wallet.py` and optionally `python3 polymarket_wallet_live.py --no-trade` with the same env.

**Ops:** Do not run **copy_bot and sniper live** on the same wallet unless you intend combined exposure.

### 3. Create / merge `.env` files

**Copy bot file** (primary secrets): keep using your existing `polymarket_copy_bot_v2/.env`.

**Sniper overrides** (optional `polymarket_sniper_bot/.env` — loaded last, overrides non-secrets only if duplicated):

```env
# Default: observe only (no chain orders)
DRY_RUN=true

# Strategy (same meanings as before)
PROFIT_TARGET_PCT=2.5
PRICE_THRESHOLD=0.80
POSITION_SIZE_USD=100
MAX_POSITIONS=5
HOURS_TO_RESOLUTION=12
# MIN_BID_ASK_SPREAD: fraction of mid, e.g. 0.02 = max 2% bid/ask spread
MIN_BID_ASK_SPREAD=0.02
MIN_24H_VOLUME=1000
MIN_ORDER_BOOK_DEPTH=500
SLIPPAGE_TOLERANCE=0.04
SNIPER_SLEEP_SECONDS=60
```

**Legacy note:** `POLYMARKET_API_KEY` from the Polymarket UI is **not** used for signed CLOB trading; ignore old snippets that list it as the main credential.

### 3a. Example sniper-only `.env` (after copy_bot file is loaded)

```env
DRY_RUN=false
POSITION_SIZE_USD=5
```

Go live only after `DRY_RUN=true` observation and micro sizes per **TESTING_GUIDE.md**.

**What each setting does:**

| Setting | Default | What It Does |
|---------|---------|-------------|
| `DRY_RUN` | true | If true, log opportunities but **do not** place orders |
| `PROFIT_TARGET_PCT` | 2.5 | Exit when profit hits this % |
| `PRICE_THRESHOLD` | 0.80 | Only buy outcomes >80% likely |
| `POSITION_SIZE_USD` | 100 | Risk $100 per trade |
| `MAX_POSITIONS` | 5 | Maximum concurrent positions |
| `HOURS_TO_RESOLUTION` | 12 | Only trade markets ending within 12h |
| **`MIN_BID_ASK_SPREAD`** | **0.02** | **Max 2% spread (0.02 = 2%)** |
| **`MIN_24H_VOLUME`** | **1000** | **Min $1,000 volume in 24h** |
| **`MIN_ORDER_BOOK_DEPTH`** | **500** | **Min $500 liquidity at best 5 prices** |

### 4. Run the Bot
```bash
python polymarket_sniper_bot.py
```

Expected output:
```
╔════════════════════════════════════════════╗
║   POLYMARKET LATE-STAGE SNIPER BOT         ║
║   Buy >80% favored outcomes, profit 2-5%   ║
╚════════════════════════════════════════════╝

Config:
  Price Threshold: >80% favored
  Profit Target: +2.5%
  Position Size: $100 per trade
  Max Positions: 5
  Hours to Resolution: <12h

Starting bot loop... (Ctrl+C to stop)

============================================================
SCAN CYCLE @ 2025-05-12 14:30:15
Active Positions: 0/5
============================================================
📊 Found 245 markets. Filtering...

🎯 Found 3 snipe opportunities (>80% favored)
...
```

### 5. Monitor with Dashboard
Open `bot_dashboard.html` in your browser to watch positions and trades in real-time.

---

## Liquidity Filtering (Critical for Clean Exits)

**The Problem:** You buy a 2% profit target on a position, but the market is illiquid. When you try to exit, the bid/ask spread is 5%, eating your profit. You're stuck in a losing position.

**The Solution:** This bot **requires** liquid markets before entering ANY trade.

### Three Liquidity Checks:

1. **Bid/Ask Spread** (`MIN_BID_ASK_SPREAD = 0.02`)
   - Only trade if spread ≤ 2%
   - Why: Tight spread = easy entry/exit without slippage
   - Example: If buying at $0.82 (ask) and selling at $0.84, a 5% spread would cost $0.041, wiping out your 2.5% profit target

2. **24-Hour Volume** (`MIN_24H_VOLUME = 1000`)
   - Only trade if market has ≥$1,000 traded in last 24 hours
   - Why: High volume = active market, not stale/abandoned
   - Example: A market with $100 volume might not have buyers when you want to exit

3. **Order Book Depth** (`MIN_ORDER_BOOK_DEPTH = 500`)
   - Only trade if top 5 bids + asks have ≥$500 total liquidity
   - Why: Deep order book = can sell your full position without moving price
   - Example: Shallow book means your exit order might only fill 50%, forcing you to sit on half a position

### Adjusting Liquidity Filters:

**Conservative (Fewer Trades, Lower Risk):**
```env
MIN_BID_ASK_SPREAD=0.01      # Ultra-tight spread
MIN_24H_VOLUME=5000          # Only high-volume markets
MIN_ORDER_BOOK_DEPTH=1000    # Deep liquidity required
```
→ ~1-2 trades per day, very safe exits

**Aggressive (More Trades, Higher Risk):**
```env
MIN_BID_ASK_SPREAD=0.05      # Allow wider spreads
MIN_24H_VOLUME=500           # Lower volume threshold
MIN_ORDER_BOOK_DEPTH=250     # Shallow book acceptable
```
→ ~5-10 trades per day, possible slippage issues

**Default (Balanced):**
```env
MIN_BID_ASK_SPREAD=0.02      # 2% max spread
MIN_24H_VOLUME=1000          # $1k volume
MIN_ORDER_BOOK_DEPTH=500     # $500 liquidity
```
→ ~3-5 trades per day, reliable exits

---

## Strategy Breakdown

### Why This Works
1. **Asymmetric edge**: Markets 80%+ likely are "mispriced" because most traders ignore them. But in the final hours, small shifts happen:
   - New information emerges
   - Liquidity providers quote tighter spreads
   - Late traders panic-exit the "wrong" side
   
2. **Quick exits**: You're not holding through resolution—exit at 2-5% profit before volatility. Example:
   - Buy Trump 80% YES at $0.82
   - Exit at $0.84 (+2.4%)
   - Market resolves YES at $0.99 (you missed 18%, but that's okay—you had 2% in 3 hours)

3. **Tight stops**: If you're wrong, you exit at a 3% loss. Risk/reward = 2% win / 3% loss, so you need 60%+ win rate to be profitable. Very achievable on heavily favored outcomes.

### Market Selection
The bot only considers markets that:
- **Resolve within the next 12 hours** (reduce overnight risk, catalyst-driven)
- **Have liquid order books** (low slippage)
- **Have favorable odds** (80%+ = ask price $0.80+)

### Position Sizing
- **Per-trade risk**: $100 (configurable)
- **Quantity**: `100 / entry_price` (e.g., if you buy at $0.85, you get ~118 shares)
- **Max positions**: 5 concurrent trades
- **Max capital at risk**: $500

---

## Example Trade Flow

```
Market: "Will Bitcoin reach $100k by June 30?"
Current price on YES: $0.85 (85% probability implied)

BOT LOGIC:
1. Scan found this market closes in 8 hours ✓
2. $0.85 > $0.80 (threshold) ✓
3. Available positions: 3/5 ✓
4. Qty = 100 / 0.85 = 117.6 shares

ACTION: BUY 117.6 @ $0.85 for $100 cost

MONITORING:
- Target exit: $0.87 (2.5% profit = $2.50 gain)
- Stop loss: $0.82 (3% loss = -$3.00)

OUTCOME A (PROFIT):
- Price moves to $0.88 (news: Bitcoin hits $99.5k)
- Bot sells 117.6 @ $0.88
- Profit: +$3.53 (+3.5%) in 1.5 hours ✅

OUTCOME B (LOSS):
- Price drops to $0.81 (Fed rate hike fear)
- Bot triggers 3% stop loss
- Loss: -$3.53 on this trade
- Move to next opportunity
```

---

## Real-World Improvements

### 1. Filter Out Obvious Losses
Some 80%+ outcomes ARE overpriced (model risk). Add a filter:
```python
# Skip if market has huge volume (already priced in)
# Skip if only 2 hours to resolution (too much binary risk)
# Skip if market has extreme recent volatility (might be a meme)
```

### 2. Dynamic Position Sizing
Instead of fixed $100 per trade, use Kelly Criterion:
```python
# If your realized win rate is 70%, risk more
# If it's 50%, risk less
kelly_fraction = (win_pct * avg_win) - (loss_pct * avg_loss) / avg_win
position_size = kelly_fraction * total_bankroll
```

### 3. Exit Signals Beyond Price
```python
# Exit early if:
# - 80% of your target profit reached (bank the win early)
# - Market becomes illiquid (bid/ask spread widens >5%)
# - Implied probability jumps 5%+ (event occurred, no point holding)
```

### 4. Trailing Stop
Instead of fixed 3% stop, use trailing:
```python
# Exit if price drops 2% from the highest price since entry
# (Lets winners run, protects against minor dips)
```

---

## Deployment Options

### Option A: Local Machine (Simplest)
- Run bot on your laptop/desktop
- Monitor via dashboard in browser
- Cost: $0/month

### Option B: Cloud (Fly.io / PythonAnywhere)
- Run 24/7 without keeping computer on
- Total cost: $5-15/month

**Fly.io setup:**
```bash
# Install flyctl CLI
curl -L https://fly.io/install.sh | sh

# Create Fly app
fly apps create polymarket-sniper

# Deploy
fly deploy

# Monitor
fly logs
```

### Option C: VPS (DigitalOcean)
- Full control, better monitoring
- Cost: $6+/month

---

## Risk Management

### Bankroll Rules
1. **Never risk >5% of bankroll per trade**: If you have $1000, max $50 per position
2. **Max 5 concurrent positions**: Spread risk
3. **Stop at -10% daily loss**: If you hit -$100 loss today, stop trading
4. **Scale in**: Start with 1 position, add more after first win

### Realistic Returns
- **Win rate**: 60-75% (heavily favored outcomes are... heavily favored)
- **Avg win**: +2-3%
- **Avg loss**: -3%
- **Expected monthly**: 5-12% on deployed capital (very conservative estimate)

**Example with $1000:**
- 100 trades/month (3-4 per day)
- 70 wins @ +2.5% = +$175
- 30 losses @ -3% = -$90
- **Net: +$85 (+8.5% monthly)**

---

## Troubleshooting

### Bot is not finding trades
- Check `PRICE_THRESHOLD` (set it lower, e.g., 0.75 instead of 0.80)
- Check `HOURS_TO_RESOLUTION` (set it higher, e.g., 24 instead of 12)
- Check API key is valid

### Positions are not closing
- API may have rate limits; wait 5-10 mins
- Check order book is liquid (bid/ask not stuck)

### High slippage / bad fills
- Reduce `POSITION_SIZE_USD` (smaller orders = better prices)
- Increase `PRICE_THRESHOLD` to only buy very liquid outcomes

---

## Next Steps

1. **Paper trade first**: Run bot with 0 capital for 1 week to verify logic
2. **Start small**: $100-500 capital, not $10k
3. **Monitor daily**: Check trades, win rate, P&L
4. **Iterate**: Adjust `PROFIT_TARGET_PCT` and `PRICE_THRESHOLD` based on results
5. **Scale up**: Once you hit 65%+ win rate, increase position size

---

## Advanced: Combine with Other Bots

This sniper pairs well with:
- **Copy-trading bot**: Copy top traders for complementary edge
- **Arbitrage bot**: Exploit spreads between Polymarket and Manifold/Kalshi
- **Yield bot**: Run this AND staking simultaneously for 15%+ total returns

---

## Questions?

- Polymarket docs: https://clob.polymarket.com/docs
- This bot is educational—always test with small amounts first
- Monitor API rate limits (Polymarket has generous limits, but be respectful)

Good luck! 🎯
