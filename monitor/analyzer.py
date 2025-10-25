def analyze(df, config):
    """
    Простая логика анализа: возвращает True если изменение последней свечи > threshold
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]
    tf_change = abs((last['close'] - prev['close']) / prev['close'] * 100)
    info = f"🚀 Сигнал | tf_change={tf_change:.2f}%"
    if config['price_change_filter'] and tf_change < config['price_change_threshold']:
        return False, "Условия не выполнены"
    return True, info
