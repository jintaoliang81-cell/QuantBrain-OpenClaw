import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

# Configuration
TICKERS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AVGO', 'PEP', 'COST',
    'ADBE', 'CSCO', 'NFLX', 'AMD', 'INTC', 'CMCSA', 'TMUS', 'AMGN', 'TXN', 'HON',
    'QCOM', 'INTU', 'SBUX', 'AMAT', 'ISRG', 'MDLZ', 'GILD', 'BKNG', 'ADI', 'VRTX'
]
WINDOW = 20
Z_SCORE_THRESHOLD = -2.0

def get_market_signals():
    signals = []
    print(f"Scanning {len(TICKERS)} tickers...")
    
    for ticker in TICKERS:
        try:
            # Fetch data
            data = yf.download(ticker, period='5d', interval='5m', progress=False)
            if data.empty or len(data) < WINDOW:
                continue
            
            # Use only 'Close' and 'Volume' columns to avoid multi-index issues
            close = data['Close'].squeeze()
            volume = data['Volume'].squeeze()
            
            # Calculate Indicators
            ma = close.rolling(window=WINDOW).mean()
            std = close.rolling(window=WINDOW).std()
            z_score = (close - ma) / std
            
            # Current values
            current_z = z_score.iloc[-1]
            current_price = close.iloc[-1]
            current_volume = volume.iloc[-1]
            avg_volume = volume.mean()
            
            # Signal Logic
            if current_z < Z_SCORE_THRESHOLD and current_volume > avg_volume:
                signals.append({
                    'Ticker': ticker,
                    'Price': round(float(current_price), 2),
                    'Z-Score': round(float(current_z), 2),
                    'Volume_Ratio': round(float(current_volume / avg_volume), 2)
                })
        except Exception as e:
            print(f"Error scanning {ticker}: {e}")
            
    return signals

if __name__ == "__main__":
    results = get_market_signals()
    if results:
        results = sorted(results, key=lambda x: x['Z-Score'])
        print("\n--- TOP BUY SIGNALS ---")
        for r in results[:3]:
            print(f"{r['Ticker']}: Price ${r['Price']}, Z-Score {r['Z-Score']}, Vol Ratio {r['Volume_Ratio']}")
    else:
        print("\nNo signals triggered at this time (Z-Score < -2.0 and Vol > Avg).")
