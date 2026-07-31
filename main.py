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
        self.wfile.write(b"IRCTC Chart Bot Active!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    print(f"🌐 Dummy Server active on port {port}")
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

# --- Auto Install Chromium ---
def ensure_playwright_browsers():
    os.system("playwright install chromium")

ensure_playwright_browsers()

# --- Bot States ---
TRAIN_NO, DATE, STATION, COACH_INPUT = range(4)
user_data_store = {}

# --- Commands & Handlers ---
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
        "🚃 किस Coach की खाली सीटें देखना चाहते हैं?\n\n"
        "उदाहरण: D9, D8, B1, C1 या सब देखने के लिए ALL लिखें:"
    )
    return COACH_INPUT

# --- Scraping Core Logic ---
async def fetch_chart_data(train_no: str, date: str, station: str, coach_input: str):
    async with async_playwright() as p:
        try:
            # Fixing ERR_HTTP2_PROTOCOL_ERROR using anti-bot flags & disabling HTTP2
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-http2",  # Prevents ERR_HTTP2_PROTOCOL_ERROR
                    "--ignore-certificate-errors",
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()

            # PAGE 1: Open Form Page
            await page.goto("https://www.irctc.co.in/online-charts/", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # Fill Train Number
            train_input = page.locator("input[placeholder*='Train']")
            await train_input.fill(train_no)
            await page.wait_for_timeout(1000)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1000)

            # Submit Form
            await page.click("button:has-text('GET TRAIN CHART')")
            await page.wait_for_timeout(4000)

            # PAGE 2: Click Class Button (e.g. SECOND SITTING, THIRD AC)
            class_buttons = await page.query_selector_all("button.mat-raised-button, button.btn")
            if class_buttons:
                # Click first available class button
                await class_buttons[0].click()
                await page.wait_for_timeout(4000)

            # PAGE 3: Extract Table Data (From | To | Coach | Berth No)
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

                    # Apply Coach Filter
                    if target_coach != "ALL" and target_coach != coach.upper():
                        continue

                    vacant_seats.append(f"From: {from_stn} ➔ To: {to_stn} | Coach: {coach} | Berth: {berth}")

            await browser.close()

            # Formatting Results
            res = f"🚆 RESERVATION CHART STATUS 🚆\n"
            res += f"Train: {train_no} | Date: {date} | Station: {station}\n"
            res += f"Filter Coach: {target_coach}\n"
            res += "───────────────────────────\n\n"

            if vacant_seats:
                res += "💺 Vacant Berth Details:\n\n"
                for idx, seat in enumerate(vacant_seats[:30], 1):  # Top 30 seats
                    res += f"{idx}. 📌 {seat}\n"
            else:
                res += f"⚠️ Coach '{target_coach}' में कोई खाली सीट नहीं मिली या चार्ट अभी तैयार नहीं हुआ है।"

            return res

        except Exception as e:
            return f"❌ Error: {str(e)}"

# --- Final Process ---
async def receive_coach_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    coach_choice = update.message.text.strip()
    user_data_store[chat_id]['coach'] = coach_choice

    t_info = user_data_store[chat_id]
    
    status_msg = await update.message.reply_text(
        f"⏳ IRCTC Chart से Coach '{coach_choice.upper()}' की सीटें निकाली जा रही हैं...\nकृपया 5-10 सेकंड प्रतीक्षा करें..."
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
    print("🤖 Bot Active...")
    app.run_polling()

if __name__ == "__main__":
    main()
