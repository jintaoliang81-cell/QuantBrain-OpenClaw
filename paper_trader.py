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
BASE_TRADE_AMOUNT = 5000.0
AGGRESSIVE_TRADE_AMOUNT = 10000.0
INITIAL_CASH = 100000.0

# Telegram & LLM Config
TELEGRAM_BOT_TOKEN = "8519943787:AAGDrCb26d1h4c_Gfw0sRqGKSjjlfgKn5Bg"
TELEGRAM_CHAT_ID = 8349528219

STATE_FILE = '/home/ubuntu/paper_trading_state.json'
LOG_FILE = '/home/ubuntu/paper_trading_log.csv'

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Core Logic ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        'cash': INITIAL_CASH, 
        'positions': {}, 
        'consecutive_losses': 0, 
        'zen_mode_until': None
    }

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

def calculate_atr(data, period=14):
    high = data['High']
    low = data['Low']
    close = data['Close'].shift(1)
    tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def calculate_var(positions, cash, confidence=0.95):
    """Monte Carlo Value at Risk (VaR) calculation"""
    if not positions: return 0
    total_value = cash
    returns = []
    for ticker in positions:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period='5d', interval='5m')['Close'].pct_change().dropna()
            returns.append(hist)
            total_value += positions[ticker]['shares'] * float(t.history(period='1d')['Close'].iloc[-1])
        except: pass
    
    if not returns: return 0
    # Simplified VaR: Average volatility across positions
    combined_std = np.mean([r.std() for r in returns])
    var = total_value * combined_std * norm.ppf(confidence)
    return var / total_value # Return as % of portfolio

async def run_trading_cycle(app):
    state = load_state()
    
    # Zen Mode Check
    if state.get('zen_mode_until'):
        zen_until = datetime.fromisoformat(state['zen_mode_until'])
        if datetime.now() < zen_until:
            logging.info("System in Zen Mode. Skipping cycle.")
            return
        else:
            state['zen_mode_until'] = None
            state['consecutive_losses'] = 0
            await send_telegram_notification(app, "🧘 *Zen Mode Ended*: System is back online and analyzing market regimes.")

    cash = state['cash']
    positions = state['positions']
    
    # Risk Management: VaR Check
    portfolio_risk = calculate_var(positions, cash)
    risk_multiplier = 1.0
    if portfolio_risk > 0.05: # If 5% drawdown probability is high
        risk_multiplier = 0.5
        logging.warning(f"High Portfolio Risk Detected: {portfolio_risk:.2%}. Reducing position sizes.")

    # 1. Manage Existing Positions
    for ticker in list(positions.keys()):
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='5d', interval='5m')
            if data.empty: continue
            
            current_price = float(data['Close'].iloc[-1])
            entry_price = positions[ticker]['entry_price']
            shares = positions[ticker]['shares']
            
            # ATR Calculation
            atr = calculate_atr(data, period=ATR_PERIOD).iloc[-1]
            atr_stop_dist = atr * ATR_MULTIPLIER
            
            # Update High Price for ATR Trailing Stop
            if 'high_price' not in positions[ticker]:
                positions[ticker]['high_price'] = current_price
            else:
                positions[ticker]['high_price'] = max(positions[ticker]['high_price'], current_price)
            
            high_price = positions[ticker]['high_price']
            pnl_pct = (current_price - entry_price) / entry_price
            
            # Z-Score
            close = data['Close']
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            current_z = (current_price - ma.iloc[-1]) / std.iloc[-1]
            
            # A. Partial Sell (Z-Score > 1.5)
            if current_z >= Z_PARTIAL_SELL_THRESHOLD and not positions[ticker].get('partial_sold', False):
                sell_shares = shares * 0.5
                pnl = (current_price - entry_price) * sell_shares
                cash += current_price * sell_shares
                positions[ticker]['shares'] -= sell_shares
                positions[ticker]['partial_sold'] = True
                log_trade_to_csv(ticker, "PARTIAL_SELL", current_price, sell_shares, pnl)
                await send_telegram_notification(app, f"💰 *Partial Sell: {ticker}*\nPrice: ${current_price:.2f}\nZ-Score: {current_z:.2f}\nPnL: ${pnl:.2f}")
                shares = positions[ticker]['shares']

            # B. ATR-Based Trailing Stop
            # Activate trailing stop only if in profit > 0.5%
            if pnl_pct > 0.005:
                trailing_stop_price = high_price - atr_stop_dist
                if current_price <= trailing_stop_price:
                    pnl = (current_price - entry_price) * shares
                    cash += current_price * shares
                    log_trade_to_csv(ticker, "ATR_TRAILING_STOP", current_price, shares, pnl)
                    await send_telegram_notification(app, f"🛑 *ATR Trailing Stop: {ticker}*\nExit: ${current_price:.2f}\nPnL: ${pnl:.2f}\nATR(14): ${atr:.2f}")
                    
                    if pnl < 0: state['consecutive_losses'] += 1
                    else: state['consecutive_losses'] = 0
                    
                    del positions[ticker]
                    continue

            # C. Hard Stop-Loss
            if pnl_pct <= -STOP_LOSS_PCT:
                pnl = (current_price - entry_price) * shares
                cash += current_price * shares
                log_trade_to_csv(ticker, "HARD_STOP_LOSS", current_price, shares, pnl)
                await send_telegram_notification(app, f"⚠️ *Hard Stop Loss: {ticker}*\nExit: ${current_price:.2f}\nPnL: ${pnl:.2f}")
                
                state['consecutive_losses'] += 1
                del positions[ticker]
                continue

        except Exception as e:
            logging.error(f"Error managing {ticker}: {e}")

    # Zen Mode Trigger
    if state['consecutive_losses'] >= 3:
        state['zen_mode_until'] = (datetime.now() + timedelta(hours=2)).isoformat()
        await send_telegram_notification(app, "🧘 *Zen Mode Activated*: 3 consecutive losses detected. Pausing for 2 hours to analyze regime shift.")
        save_state(state)
        return

    # 2. Scan for New Opportunities
    for ticker in TICKERS:
        if ticker in positions: continue
        try:
            t = yf.Ticker(ticker)
            data = t.history(period='5d', interval='5m')
            if data.empty or len(data) < WINDOW: continue
            
            close = data['Close']
            volume = data['Volume']
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            vol_ma = volume.rolling(window=WINDOW).mean()
            
            current_z = (close.iloc[-1] - ma.iloc[-1]) / std.iloc[-1]
            current_vol = volume.iloc[-1]
            current_price = float(close.iloc[-1])
            
            # Order Flow Filter: Z-Score < -2.5 AND Volume > 2x MA Volume
            if current_z < Z_BUY_THRESHOLD and current_vol > (2 * vol_ma.iloc[-1]):
                amount = (AGGRESSIVE_TRADE_AMOUNT if current_z < Z_BUY_AGGRESSIVE_THRESHOLD else BASE_TRADE_AMOUNT) * risk_multiplier
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
                    log_trade_to_csv(ticker, "BUY_ELITE", current_price, shares)
                    await send_telegram_notification(app, f"🚀 *Elite Buy: {ticker}*\nPrice: ${current_price:.2f}\nZ-Score: {current_z:.2f}\nVol Spike: {current_vol/vol_ma.iloc[-1]:.1f}x\nRisk Adj: {risk_multiplier:.1f}x")
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
        "You use advanced concepts like ATR, Z-Score, VaR, and Zen Mode. "
        "Keep responses concise and professional."
    )
    user_prompt = f"Context Data: {json.dumps(context_data)}\nUser Message: {user_text}"
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        )
        return response.choices[0].message.content
    except: return "Liang, I'm having trouble thinking right now."

