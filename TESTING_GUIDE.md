# Polymarket Bot Testing Guide (No Testnet)

## Problem
Polymarket has no testnet. You must test on mainnet with real USDC.e.

## Solution: 3-Phase Testing Strategy

---

## Phase 1: Read-Only Testing (FREE - Week 1)

**Goal:** Validate market discovery and filtering logic without trading.

### What to Do
1. Keep `POSITION_SIZE_USD=0` or comment out order placement
2. Run bot in "dry-run" mode—just fetch and print opportunities
3. Verify:
   - Markets are being fetched correctly
   - Liquidity filters work (getting 3-5 opportunities per scan)
   - Price thresholds filter as expected
   - Spreads, volumes, depths are reasonable

### Code Changes (temporary)
```python
def place_buy_order(...):
    # TEMPORARILY: Don't actually place orders
    print(f"[DRY RUN] Would buy: {outcome} @ ${price:.4f}")
    # return None  # Don't add to positions
    # Just log what WOULD happen
```

### Run Command
```bash
# .env:
POSITION_SIZE_USD=0
TESTNET_MODE=False

python polymarket_sniper_bot.py
```

### What You're Testing
- ✅ API connectivity works
- ✅ Market fetching works
- ✅ Liquidity filtering isn't too strict (finding opportunities)
- ✅ Liquidity filtering isn't too loose (avoiding illiquid markets)
- ✅ Logic is sound (no crashes, infinite loops)

### Expected Output
```
============================================================
SCAN CYCLE @ 2025-05-12 14:30:15
Active Positions: 0/5
============================================================
📊 Found 245 markets. Filtering by liquidity & timing...

✅ Found 4 LIQUID snipe opportunities (>80% favored)
   Spread tolerance: ≤2.0%
   Min volume 24h: ≥$1000
   Min liquidity depth: ≥$500

   🎯 Market: Biden approval >50%...
      Outcome: YES
      Ask: 0.8420 (84.2% prob)
      Spread: 0.87% | Depth: $3,420 | Vol24h: $8,540
      [DRY RUN] Would buy at $0.8420

   🎯 Market: Bitcoin >$100k by June...
      [DRY RUN] Would buy at $0.8150
```

### Adjustments
If opportunities are too few:
```env
# Lower thresholds slightly
PRICE_THRESHOLD=0.75          # Instead of 0.80
MIN_BID_ASK_SPREAD=0.03       # Instead of 0.02
HOURS_TO_RESOLUTION=24        # Instead of 12
```

If opportunities are too many (or bot is slow):
```env
# Tighten filters
PRICE_THRESHOLD=0.85
MIN_24H_VOLUME=5000
MIN_ORDER_BOOK_DEPTH=1000
```

---

## Phase 2: Micro-Trading on Mainnet (SMALL REAL MONEY - Week 2)

**Goal:** Test actual order placement with minimal risk ($1-5 total).

### Setup
```env
TESTNET_MODE=False
POSITION_SIZE_USD=1        # Risk $1 per trade, not $100
MAX_POSITIONS=5            # Can still do 5 at once = $5 max exposure
PROFIT_TARGET_PCT=2.5      # Keep strategy same
```

