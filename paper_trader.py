import yfinance as yf
import pandas as pd
import numpy as np
import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from scipy.stats import norm
from dotenv import load_dotenv

# --- Initialization ---
load_dotenv()
client = OpenAI()

# --- Configuration ---
TICKERS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'PEP', 'COST',
    'ADBE', 'CSCO', 'NFLX', 'AMD', 'INTC', 'CMCSA', 'TMUS', 'AMGN', 'TXN', 'HON',
    'QCOM', 'INTU', 'SBUX', 'AMAT', 'ISRG', 'MDLZ', 'GILD', 'BKNG', 'ADI', 'VRTX'
]
WINDOW = 20
ATR_PERIOD = 14
ATR_MULTIPLIER = 1.5
Z_BUY_THRESHOLD = -2.5
Z_BUY_AGGRESSIVE_THRESHOLD = -3.0
Z_PARTIAL_SELL_THRESHOLD = 1.5
STOP_LOSS_PCT = 0.015
INITIAL_CASH = 100000.0

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

STATE_FILE = '/home/ubuntu/paper_trading_state.json'
LOG_FILE = '/home/ubuntu/paper_trading_log.csv'

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Core Logic ---
def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except: pass
    return {
        'cash': INITIAL_CASH, 
        'positions': {}, 
        'consecutive_losses': 0, 
        'zen_mode_until': None,
        'win_rate': 0.68, # Historical seed
        'avg_win': 0.01,
        'avg_loss': 0.015
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

async def send_telegram_notification(app, message):
    try:
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    except: pass

def log_trade_to_csv(ticker, action, price, shares, pnl=0):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp},{ticker},{action},{price},{shares},{pnl}\n"
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w') as f:
            f.write("Timestamp,Ticker,Action,Price,Shares,PnL\n")
    with open(LOG_FILE, 'a') as f:
        f.write(log_entry)

# --- Transhuman Modules ---

def calculate_kelly_fraction(win_rate, avg_win, avg_loss):
    """Kelly Criterion: f* = (p/a) - (q/b) where p=win_rate, q=loss_rate, a=loss, b=win"""
    if avg_loss == 0: return 0.1
    p = win_rate
    q = 1 - p
    b = avg_win
    a = avg_loss
    f_star = (p / a) - (q / b) if b != 0 else 0
    return max(0, min(f_star * 0.5, 0.2)) # Half-Kelly for safety, max 20% per trade

def calculate_var_99(positions, cash, confidence=0.99):
    """99% VaR calculation"""
    if not positions: return 0
    total_value = cash
    returns = []
    for ticker in positions:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period='5d', interval='5m')['Close'].pct_change().dropna()
            if not hist.empty:
                returns.append(hist)
                total_value += positions[ticker]['shares'] * float(t.history(period='1d')['Close'].iloc[-1])
        except: pass
    if not returns: return 0
    combined_std = np.mean([r.std() for r in returns])
    var = total_value * combined_std * norm.ppf(confidence)
    return var / total_value

def detect_market_regime(ticker):
    """Bayesian-like Regime Detection: Bull, Bear, Sideways, Crash"""
    try:
        t = yf.Ticker(ticker)
        data = t.history(period='10d', interval='1h')
        returns = data['Close'].pct_change().dropna()
        vol = returns.std()
        trend = (data['Close'].iloc[-1] - data['Close'].iloc[0]) / data['Close'].iloc[0]
        
        if trend < -0.05 and vol > 0.02: return "CRASH"
        if trend < -0.02: return "BEAR"
        if trend > 0.02: return "BULL"
        return "SIDEWAYS"
    except: return "SIDEWAYS"

