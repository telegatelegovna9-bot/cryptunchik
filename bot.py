import asyncio
import pytz
import telegram
import os
import sys
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from monitor.fetcher import get_all_futures_tickers, fetch_ohlcv_binance
from monitor.analyzer import analyze
from monitor.logger import log
from monitor.settings import load_config, save_config

config = load_config()
scheduler = AsyncIOScheduler(timezone=pytz.UTC)
semaphore = asyncio.Semaphore(10)  # общий Semaphore для асинхронных задач

# Исключаем по ключевым словам
EXCLUDED_KEYWORDS = ["ALPHA", "WEB3", "AI", "BOT"]

def update_config(key, value):
    config[key] = value
    save_config(config)

def parse_human_number(value: str) -> float:
    """Преобразует строку с суффиксом (K, M, B) в число."""
    value = value.strip().upper()
    multiplier = 1
    if value.endswith("K"):
        multiplier = 1_000
        value = value[:-1]
    elif value.endswith("M"):
        multiplier = 1_000_000
        value = value[:-1]
    elif value.endswith("B"):
        multiplier = 1_000_000_000
        value = value[:-1]
    try:
        return float(value) * multiplier
    except ValueError:
        raise ValueError("❌ Неверный формат числа. Используйте: 100K, 2.5M, 1B")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [KeyboardButton("Start Monitor"), KeyboardButton("Stop Monitor")],
        [KeyboardButton("Set Timeframe"), KeyboardButton("Set Volume")],
        [KeyboardButton("Set Change"), KeyboardButton("Toggle Change")],
        [KeyboardButton("Status"), KeyboardButton("Reload Bot")]
    ]
    reply_markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    await update.message.reply_text("🤖 Бот готов к работе.", reply_markup=reply_markup)

async def run_monitor():
    tickers = await get_all_futures_tickers()

    # Фильтрация по ключевым словам
    tickers = [t for t in tickers if not any(k in t.upper() for k in EXCLUDED_KEYWORDS)]
    log(f"Всего тикеров после фильтра: {len(tickers)}")

    if not tickers:
        log("Тикеры не найдены, проверка остановлена.")
        return

    total, signals = 0, 0

    async def process_symbol(symbol):
        nonlocal total, signals
        async with semaphore:
            try:
                df = await fetch_ohlcv_binance(symbol, config['timeframe'])
                if df.empty:
                    log(f"[{symbol}] свечи не получены")
                    return

                is_signal, info = analyze(df, config)
                total += 1

                if is_signal:
                    signals += 1
                    await send_signal(symbol, df, info)
                else:
                    log(f"[{symbol}] Условия не выполнены")
            except Exception as e:
                log(f"Ошибка {symbol}: {e}")

    await asyncio.gather(*(process_symbol(symbol) for symbol in tickers))
    log(f"Обработано: {total}, Сигналов: {signals}")

