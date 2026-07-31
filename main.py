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

# --- Dummy Server for Render ---
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot Active")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

def ensure_playwright_browsers():
    os.system("playwright install chromium")

ensure_playwright_browsers()

# --- Bot Conversation States ---
TRAIN_NO, DATE, STATION, COACH_INPUT = range(4)
user_data_store = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 IRCTC Reservation Chart Bot में आपका स्वागत है!\n\n"
        "कृपया Train Number दर्ज करें (उदा. 22188):"
    )
    return TRAIN_NO

async def receive_train_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id] = {'train_no': update.message.text.strip()}
    await update.message.reply_text("🗓️ अब Journey Date दर्ज करें (Format: YYYY-MM-DD, उदा. 2026-07-31):")
    return DATE

async def receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id]['date'] = update.message.text.strip()
    await update.message.reply_text("🚉 अब Boarding Station Code दर्ज करें (उदा. MML, NDLS):")
    return STATION

async def receive_station(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_data_store[chat_id]['station'] = update.message.text.strip().upper()

    await update.message.reply_text(
        "🚃 किस Coach की सीटें देखना चाहते हैं?\n"
        "उदा. D9, D8, B1, C1 या सभी के लिए ALL लिखें:"
    )
    return COACH_INPUT

# --- Optimized Fast Scraping Logic ---
async def fetch_chart_data(train_no: str, date: str, station: str, coach_input: str):
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-http2",
                    "--blink-settings=imagesEnabled=false",
                ]
            )
            # Custom Context to Bypass Cloudflare/Bot detection
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768}
            )
            page = await context.new_page()

            # Abort heavy assets to speed up connection
            await page.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2}", lambda route: route.abort())

            # 1. Open IRCTC Page (Fast Load Mode)
            await page.goto("https://www.irctc.co.in/online-charts/", timeout=30000, wait_until="commit")
            await page.wait_for_timeout(2000)

            # 2. Fill Train Number using Keyboard Actions
            train_input = page.locator("input[placeholder*='Train']")
            await train_input.click()
            await train_input.fill(train_no)
            await page.wait_for_timeout(1500)
            
            # Use Keyboard ArrowDown & Enter to select Autocomplete option reliably
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1000)

            # 3. Fill Station Code
            stn_input = page.locator("input[placeholder*='Boarding'], input[placeholder*='Station']")
            if await stn_input.count() > 0:
                await stn_input.click()
                await stn_input.fill(station)
                await page.wait_for_timeout(1500)
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1000)

            # 4. Click Submit
            get_chart_btn = page.locator("button:has-text('GET TRAIN CHART')")
            await get_chart_btn.click()
            await page.wait_for_timeout(4000)

            # 5. Click First Class Option Button
            class_buttons = await page.query_selector_all("button")
            for btn in class_buttons:
                txt = await btn.inner_text()
                if any(c in txt for c in ["SITTING", "AC", "CHAIR", "SLEEPER", "2S", "3A", "CC"]):
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    break

            # 6. Extract Table Details
            rows = await page.query_selector_all("tr")
            target_coach = coach_input.strip().upper()
            vacant_seats = []

            for row in rows:
                cols = await row.query_selector_all("td")
                if len(cols) >= 4:
                    from_stn = (await cols[0].inner_text()).strip()
                    to_stn = (await cols[1].inner_text()).strip()
                    coach = (await cols[2].inner_text()).strip()
                    berth = (await cols[3].inner_text()).strip()

                    if target_coach != "ALL" and target_coach != coach.upper():
                        continue

                    vacant_seats.append(f"From: {from_stn} ➔ To: {to_stn} | Coach: {coach} | Berth No: {berth}")

            await browser.close()

            # Format Response
            res = f"🚆 RESERVATION CHART STATUS 🚆\n"
            res += f"Train: {train_no} | Date: {date} | Station: {station}\n"
            res += f"Filter Coach: {target_coach}\n"
            res += "───────────────────────────\n\n"

            if vacant_seats:
                res += "💺 Vacant Berth Details:\n\n"
                for idx, seat in enumerate(vacant_seats[:35], 1):
                    res += f"{idx}. 📌 {seat}\n"
            else:
                res += f"⚠️ Coach '{target_coach}' में कोई खाली सीट नहीं मिली या चार्ट लोड नहीं हो सका।"

            return res

        except Exception as e:
            return f"❌ Scraping Error: {str(e)}"

# --- Process Coach Input ---
async def receive_coach_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    coach_choice = update.message.text.strip()
    user_data_store[chat_id]['coach'] = coach_choice

    t_info = user_data_store[chat_id]
    
    status_msg = await update.message.reply_text(
        f"⏳ IRCTC से Coach '{coach_choice.upper()}' की जानकारी निकाली जा रही है...\nकृपया 5-8 सेकंड प्रतीक्षा करें..."
    )

    result = await fetch_chart_data(t_info['train_no'], t_info['date'], t_info['station'], coach_choice)
    await status_msg.edit_text(result)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ प्रक्रिया रद्द कर दी गई।")
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
            COACH_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_coach_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    print("🤖 Bot Active with Keyboard Autocomplete Navigation...")
    app.run_polling()

if __name__ == "__main__":
    main()
