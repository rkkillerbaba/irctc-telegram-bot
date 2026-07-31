import os
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from playwright.async_api import async_playwright

# --- Dummy HTTP Server for Render Free Web Service ---
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is active and running!")

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    print(f"🌐 Dummy Web Server listening on port {port}")
    server.serve_forever()

# Background में Dummy Server चालू करें
threading.Thread(target=start_dummy_server, daemon=True).start()

# --- Telegram Bot Logic ---
TRAIN_NO, DATE, STATION, COACH_CHOICE = range(4)
user_data_store = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **IRCTC Chart Search Bot** में आपका स्वागत है!\n\n"
        "कृपया **Train Number** दर्ज करें (उदा. `12952`):"
    )
    return TRAIN_NO

async def receive_train_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id] = {'train_no': update.message.text.strip()}
    await update.message.reply_text("🗓️ अब **Journey Date** दर्ज करें (Format: `YYYY-MM-DD`, उदा. `2026-08-05`):")
    return DATE

async def receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id]['date'] = update.message.text.strip()
    await update.message.reply_text("🚉 अब **Boarding Station Code** दर्ज करें (उदा. `NDLS`, `MMCT`):")
    return STATION

async def receive_station(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_data_store[chat_id]['station'] = update.message.text.strip().upper()

    reply_keyboard = [["All Coaches", "S1", "S2"], ["B1", "B2", "A1"], ["H1"]]
    markup = ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        "🚂 आप किस **Coach** की सीटें देखना चाहते हैं? चुनें या टाइप करें (उदा. `B1` या `All`):",
        reply_markup=markup
    )
    return COACH_CHOICE

async def fetch_chart_data(train_no: str, date: str, station: str, coach_choice: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            await page.goto("https://www.irctc.co.in/online-charts/", timeout=60000)
            await page.wait_for_load_state("networkidle")

            await page.fill("input[placeholder='Enter Train Name/Number']", train_no)
            await page.wait_for_timeout(1000)
            await page.keyboard.press("Enter")
            
            await page.click("button:has-text('GET TRAIN CHART')")
            await page.wait_for_timeout(5000)

            vacant_seats_list = []
            rows = await page.query_selector_all(".vacant-berth-row, tr")
            
            for row in rows:
                text = await row.inner_text()
                if text.strip():
                    if coach_choice.upper() != "ALL" and coach_choice.upper() not in text.upper():
                        continue
                    vacant_seats_list.append(text.strip().replace("\n", " | "))

            await browser.close()

            if vacant_seats_list:
                res = f"🎫 **VACANT SEAT DETAILS ({coach_choice.upper()})** 🎫\n"
                res += f"🚆 Train: `{train_no}` | 📅 `{date}` | 🚉 `{station}`\n"
                res += "───────────────────────────\n\n"
                for seat in vacant_seats_list[:20]:
                    res += f"📌 {seat}\n"
                return res
            else:
                return f"⚠️ Train {train_no} ({coach_choice}) के लिए कोई खाली सीट नहीं मिली।"

        except Exception as e:
            await browser.close()
            return f"❌ त्रुटि: {str(e)}"

async def receive_coach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    coach_choice = update.message.text.strip()
    user_data_store[chat_id]['coach'] = coach_choice
    t_info = user_data_store[chat_id]
    
    status_msg = await update.message.reply_text(
        f"⏳ **Searching Chart for Coach {coach_choice}...**\nकृपया प्रतीक्षा करें...",
        reply_markup=ReplyKeyboardRemove()
    )

    result = await fetch_chart_data(t_info['train_no'], t_info['date'], t_info['station'], coach_choice)
    await status_msg.edit_text(result, parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ प्रक्रिया रद्द कर दी गई।", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    BOT_TOKEN = os.getenv("BOT_TOKEN")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            TRAIN_NO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_train_no)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_date)],
            STATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_station)],
            COACH_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_coach)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    print("🤖 Bot Active with Free Web Service Port Binding...")
    app.run_polling()

if __name__ == "__main__":
    main()