async def send_signal(symbol, df, info):
    bot = telegram.Bot(token=config['telegram_token'])

    # вычисляем изменение цены
    try:
        last_close = float(df['close'].iloc[-1])
        prev_close = float(df['close'].iloc[-2])
    except Exception:
        last_close = prev_close = None

    tf_change = ((last_close - prev_close) / prev_close * 100) if prev_close else 0.0
    signal_type_text = "🚀 ПАМП" if tf_change > 0 else "🔻 ДАМП"

    if abs(tf_change) >= max(2.0, config.get('price_change_threshold', 5.0)):
        brief_info = "Резкий рост! Возможен памп 💹" if tf_change > 0 else "Резкое падение. Возможен дамп 📉"
    else:
        brief_info = "Движение есть, требуется дополнительный анализ."

    symbol_tv = symbol.replace("/", "").replace(":", "")
    tradingview_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol_tv}.P"

    html = (
        f"<b>{signal_type_text}</b> | <b>{tf_change:.2f}%</b>\n"
        f"Монета: <code>{symbol}</code>\n"
        f"Цена сейчас: <b>{last_close:.6f} USDT</b>\n"
        f"{brief_info}\n\n"
        f"<a href=\"{tradingview_url}\">Открыть полный график на TradingView</a>\n\n"
        f"<i>Доп. инфо:</i> {info if isinstance(info, str) else ''}"
    )

    await bot.send_message(chat_id=config['chat_id'], text=html, parse_mode="HTML", disable_web_page_preview=True)
    log(f"[{symbol}] Сигнал отправлен: {signal_type_text} | {tf_change:.2f}% | price={last_close}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "Start Monitor":
        config['bot_status'] = True
        save_config(config)
        if scheduler.get_job('monitor'):
            scheduler.remove_job('monitor')
        scheduler.add_job(run_monitor, 'interval', minutes=1, id='monitor')
        await update.message.reply_text("Мониторинг запущен ✅")

    elif text == "Stop Monitor":
        config['bot_status'] = False
        save_config(config)
        scheduler.remove_all_jobs()
        await update.message.reply_text("Мониторинг остановлен ⛔")

    elif text == "Set Timeframe":
        await update.message.reply_text("Введите таймфрейм (1m, 5m, 15m):")
        context.user_data['awaiting'] = 'timeframe'

    elif text == "Set Volume":
        await update.message.reply_text("Введите фильтр объема (например, 100K, 2M, 1B):")
        context.user_data['awaiting'] = 'volume'

    elif text == "Set Change":
        await update.message.reply_text("Введите порог изменения цены в % (например, 5.0):")
        context.user_data['awaiting'] = 'change'

    elif text == "Toggle Change":
        config['price_change_filter'] = not config['price_change_filter']
        save_config(config)
        await update.message.reply_text(
            f"Фильтр изменения: {'включен' if config['price_change_filter'] else 'выключен'}"
        )

    elif text == "Status":
        vol = config.get('volume_filter', 0)
        try:
            vol_str = human_readable_number(int(vol))
        except Exception:
            vol_str = f"{vol:,}"
        msg = (
            f"Таймфрейм: {config.get('timeframe')}\n"
            f"Фильтр объема: {vol_str}\n"
            f"Фильтр изменения: {config.get('price_change_filter')} ({config.get('price_change_threshold')}%)\n"
            f"Статус бота: {'включен' if config.get('bot_status') else 'выключен'}"
        )
        await update.message.reply_text(msg)

    elif text == "Reload Bot":
        username = update.message.from_user.username or update.message.from_user.first_name
        log(f"♻️ Перезагрузка запрошена пользователем @{username}")
        await update.message.reply_text("♻️ Перезагрузка бота...")
        await reload_bot()

    elif 'awaiting' in context.user_data:
        if context.user_data['awaiting'] == 'timeframe':
            update_config('timeframe', text)
            await update.message.reply_text(f"Таймфрейм обновлён: {text}")
        elif context.user_data['awaiting'] == 'volume':
            try:
                volume_value = parse_human_number(text)
                update_config('volume_filter', volume_value)
                await update.message.reply_text(f"Фильтр объема обновлён: {human_readable_number(int(volume_value))}")
            except ValueError as e:
                await update.message.reply_text(str(e))
        elif context.user_data['awaiting'] == 'change':
            update_config('price_change_threshold', float(text))
            await update.message.reply_text(f"Порог изменения обновлён: {text}%")
        context.user_data.pop('awaiting')

def human_readable_number(n: int) -> str:
    """Возвращает читаемую строку: 1_200_000 -> '1.2M'"""
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    return str(n)

async def reload_bot():
    """Перезапуск бота без полного выключения"""
    log("♻️ Выполняется перезагрузка бота...")
    scheduler.remove_all_jobs()
    python = sys.executable
    os.execl(python, python, *sys.argv)

if __name__ == '__main__':
    app = ApplicationBuilder().token(config['telegram_token']).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    scheduler.start()
    print("🚀 Бот запущен. Используй /start в Telegram.")
    app.run_polling()