### What to Do
1. Fund wallet with $20 USDC.e (you'll only risk $5, keep $15 buffer)
2. Enable actual order placement:
   ```python
   # In place_buy_order():
   # Uncomment the actual order placement code
   ```
3. Run bot for 24-48 hours
4. Monitor:
   - Does it find opportunities?
   - Do orders fill?
   - Do exits trigger correctly?
   - Any unexpected errors?

### What You're Testing
- ✅ API authentication works (signing orders)
- ✅ Orders actually place on mainnet
- ✅ Order fills happen as expected
- ✅ Exit logic triggers correctly
- ✅ No slippage surprises
- ✅ Trade log saves properly

### Risk Exposure
- **Max exposure:** $5 (5 positions × $1 each)
- **Expected loss if all positions fail:** -$5 × 3% = -$0.15
- **Realistic outcome:** 2-3 winning trades, 1-2 losing trades
- **Net:** +$0.30 to +$1.50 profit (if 70% win rate)

### Monitoring Checklist
- [ ] First 10 trades execute without errors
- [ ] Win rate is 60%+ (at least 6 wins out of 10)
- [ ] Exit prices are close to expected (no huge slippage)
- [ ] Trade log JSON file updates
- [ ] No wallet errors or auth failures
- [ ] Bot survives 12+ hours without crashing

### If Things Go Wrong
- **Orders not filling:** Liquidity might be too tight, increase `MIN_BID_ASK_SPREAD` to 0.03
- **Positions exit too fast:** Increase `PROFIT_TARGET_PCT` to 3.0
- **Not finding opportunities:** Decrease `HOURS_TO_RESOLUTION` to 6
- **Slippage is huge:** Check if `MIN_ORDER_BOOK_DEPTH` is too low, increase to $1000

---

## Phase 3: Scale to Real Positions (NORMAL TRADING - Week 3+)

**Goal:** Trade with real position sizing after proving profitability.

### Prerequisites (MUST HAVE)
- ✅ 30+ trades completed in Phase 2
- ✅ Win rate ≥ 65%
- ✅ No major bugs or errors
- ✅ Comfortable with bot behavior

### Setup
```env
TESTNET_MODE=False
POSITION_SIZE_USD=50        # Start with $50 per trade
MAX_POSITIONS=5             # $250 max exposure
PROFIT_TARGET_PCT=2.5       # Keep strategy same
```

### Fund Wallet
- $500-1000 USDC.e initially
- Keep 50% as buffer (only risk 50% of capital)
- Scale up only after hitting consistent 65%+ win rate

### Daily Monitoring
- Check 2x daily (morning, evening)
- Monitor P&L, win rate, active positions
- Adjust settings if needed
- Keep trade log backed up

### Scale Path
```
Week 1:  $1/trade (Phase 2) → Build confidence, find bugs
Week 2:  $10/trade → Test at slightly higher scale
Week 3:  $50/trade → Real money but still conservative
Week 4+: $100+/trade → Once you hit 65%+ win rate over 100+ trades
```

---

## Testnet Alternative: Polygon Amoy

If you want to avoid mainnet entirely, you can test on **Amoy testnet** (Polygon's sandbox):

### Setup
```env
TESTNET_MODE=True           # Use testnet endpoints
POSITION_SIZE_USD=100       # No risk, test full strategy
```

### Get Testnet Tokens
1. Go to https://faucet.polygon.technology/
2. Get Amoy testnet MATIC + USDC
3. Use them to trade on Amoy

### Caveats
- **Limited markets:** Amoy has far fewer markets than mainnet
- **No liquidity:** Markets are often illiquid or have bad spreads
- **Fake data:** Prices and volumes don't reflect real markets
- **Different endpoints:** API might behave slightly differently

### When to Use Amoy
- Testing integration with official Polymarket SDK
- Testing order placement mechanics
- Testing advanced features (cancellations, batch orders)
- NOT ideal for testing strategy profitability

---

## Recommended Path

### For Most Traders (Recommended)
```
Week 1: Phase 1 (dry-run, free)
  ↓
Week 2: Phase 2 (micro-trades, $5 risk)
  ↓
Week 3: Phase 3 (scale to $50-100/trade)
```

### For Ultra-Conservative
```
Week 1: Phase 1 (dry-run)
  ↓
Week 1-2: Amoy testnet (play money)
  ↓
Week 2: Phase 2 (micro-trades)
  ↓
Week 3: Phase 3 (scale up)
```

### For Risk-Takers
```
Week 1: Phase 2 immediately ($1-10 trades)
  ↓
Adjust settings based on results
  ↓
Week 2: Scale to $50+ once profitable
```

---

## Debugging Commands

### Test API Connection
```python
# In Python REPL:
import requests
response = requests.get("https://clob.polymarket.com/markets?limit=1")
print(response.status_code)  # Should be 200
print(response.json())
```

### Test Liquidity Filter
```python
# Check specific token
token_id = "YOUR_TOKEN_ID"
is_liquid, spread, depth, volume = check_liquidity(token_id, 0.80)
print(f"Liquid: {is_liquid}, Spread: {spread:.2f}%, Depth: ${depth}, Vol: ${volume}")
```

### Test Order Book
```python
# See actual bids/asks
bid, ask = fetch_order_book(token_id)
print(f"Bid: ${bid:.4f}, Ask: ${ask:.4f}, Spread: {((ask-bid)/((ask+bid)/2)*100):.2f}%")
```

### Test Exit Logic
```python
# Manually trigger exit check
check_exit_conditions()
# Should print exits if positions hit targets
```

---

## Common Mistakes to Avoid

❌ **Mistake:** Starting Phase 2 with $50/trade
✅ **Fix:** Start with $1, scale up after 50 trades

❌ **Mistake:** Not backing up trade_log.json
✅ **Fix:** Copy it daily or set up cloud sync

❌ **Mistake:** Changing settings every day
✅ **Fix:** Let it run for at least 1 week before adjusting

❌ **Mistake:** Trading without monitoring
✅ **Fix:** Check positions 2x daily, keep alert on

❌ **Mistake:** Not tracking win rate
✅ **Fix:** Calculate after every 20 trades: wins / total

---

## Quick Reference: Thresholds to Adjust

| Problem | Solution |
|---------|----------|
| Too few opportunities | Decrease `PRICE_THRESHOLD` to 0.75, increase `HOURS_TO_RESOLUTION` to 24 |
| Too many opportunities | Increase `PRICE_THRESHOLD` to 0.85, tighten `MIN_BID_ASK_SPREAD` to 0.01 |
| High slippage at exit | Increase `MIN_ORDER_BOOK_DEPTH`, decrease `POSITION_SIZE_USD` |
| Orders not filling | Decrease `PROFIT_TARGET_PCT`, use FOK order type |
| Positions stuck | Increase `MIN_BID_ASK_SPREAD` tolerance, only trade major markets |
| Too slow | Add parallel API calls, cache market data |

---

## Summary

No testnet? No problem.

1. **Week 1:** Dry-run (free) ← Start here
2. **Week 2:** $1-5 micro-trades (real money, minimal risk)
3. **Week 3:** $50-100 trades (scale after 65%+ win rate)

By the end of Week 3, you'll have real data on whether this strategy works. If you hit 65%+ win rate, scale up. If not, adjust and try again.

Good luck! 🚀
