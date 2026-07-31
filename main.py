import os
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import httpx
from telegram import Update
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
        self.wfile.write(b"Bot Active")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

# --- States ---
TRAIN_NO, DATE, STATION, COACH_INPUT = range(4)
user_data_store = {}

# Exact Headers required by IRCTC Charts API
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.irctc.co.in",
    "Referer": "https://www.irctc.co.in/online-charts/",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin"
}

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

async def parse_coach_json(json_data):
    berth_type_map = {'L': 'Lower', 'M': 'Middle', 'U': 'Upper', 'R': 'Side Lower', 'P': 'Side Upper'}
    vacant_list = []

    if not json_data or 'bdd' not in json_data:
        return []

    for berth in json_data.get('bdd', []):
        berth_no = berth.get('berthNo')
        berth_code = berth_type_map.get(berth.get('berthCode'), berth.get('berthCode'))
        
        # Check segment occupancy
        for seg in berth.get('bsd', []):
            if not seg.get('occupancy'):  # False means seat is VACANT!
                from_stn = seg.get('from')
                to_stn = seg.get('to')
                quota = seg.get('quota', 'GN')
                vacant_list.append(f"Berth {berth_no} ({berth_code}) | {from_stn} ➔ {to_stn} [Quota: {quota}]")

    return vacant_list

async def fetch_chart_api(train_no, date, station, coach_input):
    # Enable HTTP/1.1 and Cookie tracking
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    
    async with httpx.AsyncClient(
        headers=HEADERS, 
        timeout=20.0, 
        verify=False, 
        http2=False, 
        limits=limits
    ) as client:
        try:
            # 1. First visit main page to get initial cookies
            await client.get("https://www.irctc.co.in/online-charts/")
            
            # 2. Call Train Composition API
            comp_url = "https://www.irctc.co.in/online-charts/api/trainComposition"
            payload = {
                "trainNo": train_no,
                "jDate": date,
                "boardStn": station
            }
            
            resp = await client.post(comp_url, json=payload)
            if resp.status_code != 200:
                return f"❌ IRCTC Response Error ({resp.status_code}): कृपया Train No, Date या Boarding Station दोबारा जाँचें।"

            comp_data = resp.json()
            coaches = comp_data.get("cdd", [])
            target_coach = coach_input.strip().upper()

            # Find target coach or default
            selected_coach = target_coach
            if target_coach == "ALL" and coaches:
                selected_coach = coaches[0].get('coachName', 'B1')

            # 3. Call Coach Details API
            coach_url = "https://www.irctc.co.in/online-charts/api/coachComposition"
            coach_payload = {
                "trainNo": train_no,
                "jDate": date,
                "boardStn": station,
                "coachName": selected_coach
            }

            coach_resp = await client.post(coach_url, json=coach_payload)
            
            res = f"🚆 RESERVATION CHART STATUS 🚆\n"
            res += f"Train: {train_no} | Date: {date} | Boarding: {station}\n"
            res += "───────────────────────────\n\n"

            if coaches:
                res += "📊 Coaches Overview:\n"
                for c in coaches[:10]:
                    res += f"  • {c.get('coachName')} ({c.get('classCode')}): {c.get('vacantBerths')} Vacant\n"
                res += "\n"

            if coach_resp.status_code == 200:
                coach_json = coach_resp.json()
                vacant_seats = await parse_coach_json(coach_json)

                if vacant_seats:
                    res += f"💺 Vacant Seats in Coach {selected_coach}:\n\n"
                    for idx, seat in enumerate(vacant_seats[:30], 1):
                        res += f"{idx}. 📌 {seat}\n"
                else:
                    res += f"⚠️ Coach {selected_coach} में कोई खाली सीट नहीं मिली।"
            else:
                res += f"⚠️ Coach {selected_coach} का डेटा फ़ेच नहीं हो सका।"

            return res

        except Exception as e:
            return f"❌ Connection Error: {str(e)}"

async def receive_coach_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    coach_choice = update.message.text.strip()
    user_data_store[chat_id]['coach'] = coach_choice
    t_info = user_data_store[chat_id]

    status_msg = await update.message.reply_text("⚡ Fast API से डेटा निकाला जा रहा है...")

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
    print("🚀 API Bot Ready!")
    app.run_polling()

if __name__ == "__main__":
    main()
