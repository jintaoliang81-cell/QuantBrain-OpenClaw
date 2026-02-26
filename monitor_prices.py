import yfinance as yf
import pandas as pd
import time

def check_prices():
    try:
        nvda = yf.Ticker("NVDA").history(period="1d", interval="1m")
        trx = yf.Ticker("TRX-USD").history(period="1d", interval="1m")
        
        nvda_price = nvda['Close'].iloc[-1]
        trx_price = trx['Close'].iloc[-1]
        trx_open = trx['Open'].iloc[0]
        trx_change = (trx_price - trx_open) / trx_open * 100
        
        print(f"NVDA: {nvda_price:.2f}, TRX: {trx_price:.4f} ({trx_change:+.2f}%)")
        
        if nvda_price < 192:
            print("ALERT: NVDA below $192!")
        if abs(trx_change) > 3:
            print(f"ALERT: TRX volatility high: {trx_change:+.2f}%")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_prices()
