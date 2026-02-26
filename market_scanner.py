import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

# Configuration
# Using a subset of Nasdaq 100 for speed in this environment
TICKERS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'PEP', 'COST',
    'ADBE', 'CSCO', 'NFLX', 'AMD', 'INTC', 'CMCSA', 'TMUS', 'AMGN', 'TXN', 'HON',
    'QCOM', 'INTU', 'SBUX', 'AMAT', 'ISRG', 'MDLZ', 'GILD', 'BKNG', 'ADI', 'VRTX'
]
WINDOW = 20
Z_SCORE_THRESHOLD = -2.0
MAX_POSITIONS = 5

def get_market_signals():
    signals = []
    print(f"Scanning {len(TICKERS)} tickers...")
    
    for ticker in TICKERS:
        try:
            # Fetch 1d data with 5m interval
            data = yf.download(ticker, period='5d', interval='5m', progress=False)
            if len(data) < WINDOW:
                continue
            
            # Calculate Z-Score
            data['MA'] = data['Close'].rolling(window=WINDOW).mean()
            data['STD'] = data['Close'].rolling(window=WINDOW).std()
            data['Z_Score'] = (data['Close'] - data['MA']) / data['STD']
            
            # Volume Check
            avg_volume = data['Volume'].mean()
            current_volume = data['Volume'].iloc[-1]
            current_z = data['Z_Score'].iloc[-1]
            current_price = data['Close'].iloc[-1]
            
            # Signal Logic
            if current_z < Z_SCORE_THRESHOLD and current_volume > avg_volume:
                signals.append({
                    'Ticker': ticker,
                    'Price': round(float(current_price), 2),
                    'Z-Score': round(float(current_z), 2),
                    'Volume_Ratio': round(float(current_volume / avg_volume), 2),
                    'Signal': 'BUY'
                })
        except Exception as e:
            print(f"Error scanning {ticker}: {e}")
            
    return signals

if __name__ == "__main__":
    results = get_market_signals()
    if results:
        # Sort by Z-Score (most oversold first)
        results = sorted(results, key=lambda x: x['Z-Score'])
        print("\n--- TOP BUY SIGNALS ---")
        for r in results[:3]:
            print(f"{r['Ticker']}: Price ${r['Price']}, Z-Score {r['Z-Score']}, Vol Ratio {r['Volume_Ratio']}")
    else:
        print("\nNo signals triggered at this time.")