async def run_trading_cycle(app):
    state = load_state()
    if state.get('zen_mode_until'):
        if datetime.now() < datetime.fromisoformat(state['zen_mode_until']): return
        state['zen_mode_until'] = None
        state['consecutive_losses'] = 0

    cash = state['cash']
    positions = state['positions']
    
    # 1. VaR Risk Check (99% Confidence)
    var_99 = calculate_var_99(positions, cash)
    if var_99 > 0.02: # 2% Portfolio Risk Limit
        await send_telegram_notification(app, f"🚨 *VaR 99% Limit Exceeded*: {var_99:.2%}. Liquidating riskiest asset.")
        # Liquidate the position with highest unrealized loss or highest volatility
        if positions:
            worst_ticker = list(positions.keys())[0] # Simplified
            t = yf.Ticker(worst_ticker)
            curr = float(t.history(period='1d')['Close'].iloc[-1])
            cash += curr * positions[worst_ticker]['shares']
            del positions[worst_ticker]
            save_state(state)

    # 2. Manage Positions
    for ticker in list(positions.keys()):
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='5d', interval='5m')
            if data.empty: continue
            
            current_price = float(data['Close'].iloc[-1])
            entry_price = positions[ticker]['entry_price']
            shares = positions[ticker]['shares']
            
            # ATR Trailing Stop
            high = data['High']
            low = data['Low']
            prev_close = data['Close'].shift(1)
            tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
            atr = tr.rolling(window=ATR_PERIOD).mean().iloc[-1]
            
            positions[ticker]['high_price'] = max(positions[ticker].get('high_price', current_price), current_price)
            pnl_pct = (current_price - entry_price) / entry_price
            
            # Exit Logic
            if pnl_pct > 0.005:
                if current_price <= (positions[ticker]['high_price'] - (atr * ATR_MULTIPLIER)):
                    pnl = (current_price - entry_price) * shares
                    cash += current_price * shares
                    log_trade_to_csv(ticker, "ATR_EXIT", current_price, shares, pnl)
                    await send_telegram_notification(app, f"🛑 *ATR Exit: {ticker}*\nPnL: ${pnl:.2f}")
                    del positions[ticker]
                    continue

            if pnl_pct <= -STOP_LOSS_PCT:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade_to_csv(ticker, "STOP_LOSS", current_price, shares, pnl)
                del positions[ticker]
                state['consecutive_losses'] += 1
                continue
        except: pass

    # 3. Scan & Buy (Whale Detector + Kelly)
    regime = detect_market_regime('QQQ')
    kelly_f = calculate_kelly_fraction(state['win_rate'], state['avg_win'], state['avg_loss'])
    
    for ticker in TICKERS:
        if ticker in positions or len(positions) >= 5: continue
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='5d', interval='5m')
            if data.empty: continue
            
            close = data['Close']
            volume = data['Volume']
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            vol_ma = volume.rolling(window=WINDOW).mean()
            
            # OBV (On-Balance Volume)
            obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
            
            current_z = (close.iloc[-1] - ma.iloc[-1]) / std.iloc[-1]
            current_vol = volume.iloc[-1]
            
            # Whale Detector: Z < -2.5 AND Vol > 2x AND OBV NOT dropping
            if current_z < Z_BUY_THRESHOLD and current_vol > (2 * vol_ma.iloc[-1]) and obv.iloc[-1] >= obv.iloc[-5]:
                amount = (cash + sum(p['shares']*p['entry_price'] for p in positions.values())) * kelly_f
                if cash >= amount:
                    shares = amount / float(close.iloc[-1])
                    cash -= amount
                    positions[ticker] = {'entry_price': float(close.iloc[-1]), 'shares': shares, 'high_price': float(close.iloc[-1])}
                    log_trade_to_csv(ticker, "KELLY_BUY", float(close.iloc[-1]), shares)
                    await send_telegram_notification(app, f"🐋 *Whale Buy: {ticker}*\nZ-Score: {current_z:.2f}\nKelly: {kelly_f:.1%}\nRegime: {regime}")
        except: pass

    state['cash'] = cash
    state['positions'] = positions
    save_state(state)

# --- NLP & Personality ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID: return
    user_text = update.message.text
    state = load_state()
    
    prompt = f"User: {user_text}\nState: {json.dumps(state)}\nPersona: Brutal Candor, witty, data-driven. Call him Liang."
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "system", "content": "You are Liang Quant Commander. Be direct, witty, and data-driven."}, {"role": "user", "content": prompt}]
        )
        await update.message.reply_text(response.choices[0].message.content, parse_mode='Markdown')
    except: pass

async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    while True:
        await run_trading_cycle(app)
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
