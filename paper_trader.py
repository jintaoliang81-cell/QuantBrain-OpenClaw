import yfinance as yf
import pandas as pd
import numpy as np
import os
import json
import asyncio
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

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

# Telegram & LLM Config
TELEGRAM_BOT_TOKEN = "8519943787:AAGDrCb26d1h4c_Gfw0sRqGKSjjlfgKn5Bg"
TELEGRAM_CHAT_ID = 8349528219
# OpenAI API Key is pre-configured in environment

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
            
            if current_z >= Z_PARTIAL_SELL_THRESHOLD and not positions[ticker].get('partial_sold', False):
                sell_shares = shares * 0.5
                pnl = (current_price - entry_price) * sell_shares
                cash += current_price * sell_shares
                positions[ticker]['shares'] -= sell_shares
                positions[ticker]['partial_sold'] = True
                log_trade_to_csv(ticker, "PARTIAL_SELL", current_price, sell_shares, pnl)
                await send_telegram_notification(app, f"💰 *Partial Sell: {ticker}*\nPrice: ${current_price:.2f}\nZ-Score: {current_z:.2f}\nPnL: ${pnl:.2f}")
                shares = positions[ticker]['shares']

            trailing_stop_active = pnl_pct >= TRAILING_STOP_TRIGGER_PCT
            trailing_stop_price = high_price * (1 - TRAILING_STOP_RETRACEMENT_PCT)
            
            if trailing_stop_active and current_price <= trailing_stop_price:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade_to_csv(ticker, "TRAILING_STOP", current_price, shares, pnl)
                await send_telegram_notification(app, f"🛑 *Trailing Stop: {ticker}*\nExit Price: ${current_price:.2f}\nPnL: ${pnl:.2f}")
                del positions[ticker]
                continue

            if pnl_pct <= -STOP_LOSS_PCT:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade_to_csv(ticker, "STOP_LOSS", current_price, shares, pnl)
                await send_telegram_notification(app, f"⚠️ *Stop Loss: {ticker}*\nExit Price: ${current_price:.2f}\nPnL: ${pnl:.2f}")
                del positions[ticker]
                continue

        except Exception as e:
            logging.error(f"Error managing {ticker}: {e}")

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

# --- NLP & LLM Logic ---
client = OpenAI()

async def get_llm_response(user_text, context_data):
    system_prompt = (
        "You are 'Liang Quant Commander', a professional, slightly witty, and data-driven quantitative trading partner. "
        "Your partner is 'Liang'. You are direct about risks and base your answers on the provided data. "
        "If the user wants to see status, positions, or balance, interpret their intent and provide the info. "
        "If they want to sell everything, confirm the intent. "
        "Keep responses concise and professional."
    )
    user_prompt = f"Context Data: {json.dumps(context_data)}\nUser Message: {user_text}"
    
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Liang, I'm having trouble thinking right now. Error: {e}"

# --- Telegram Handlers ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID: return
    user_text = update.message.text
    state = load_state()
    
    # Gather current market data for context
    market_context = {"cash": state['cash'], "positions": {}}
    for ticker, info in state['positions'].items():
        try:
            t = yf.Ticker(ticker)
            curr = float(t.history(period='1d', interval='1m')['Close'].iloc[-1])
            market_context["positions"][ticker] = {
                "entry": info['entry_price'],
                "current": curr,
                "pnl_pct": (curr - info['entry_price']) / info['entry_price'] * 100
            }
        except: pass

    # Special Command: Stop All
    if "全部賣掉" in user_text or "stop all" in user_text.lower():
        cash = state['cash']
        for ticker, info in list(state['positions'].items()):
            try:
                t = yf.Ticker(ticker)
                curr = float(t.history(period='1d', interval='1m')['Close'].iloc[-1])
                cash += curr * info['shares']
                log_trade_to_csv(ticker, "STOP_ALL_MANUAL", curr, info['shares'])
                del state['positions'][ticker]
            except: pass
        state['cash'] = cash
        save_state(state)
        await update.message.reply_text("Liang, I've liquidated all positions as requested. We are 100% in cash now. Safety first.", parse_mode='Markdown')
        return

    # General NLP Response
    response = await get_llm_response(user_text, market_context)
    await update.message.reply_text(response, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_message(update, context) # Let NLP handle it

# --- Main Loop ---
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("status", handle_message))
    app.add_handler(CommandHandler("balance", handle_message))
    app.add_handler(CommandHandler("positions", handle_message))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    logging.info("NLP Bot is listening...")
    
    while True:
        await run_trading_cycle(app)
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
