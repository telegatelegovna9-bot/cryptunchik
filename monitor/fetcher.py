import aiohttp
import pandas as pd
from monitor.logger import log

BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"

async def get_all_futures_tickers():
    try:
        url = f"{BINANCE_FAPI}/ticker/24hr"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                tickers = [item['symbol'] for item in data if item['symbol'].endswith('USDT')]
                log(f"Всего тикеров: {len(tickers)}")
                return tickers
    except Exception as e:
        log(f"Ошибка получения тикеров: {e}")
        return []

async def fetch_ohlcv_binance(symbol, timeframe='1m', limit=100):
    interval_map = {'1m':'1m', '5m':'5m', '15m':'15m'}
    interval = interval_map.get(timeframe,'1m')
    url = f"{BINANCE_FAPI}/klines"
    params = {"symbol":symbol, "interval":interval, "limit":limit}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                if not data:
                    log(f"{symbol} - данные OHLCV пусты")
                    return pd.DataFrame()
                df = pd.DataFrame(data, columns=['timestamp','open','high','low','close','volume',
                                                 'close_time','quote_asset_volume','num_trades',
                                                 'taker_buy_base','taker_buy_quote','ignore'])
                df = df[['timestamp','open','high','low','close','volume']]
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
                return df
    except Exception as e:
        log(f"Ошибка получения OHLCV для {symbol}: {e}")
        return pd.DataFrame()
