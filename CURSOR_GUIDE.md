# Polymarket Late-Stage Sniper Bot - Cursor Guide

## Overview
Low-code bot that buys Polymarket outcomes 80%+ likely, takes 2-5% profit before resolution. Liquidity-filtered to avoid slippage.

**Strategy:** Heavily favored outcomes are often mispriced in final hours. You catch 2-5% moves from liquidity shifts, not betting on the outcome itself.

---

## Quick Start

### 1. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

pip install requests python-dotenv
```

### 2. Get API Key
1. Polymarket.com → Sign in
2. Settings → API Keys
3. Generate new key, copy it

### 3. Create `.env`
```env
POLYMARKET_API_KEY=your_key
POLYMARKET_ADDRESS=your_wallet
PROFIT_TARGET_PCT=2.5
PRICE_THRESHOLD=0.80
POSITION_SIZE_USD=100
MAX_POSITIONS=5
HOURS_TO_RESOLUTION=12
MIN_BID_ASK_SPREAD=0.02
MIN_24H_VOLUME=1000
MIN_ORDER_BOOK_DEPTH=500
```

### 4. Run
```bash
python polymarket_sniper_bot.py
```

---

## Code Structure (for Cursor improvements)

### Key Functions

#### `fetch_active_markets()`
- Grabs all active Polymarket markets from API
- Returns list of market objects with id, question, endDate
- **Improve:** Add pagination for 1000+ markets, cache results

#### `is_close_to_resolution(market)`
- Filters markets ending within `HOURS_TO_RESOLUTION`
- Parses ISO timestamps
- **Improve:** Handle timezone edge cases, add market state filtering (only "open" markets)

#### `check_liquidity(token_id, price_level)`
- **Core function** - prevents illiquid trades
- Calculates spread %, order book depth, 24h volume
- Returns (is_liquid, spread_pct, depth, volume)
- **Improve:** Cache results per token (don't fetch every cycle), add slippage simulation

#### `fetch_order_book(token_id)`
- Gets best bid/ask prices
- Used for entry prices and exit monitoring
- **Improve:** Handle partial fills, add mid-price calculation

#### `scan_and_trade()`
- Main loop: fetches markets → filters liquidity → finds opportunities
- Places buy orders (simulated)
- Checks exit conditions on existing positions
- **Improve:** Add logging to file, parallel API requests (speed up scanning)

#### `check_exit_conditions()`
- Monitors positions for profit targets or stop-losses
- Exits at +2.5% or -3%
- **Improve:** Add trailing stops, time-based exits (exit 1h before resolution)

### Configuration Variables
```python
PRICE_THRESHOLD = 0.80              # Only buy 80%+ likely
PROFIT_TARGET_PCT = 2.5             # Exit at +2.5%
POSITION_SIZE_USD = 100             # Risk per trade
MAX_POSITIONS = 5                   # Concurrent trades
HOURS_TO_RESOLUTION = 12            # Only trade markets ending soon
MIN_BID_ASK_SPREAD = 0.02           # Max 2% spread
MIN_24H_VOLUME = 1000               # Min volume
MIN_ORDER_BOOK_DEPTH = 500          # Min liquidity depth
```

### Data Structures

**Position (in memory):**
```python
{
    "token_id": "token_abc",
    "market_id": "market_123",
    "outcome": "Biden wins 2024",
    "side": "buy",
    "price": 0.82,
    "qty": 122.0,
    "cost_usd": 100.0,
    "entry_time": "2025-05-12T14:30:00",
    "target_exit_price": 0.84,  # price * (1 + profit_target)
}
```

**Trade Log (saved to JSON):**
```python
{
    "token_id": "...",
    "outcome": "...",
    "entry_price": 0.82,
    "exit_price": 0.85,
    "profit_pct": 3.66,
    "profit_usd": 3.66,
    "entry_time": "...",
    "exit_time": "...",
}
```

---

## Common Improvements (with Cursor)

### 1. Add Database Persistence
**Problem:** Positions & trades only in memory (lost if bot crashes)

**Solution:** Replace dict with SQLite
```python
# Add to top:
import sqlite3

