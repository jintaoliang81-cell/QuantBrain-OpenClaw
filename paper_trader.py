import yfinance as yf
import pandas as pd
import numpy as np
import os
import json
import asyncio
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Configuration ---
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

# Telegram Config
TELEGRAM_BOT_TOKEN = "8519943787:AAGDrCb26d1h4c_Gfw0sRqGKSjjlfgKn5Bg"
TELEGRAM_CHAT_ID = 8349528219  # Integer for comparison

STATE_FILE = '/home/ubuntu/paper_trading_state.json'
LOG_FILE = '/home/ubuntu/paper_trading_log.csv'

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Core Logic ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {'cash': INITIAL_CASH, 'positions': {}}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

async def send_telegram_notification(app, message):
    await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')

def log_trade_to_csv(ticker, action, price, shares, pnl=0):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp},{ticker},{action},{price},{shares},{pnl}\n"
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w') as f:
            f.write("Timestamp,Ticker,Action,Price,Shares,PnL\n")
    with open(LOG_FILE, 'a') as f:
        f.write(log_entry)

async def run_trading_cycle(app):
    state = load_state()
    cash = state['cash']
    positions = state['positions']
    
    # 1. Manage Existing Positions
    for ticker in list(positions.keys()):
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='1d', interval='5m')
            if data.empty: continue
            
            current_price = float(data['Close'].iloc[-1])
            entry_price = positions[ticker]['entry_price']
            shares = positions[ticker]['shares']
            
            if 'high_price' not in positions[ticker]:
                positions[ticker]['high_price'] = current_price
            else:
                positions[ticker]['high_price'] = max(positions[ticker]['high_price'], current_price)
            
            high_price = positions[ticker]['high_price']
            pnl_pct = (current_price - entry_price) / entry_price
            
            close = data['Close']
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            z_score = (close - ma) / std
            current_z = z_score.iloc[-1]
            
            # Partial Sell
            if current_z >= Z_PARTIAL_SELL_THRESHOLD and not positions[ticker].get('partial_sold', False):
                sell_shares = shares * 0.5
                pnl = (current_price - entry_price) * sell_shares
                cash += current_price * sell_shares
                positions[ticker]['shares'] -= sell_shares
                positions[ticker]['partial_sold'] = True
                log_trade_to_csv(ticker, "PARTIAL_SELL", current_price, sell_shares, pnl)
                await send_telegram_notification(app, f"💰 *Partial Sell: {ticker}*\nPrice: ${current_price:.2f}\nZ-Score: {current_z:.2f}\nPnL: ${pnl:.2f}")
                shares = positions[ticker]['shares']

            # Trailing Stop
            trailing_stop_active = pnl_pct >= TRAILING_STOP_TRIGGER_PCT
            trailing_stop_price = high_price * (1 - TRAILING_STOP_RETRACEMENT_PCT)
            
            if trailing_stop_active and current_price <= trailing_stop_price:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade_to_csv(ticker, "TRAILING_STOP", current_price, shares, pnl)
                await send_telegram_notification(app, f"🛑 *Trailing Stop: {ticker}*\nExit Price: ${current_price:.2f}\nPnL: ${pnl:.2f}")
                del positions[ticker]
                continue

            # Hard Stop-Loss
            if pnl_pct <= -STOP_LOSS_PCT:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade_to_csv(ticker, "STOP_LOSS", current_price, shares, pnl)
                await send_telegram_notification(app, f"⚠️ *Stop Loss: {ticker}*\nExit Price: ${current_price:.2f}\nPnL: ${pnl:.2f}")
                del positions[ticker]
                continue

        except Exception as e:
            logging.error(f"Error managing {ticker}: {e}")

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
                    log_trade_to_csv(ticker, "BUY", current_price, shares)
                    await send_telegram_notification(app, f"🚀 *Buy Alert: {ticker}*\nPrice: ${current_price:.2f}\nZ-Score: {current_z:.2f}\nAmount: ${amount}")
        except Exception as e:
            pass

    state['cash'] = cash
    state['positions'] = positions
    save_state(state)

# --- Telegram Handlers ---
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID: return
    state = load_state()
    total_equity = state['cash']
    pos_str = ""
    for ticker, info in state['positions'].items():
        try:
            t = yf.Ticker(ticker)
            curr = float(t.history(period='1d', interval='1m')['Close'].iloc[-1])
            pnl = (curr - info['entry_price']) / info['entry_price'] * 100
            total_equity += curr * info['shares']
            pos_str += f"• {ticker}: ${curr:.2f} ({pnl:+.2f}%)\n"
        except: pos_str += f"• {ticker}: Error fetching price\n"
    
    if not pos_str: pos_str = "None\n"
    msg = f"📊 *Current Portfolio*\n{pos_str}\n*Balance*: ${total_equity:,.2f}\n*Cash*: ${state['cash']:,.2f}"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID: return
    state = load_state()
    await update.message.reply_text(f"💰 *Available Cash*: ${state['cash']:,.2f}", parse_mode='Markdown')

async def positions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID: return
    state = load_state()
    if 'NVDA' not in state['positions']:
        await update.message.reply_text("❌ No active NVDA position.")
        return
    
    try:
        t = yf.Ticker('NVDA')
        data = t.history(period='1d', interval='5m')
        close = data['Close']
        ma = close.rolling(window=WINDOW).mean()
        std = close.rolling(window=WINDOW).std()
        z = (close.iloc[-1] - ma.iloc[-1]) / std.iloc[-1]
        entry = state['positions']['NVDA']['entry_price']
        await update.message.reply_text(f"🎯 *NVDA Status*\nEntry: ${entry:.2f}\nCurrent Z-Score: {z:.2f}", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# --- Main Loop ---
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("positions", positions_cmd))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    logging.info("Bot is listening...")
    
    # Trading Loop
    while True:
        await run_trading_cycle(app)
        await asyncio.sleep(300) # 5 minutes

if __name__ == "__main__":
    asyncio.run(main())
