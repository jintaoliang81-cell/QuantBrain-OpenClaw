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
Z_SELL_THRESHOLD = 0.0
STOP_LOSS_PCT = 0.015
TRADE_AMOUNT = 5000.0
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
    
    print(f"--- Trading Cycle: {datetime.now().strftime('%H:%M:%S')} ---")
    
    # 1. Manage Existing Positions (Sell/Stop-Loss)
    for ticker in list(positions.keys()):
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='1d', interval='5m')
            if data.empty: continue
            
            current_price = float(data['Close'].iloc[-1])
            entry_price = positions[ticker]['entry_price']
            shares = positions[ticker]['shares']
            
            # Calculate Z-Score for Exit
            close = data['Close']
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            z_score = (close - ma) / std
            current_z = z_score.iloc[-1]
            
            # Exit Logic: Z-Score returns to 0 OR Stop-Loss
            pnl_pct = (current_price - entry_price) / entry_price
            
            if current_z >= Z_SELL_THRESHOLD or pnl_pct <= -STOP_LOSS_PCT:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                action = "SELL_EXIT" if current_z >= Z_SELL_THRESHOLD else "STOP_LOSS"
                log_trade(ticker, action, current_price, shares, pnl)
                print(f"EXIT {ticker}: {action} at ${current_price:.2f} | PnL: ${pnl:.2f}")
                del positions[ticker]
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
            
            if current_z < Z_BUY_THRESHOLD and cash >= TRADE_AMOUNT:
                shares = TRADE_AMOUNT / current_price
                cash -= TRADE_AMOUNT
                positions[ticker] = {
                    'entry_price': current_price,
                    'shares': shares,
                    'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                log_trade(ticker, "BUY", current_price, shares)
                print(f"BUY {ticker}: Z-Score {current_z:.2f} at ${current_price:.2f}")
        except Exception as e:
            pass

    # 3. Save State and Display Status
    state['cash'] = cash
    state['positions'] = positions
    save_state(state)
    
    total_equity = cash
    print("\n--- Active Trades ---")
    if not positions:
        print("None")
    for ticker, info in positions.items():
        try:
            t = yf.Ticker(ticker)
            curr_price = float(t.history(period='1d', interval='1m')['Close'].iloc[-1])
            pnl_pct = (curr_price - info['entry_price']) / info['entry_price'] * 100
            total_equity += curr_price * info['shares']
            print(f"{ticker} | Entry: ${info['entry_price']:.2f} | Curr: ${curr_price:.2f} | P/L: {pnl_pct:.2f}%")
        except: pass
    
    print(f"\nAccount Balance: ${total_equity:.2f} (Cash: ${cash:.2f})")

if __name__ == "__main__":
    run_trading_cycle()
