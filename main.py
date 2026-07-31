import os
import asyncio
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update, ReplyKeyboardRemove
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
        self.wfile.write(b"IRCTC Chart Bot is Active!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    print(f"🌐 Dummy Web Server listening on port {port}")
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

# --- Auto Install Chromium ---
def ensure_playwright_browsers():
    print("⏳ Checking/Installing Playwright Chromium...")
    os.system("playwright install chromium")

ensure_playwright_browsers()

# --- Telegram Bot States ---
TRAIN_NO, DATE, STATION, COACH_INPUT = range(4)
user_data_store = {}

# --- Step 1: Start Command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 IRCTC Chart Search Bot में आपका स्वागत है!\n\n"
        "कृपया Train Number दर्ज करें (उदा. 22188):"
    )
    return TRAIN_NO

# --- Step 2: Receive Train Number ---
async def receive_train_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id] = {'train_no': update.message.text.strip()}
    await update.message.reply_text("🗓️ अब Journey Date दर्ज करें (Format: YYYY-MM-DD, उदा. 2026-07-31):")
    return DATE

# --- Step 3: Receive Date ---
async def receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id]['date'] = update.message.text.strip()
    await update.message.reply_text("🚉 अब Boarding Station Code दर्ज करें (उदा. MML, NDLS):")
    return STATION

# --- Step 4: Receive Station & Ask for Specific Coach ---
async def receive_station(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_data_store[chat_id]['station'] = update.message.text.strip().upper()

    await update.message.reply_text(
        "🚃 आप कौन से Coach की खाली सीटें सीट-नंबर वाइज देखना चाहते हैं?\n\n"
        "उदाहरण: D8, B1, S1 या सभी देखने के लिए ALL टाइप करें:"
    )
    return COACH_INPUT

# --- Web Scraping Logic ---
async def fetch_chart_data(train_no: str, date: str, station: str, coach_input: str):
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            # 1. Goto IRCTC Chart Portal
            await page.goto("https://www.irctc.co.in/online-charts/", timeout=60000)
            await page.wait_for_load_state("networkidle")

            # 2. Input Search Details
            await page.fill("input[placeholder='Enter Train Name/Number']", train_no)
            await page.wait_for_timeout(1000)
            await page.keyboard.press("Enter")
            
            await page.click("button:has-text('GET TRAIN CHART')")
            await page.wait_for_timeout(5000)

            # 3. Extract Available Classes Header
            class_buttons = await page.query_selector_all("button:has-text('AC'), button:has-text('SITTING'), button:has-text('CHAIR'), button:has-text('SLEEPER')")
            available_classes = []
            for btn in class_buttons:
                txt = await btn.inner_text()
                if txt.strip() and txt.strip() not in available_classes:
                    available_classes.append(txt.strip())

            # 4. Extract Vacant Rows & Filter by Target Coach
            rows = await page.query_selector_all(".vacant-berth-row, tr")
            target_coach = coach_input.strip().upper()
            vacant_seats = []
            
            for row in rows:
                text = await row.inner_text()
                if text.strip() and "Vacant" in text:
                    # Specific coach filtering (e.g. D8)
                    if target_coach != "ALL" and target_coach not in text.upper():
                        continue
                    vacant_seats.append(text.strip().replace("\n", " | "))

            await browser.close()

            # --- Formatting Final Output ---
            res = f"🚆 RESERVATION CHART STATUS 🚆\n"
            res += f"Train No: {train_no} | Date: {date} | Station: {station}\n"
            res += f"Selected Coach Filter: {target_coach}\n"
            res += "───────────────────────────\n\n"

            if available_classes:
                res += "📊 Available Classes in Train:\n"
                for cls in available_classes:
                    res += f"  • {cls}\n"
                res += "\n"

            if vacant_seats:
                res += f"💺 Vacant Seats List (Seat Number Wise):\n"
                for idx, seat in enumerate(vacant_seats, 1):
                    res += f"{idx}. 📌 {seat}\n"
            else:
                if target_coach != "ALL":
                    res += f"⚠️ Coach '{target_coach}' में कोई खाली सीट नहीं मिली या सीट का डेटा उपलब्ध नहीं है।"
                else:
                    res += f"⚠️ Train {train_no} में कोई खाली सीट नहीं मिली या चार्ट अभी तैयार नहीं हुआ है।"

            return res

        except Exception as e:
            return f"❌ Error extracting chart data: {str(e)}"

# --- Step 5: Process Coach and Send Result ---
async def receive_coach_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    coach_choice = update.message.text.strip()
    user_data_store[chat_id]['coach'] = coach_choice

    t_info = user_data_store[chat_id]
    
    status_msg = await update.message.reply_text(
        f"⏳ Fetching Vacant Seats for Coach '{coach_choice.upper()}' in Train {t_info['train_no']}...\nकृपया 5-10 सेकंड प्रतीक्षा करें..."
    )

    result = await fetch_chart_data(t_info['train_no'], t_info['date'], t_info['station'], coach_choice)
    
    await status_msg.edit_text(result)
    return ConversationHandler.END

# --- Cancel Command ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ प्रक्रिया रद्द कर दी गई।")
    return ConversationHandler.END

# --- Main App ---
def main():
    BOT_TOKEN = os.getenv("BOT_TOKEN")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            TRAIN_NO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_train_no)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_date)],
            STATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_station)],
            COACH_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_coach_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    print("🤖 Bot Active with Seat Number Wise Output...")
    app.run_polling()

if __name__ == "__main__":
    main()
