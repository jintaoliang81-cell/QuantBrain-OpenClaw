import yfinance as yf
import pandas as pd
import numpy as np
import os
import json
from datetime import datetime

# Configuration
TICKERS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'PEP', 'COST',
    'ADBE', 'CSCO', 'NFLX', 'AMD', 'INTC', 'CMCSA', 'TMUS', 'AMGN', 'TXN', 'HON',
    'QCOM', 'INTU', 'SBUX', 'AMAT', 'ISRG', 'MDLZ', 'GILD', 'BKNG', 'ADI', 'VRTX'
]
WINDOW = 20
Z_BUY_THRESHOLD = -2.5
Z_BUY_AGGRESSIVE_THRESHOLD = -3.0
Z_PARTIAL_SELL_THRESHOLD = 1.5
STOP_LOSS_PCT = 0.015
TRAILING_STOP_TRIGGER_PCT = 0.010
TRAILING_STOP_RETRACEMENT_PCT = 0.005
BASE_TRADE_AMOUNT = 5000.0
AGGRESSIVE_TRADE_AMOUNT = 10000.0
INITIAL_CASH = 100000.0

STATE_FILE = '/home/ubuntu/paper_trading_state.json'
LOG_FILE = '/home/ubuntu/paper_trading_log.csv'

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {'cash': INITIAL_CASH, 'positions': {}}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def log_trade(ticker, action, price, shares, pnl=0):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp},{ticker},{action},{price},{shares},{pnl}\n"
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w') as f:
            f.write("Timestamp,Ticker,Action,Price,Shares,PnL\n")
    with open(LOG_FILE, 'a') as f:
        f.write(log_entry)

def run_trading_cycle():
    state = load_state()
    cash = state['cash']
    positions = state['positions']
    
    print(f"--- Aggressive Trading Cycle: {datetime.now().strftime('%H:%M:%S')} ---")
    
    # 1. Manage Existing Positions
    for ticker in list(positions.keys()):
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='1d', interval='5m')
            if data.empty: continue
            
            current_price = float(data['Close'].iloc[-1])
            entry_price = positions[ticker]['entry_price']
            shares = positions[ticker]['shares']
            
            # Update High Price for Trailing Stop
            if 'high_price' not in positions[ticker]:
                positions[ticker]['high_price'] = current_price
            else:
                positions[ticker]['high_price'] = max(positions[ticker]['high_price'], current_price)
            
            high_price = positions[ticker]['high_price']
            pnl_pct = (current_price - entry_price) / entry_price
            
            # Calculate Z-Score
            close = data['Close']
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            z_score = (close - ma) / std
            current_z = z_score.iloc[-1]
            
            # A. Partial Sell (Z-Score > 1.5)
            if current_z >= Z_PARTIAL_SELL_THRESHOLD and not positions[ticker].get('partial_sold', False):
                sell_shares = shares * 0.5
                pnl = (current_price - entry_price) * sell_shares
                cash += current_price * sell_shares
                positions[ticker]['shares'] -= sell_shares
                positions[ticker]['partial_sold'] = True
                log_trade(ticker, "PARTIAL_SELL_Z1.5", current_price, sell_shares, pnl)
                print(f"PARTIAL SELL {ticker}: Z-Score {current_z:.2f} at ${current_price:.2f} | PnL: ${pnl:.2f}")
                shares = positions[ticker]['shares'] # Update local shares for next checks

            # B. Trailing Stop Logic
            trailing_stop_active = pnl_pct >= TRAILING_STOP_TRIGGER_PCT
            trailing_stop_price = high_price * (1 - TRAILING_STOP_RETRACEMENT_PCT)
            
            if trailing_stop_active and current_price <= trailing_stop_price:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade(ticker, "TRAILING_STOP_EXIT", current_price, shares, pnl)
                print(f"EXIT {ticker}: Trailing Stop at ${current_price:.2f} | PnL: ${pnl:.2f}")
                del positions[ticker]
                continue

            # C. Hard Stop-Loss
            if pnl_pct <= -STOP_LOSS_PCT:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade(ticker, "HARD_STOP_LOSS", current_price, shares, pnl)
                print(f"EXIT {ticker}: Hard Stop-Loss at ${current_price:.2f} | PnL: ${pnl:.2f}")
                del positions[ticker]
                continue

        except Exception as e:
            print(f"Error managing {ticker}: {e}")

    # 2. Scan for New Opportunities
    for ticker in TICKERS:
        if ticker in positions: continue
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='1d', interval='5m')
            if data.empty or len(data) < WINDOW: continue
            
            close = data['Close']
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            z_score = (close - ma) / std
            current_z = z_score.iloc[-1]
            current_price = float(close.iloc[-1])
            
            if current_z < Z_BUY_THRESHOLD:
                amount = AGGRESSIVE_TRADE_AMOUNT if current_z < Z_BUY_AGGRESSIVE_THRESHOLD else BASE_TRADE_AMOUNT
                if cash >= amount:
                    shares = amount / current_price
                    cash -= amount
                    positions[ticker] = {
                        'entry_price': current_price,
                        'shares': shares,
                        'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'high_price': current_price,
                        'partial_sold': False
                    }
                    log_trade(ticker, "BUY_AGGRESSIVE" if current_z < Z_BUY_AGGRESSIVE_THRESHOLD else "BUY", current_price, shares)
                    print(f"BUY {ticker}: Z-Score {current_z:.2f} at ${current_price:.2f} | Amount: ${amount}")
        except Exception as e:
            pass

    # 3. Save State and Display Status
    state['cash'] = cash
    state['positions'] = positions
    save_state(state)
    
    total_equity = cash
    print("\n--- Active Trades (Aggressive Mode) ---")
    if not positions:
        print("None")
    for ticker, info in positions.items():
        try:
            t = yf.Ticker(ticker)
            curr_price = float(t.history(period='1d', interval='1m')['Close'].iloc[-1])
            pnl_pct = (curr_price - info['entry_price']) / info['entry_price'] * 100
            total_equity += curr_price * info['shares']
            
            # Calculate current trailing stop level
            high_p = max(info.get('high_price', curr_price), curr_price)
            ts_level = high_p * (1 - TRAILING_STOP_RETRACEMENT_PCT)
            ts_status = f"Active (Level: ${ts_level:.2f})" if pnl_pct >= (TRAILING_STOP_TRIGGER_PCT * 100) else "Pending"
            
            print(f"{ticker} | Entry: ${info['entry_price']:.2f} | Curr: ${curr_price:.2f} | P/L: {pnl_pct:.2f}% | TS: {ts_status}")
        except: pass
    
    print(f"\nAccount Balance: ${total_equity:.2f} (Cash: ${cash:.2f})")

if __name__ == "__main__":
    run_trading_cycle()