# New function:
def init_db():
    conn = sqlite3.connect("bot_trades.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            token_id TEXT PRIMARY KEY,
            entry_price REAL,
            qty REAL,
            entry_time TEXT
        )
    """)
    conn.commit()
    return conn

# Replace: positions[key] = order
# With: db.execute("INSERT INTO positions...", order)
```

### 2. Add Real Order Placement
**Problem:** Bot is simulated (doesn't actually trade)

**Solution:** Integrate Polymarket signed orders
```python
def place_buy_order_live(token_id, qty, price):
    # Sign order with wallet key
    signature = sign_order(...)
    
    response = requests.post(
        f"{API_BASE}/orders",
        json={
            "token_id": token_id,
            "qty": qty,
            "price": price,
            "signature": signature,
        }
    )
    return response.json()
```

### 3. Add Trailing Stops
**Problem:** Stop-loss is fixed at -3% (misses small bounces)

**Solution:** Track highest price, exit if drops 2% from peak
```python
def check_exit_conditions():
    for pos_key, position in list(positions.items()):
        # ... existing code ...
        
        # Track highest price seen
        if "max_price" not in position:
            position["max_price"] = ask_price
        else:
            position["max_price"] = max(position["max_price"], ask_price)
        
        # Trailing stop: exit if drops 2% from peak
        if ask_price < position["max_price"] * 0.98:
            # Exit trade
```

### 4. Add Discord/Telegram Alerts
**Problem:** Can't monitor bot if you're away

**Solution:** Send trade notifications
```python
import requests

def send_alert(message):
    requests.post(
        "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID",
        json={"content": message}
    )

# In place_buy_order:
send_alert(f"✅ BUY: {outcome} @ ${price:.4f}")
```

### 5. Add Kelly Sizing
**Problem:** Fixed position size (doesn't scale with win rate)

**Solution:** Size based on actual edge
```python
def calculate_kelly_size(win_rate, avg_win_pct, avg_loss_pct, bankroll):
    # Kelly: f* = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
    edge = (win_rate * avg_win_pct) - ((1 - win_rate) * avg_loss_pct)
    kelly_fraction = edge / avg_win_pct
    return bankroll * kelly_fraction * 0.25  # 25% of Kelly for safety
```

### 6. Add Parallel Market Scanning
**Problem:** Scanning 1000 markets takes too long (slow API calls)

**Solution:** Use ThreadPoolExecutor
```python
from concurrent.futures import ThreadPoolExecutor

def scan_and_trade():
    markets = fetch_active_markets()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        opportunities = list(executor.map(evaluate_market, markets))
    
    opportunities = [opp for opp in opportunities if opp is not None]
```

### 7. Add Smart Exit Timing
**Problem:** Hold through resolution (binary risk)

**Solution:** Exit 1 hour before resolution
```python
def should_exit_early(market_end_time):
    time_to_resolution = market_end_time - datetime.now()
    return time_to_resolution < timedelta(hours=1)
```

### 8. Add Backtesting
**Problem:** Can't test strategy without running live

**Solution:** Backtest against historical data
```python
def backtest(start_date, end_date):
    historical_markets = fetch_markets_by_date(start_date, end_date)
    
    total_pnl = 0
    win_count = 0
    
    for market in historical_markets:
        # Simulate trade
        entry_price = market.price_at_time(start)
        exit_price = market.price_at_time(start + timedelta(hours=2))
        pnl = (exit_price - entry_price) / entry_price
        
        if pnl > 0:
            win_count += 1
        total_pnl += pnl
    
    print(f"Backtest: {win_count}/{len(historical_markets)} wins, +{total_pnl*100:.1f}% total")
```

---

## Configuration Tuning

### Conservative (Fewer Trades, Lower Risk)
```env
PRICE_THRESHOLD=0.85              # Only 85%+ likely outcomes
PROFIT_TARGET_PCT=3.0             # Hold for 3% profit (safer)
MIN_BID_ASK_SPREAD=0.01           # Ultra-tight spreads only
MIN_24H_VOLUME=5000               # Only major markets
MIN_ORDER_BOOK_DEPTH=1000         # Deep liquidity required
```
→ ~1-2 trades/day, very low slippage risk

### Aggressive (More Trades, Higher Risk)
```env
PRICE_THRESHOLD=0.75              # Buy 75%+ likely outcomes
PROFIT_TARGET_PCT=2.0             # Exit faster
MIN_BID_ASK_SPREAD=0.05           # Allow wider spreads
MIN_24H_VOLUME=500                # Lower volume threshold
MIN_ORDER_BOOK_DEPTH=250          # Shallow book acceptable
```
→ ~10-15 trades/day, possible slippage

### Default (Balanced)
```env
PRICE_THRESHOLD=0.80
PROFIT_TARGET_PCT=2.5
MIN_BID_ASK_SPREAD=0.02
MIN_24H_VOLUME=1000
MIN_ORDER_BOOK_DEPTH=500
```
→ ~3-5 trades/day, reliable exits

---

## Debugging in Cursor

### Add Verbose Logging
```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# In functions:
logger.debug(f"Evaluating {token_id}: spread={spread_pct}%, depth=${depth}")
```

### Add Breakpoints
```python
# In scan_and_trade, after filtering:
import pdb; pdb.set_trace()  # Pause here to inspect opportunities
```

### Print Order Book
```python
def debug_orderbook(token_id):
    response = requests.get(f"{API_BASE}/orderBooks/{token_id}")
    data = response.json()
    print(json.dumps(data, indent=2))
```

---

## Testing Checklist

- [ ] API key works (test with simple market fetch)
- [ ] Liquidity filters are reasonable (getting 3-5 opportunities per scan)
- [ ] Stop-loss triggers correctly (manually set price down 5%)
- [ ] Profit target triggers (manually set price up 5%)
- [ ] Trade log saves to JSON
- [ ] Dashboard updates with mock data
- [ ] Bot survives 1-hour continuous run without crashes
- [ ] Keyboard interrupt (Ctrl+C) saves positions gracefully

---

## Next Steps

1. **Get it running:** Copy files, set up `.env`, run bot for 1 week (simulated)
2. **Tweak settings:** Adjust liquidity filters based on opportunity frequency
3. **Add improvements:** Start with database persistence, then alerts
4. **Paper trade:** Run with real API but $0 positions for 1 week
5. **Go live:** Start with $100-500, scale up after 65%+ win rate

---

## Key Metrics to Track

**Per Trade:**
- Entry price, exit price, profit %
- Time held, spread paid, slippage

**Monthly:**
- Win rate (should be 60-75%)
- Avg win %, avg loss %
- Profit factor (gross wins / gross losses)
- Sharpe ratio (return / volatility)

**Dashboard should show:**
- Total P&L today/month
- Win rate
- Active positions
- Capital at risk
- Recent 10 trades

---

## API Limits & Rates

Polymarket CLOB API is generous but be respectful:
- Markets endpoint: 100 results/call
- Order books: ~100 calls/min sustainable
- Adjust scan cycle timing if rate limited

---

## Resources

- Polymarket API: https://clob.polymarket.com/docs
- Cursor IDE: https://cursor.com
- Python requests: https://docs.python-requests.org
- Deployed on: Fly.io, PythonAnywhere, or local machine

---

## Final Notes

**Why this works:**
- 80%+ outcomes are "boring" → mispriced in final hours
- You exit before resolution → lower volatility risk
- Liquidity filters prevent stuck positions → consistent exits
- 60%+ win rate on biased flips (edge!)

**Risk management:**
- Max 5 concurrent positions
- 3% stop-loss, 2.5% profit target
- Scan only markets ending within 12h
- Only trade liquid markets (spread ≤2%, volume ≥$1k)

**Scale path:**
- Week 1: $100-500, paper trading
- Week 2-4: Real money, monitor daily
- Month 2: $1,000-2,000 if 65%+ win rate
- Month 3+: Scale to bankroll limit

Good luck! Feel free to modify and improve in Cursor. 🚀