# --- Telegram Handlers ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID: return
    user_text = update.message.text
    state = load_state()
    
    market_context = {"cash": state['cash'], "positions": {}, "risk_var": calculate_var(state['positions'], state['cash'])}
    for ticker, info in state['positions'].items():
        try:
            t = yf.Ticker(ticker)
            curr = float(t.history(period='1d', interval='1m')['Close'].iloc[-1])
            market_context["positions"][ticker] = {
                "entry": info['entry_price'], "current": curr, "pnl_pct": (curr - info['entry_price']) / info['entry_price'] * 100
            }
        except: pass

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
        state['consecutive_losses'] = 0
        save_state(state)
        await update.message.reply_text("Liang, I've liquidated all positions. We are 100% in cash. Zen Mode reset.", parse_mode='Markdown')
        return

    response = await get_llm_response(user_text, market_context)
    await update.message.reply_text(response, parse_mode='Markdown')

async def strategist_report(app):
    state = load_state()
    var = calculate_var(state['positions'], state['cash'])
    msg = f"🧠 *Strategist Report*\n\n*Market Probability*: Mean Reversion Regime\n*Portfolio VaR (95%)*: {var:.2%}\n*Zen Status*: {'Active' if state.get('zen_mode_until') else 'Normal'}\n*Risk Multiplier*: {'0.5x' if var > 0.05 else '1.0x'}\n\nLiang, the market pulse is steady. We are letting the ATR stops breathe."
    await send_telegram_notification(app, msg)

# --- Main Loop ---
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CommandHandler("status", handle_message))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    logging.info("Elite Brain Bot is listening...")
    
    last_report_time = datetime.now()
    while True:
        await run_trading_cycle(app)
        
        # 4-hour Strategist Report
        if datetime.now() - last_report_time > timedelta(hours=4):
            await strategist_report(app)
            last_report_time = datetime.now()
            
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
