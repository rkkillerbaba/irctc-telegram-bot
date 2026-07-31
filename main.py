import os
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import httpx
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# --- Dummy Web Server for Render ---
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

# --- Conversation States ---
TRAIN_NO, DATE, STATION, COACH_INPUT = range(4)
user_data_store = {}

# Common Headers to mimic Real Browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://reservationchart.online",
    "Referer": "https://reservationchart.online/",
    "bmirak": "webbm"
}

# --- Step Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 IRCTC Fast Chart Bot में आपका स्वागत है!\n\nTrain Number दर्ज करें (उदा. 22188):")
    return TRAIN_NO

async def receive_train_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id] = {'train_no': update.message.text.strip()}
    await update.message.reply_text("🗓️ Journey Date दर्ज करें (YYYY-MM-DD, उदा. 2026-07-31):")
    return DATE

async def receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id]['date'] = update.message.text.strip()
    await update.message.reply_text("🚉 Boarding Station Code दर्ज करें (उदा. ADTL, MML):")
    return STATION

async def receive_station(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_data_store[chat_id]['station'] = update.message.text.strip().upper()

    await update.message.reply_text("🚃 किस Coach की खाली सीटें देखना चाहते हैं?\nउदा. B2, D9, B1 या ALL दर्ज करें:")
    return COACH_INPUT

# --- Direct API Fetcher Logic ---
async def parse_coach_json(json_data, target_coach):
    berth_type_map = {'L': 'Lower', 'M': 'Middle', 'U': 'Upper', 'R': 'Side Lower', 'P': 'Side Upper'}
    vacant_list = []

    for berth in json_data.get('bdd', []):
        berth_no = berth.get('berthNo')
        berth_code = berth_type_map.get(berth.get('berthCode'), berth.get('berthCode'))
        
        # Segment Analysis (Free vs Occupied)
        for seg in berth.get('bsd', []):
            if not seg.get('occupancy'): # If occupancy is False -> Seat Available!
                from_stn = seg.get('from')
                to_stn = seg.get('to')
                quota = seg.get('quota', 'GN')
                vacant_list.append(f"Berth {berth_no} ({berth_code}) | {from_stn} ➔ {to_stn} [Quota: {quota}]")

    return vacant_list

async def fetch_chart_api(train_no, date, station, coach_input):
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        try:
            # 1. Fetch Train Composition (Summary)
            comp_url = "https://www.irctc.co.in/online-charts/api/trainComposition"
            payload = {
                "trainNo": train_no,
                "jDate": date,
                "boardStn": station
            }
            
            resp = await client.post(comp_url, json=payload)
            if resp.status_code != 200:
                return f"❌ IRCTC API Error ({resp.status_code}): चार्ट उपलब्ध नहीं है या स्टेशन/डेट गलत है।"

            comp_data = resp.json()
            coaches = comp_data.get("cdd", [])

            # Filter Target Coaches
            target_coach = coach_input.strip().upper()

            # 2. Fetch Specific Coach Details if requested
            coach_url = "https://www.irctc.co.in/online-charts/api/coachComposition"
            coach_payload = {
                "trainNo": train_no,
                "jDate": date,
                "boardStn": station,
                "coachName": target_coach if target_coach != "ALL" else (coaches[0]['coachName'] if coaches else "B1")
            }

            coach_resp = await client.post(coach_url, json=coach_payload)
            
            res = f"🚆 RESERVATION CHART STATUS 🚆\n"
            res += f"Train: {train_no} | Date: {date} | Station: {station}\n"
            res += "───────────────────────────\n\n"

            if coaches:
                res += "📊 Coach Summary:\n"
                for c in coaches:
                    res += f"  • {c.get('coachName')} ({c.get('classCode')}): {c.get('vacantBerths')} Vacant\n"
                res += "\n"

            if coach_resp.status_code == 200:
                coach_json = coach_resp.json()
                vacant_seats = await parse_coach_json(coach_json, target_coach)

                if vacant_seats:
                    res += f"💺 Vacant Seats for Coach {target_coach}:\n\n"
                    for idx, seat in enumerate(vacant_seats[:35], 1):
                        res += f"{idx}. 📌 {seat}\n"
                else:
                    res += f"⚠️ Coach {target_coach} में कोई खाली सीट नहीं मिली।"

            return res

        except Exception as e:
            return f"❌ API Processing Error: {str(e)}"

async def receive_coach_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    coach_choice = update.message.text.strip()
    user_data_store[chat_id]['coach'] = coach_choice
    t_info = user_data_store[chat_id]

    status_msg = await update.message.reply_text("⚡ Fast API से चार्ट डेटा निकाला जा रहा है...")

    result = await fetch_chart_api(t_info['train_no'], t_info['date'], t_info['station'], coach_choice)
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
    print("🚀 Superfast API-based Bot Active!")
    app.run_polling()

if __name__ == "__main__":
    main()
