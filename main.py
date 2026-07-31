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

# --- Dummy Web Server for Render Health Check ---
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"IRCTC Bot is Active and Healthy!")

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

# 🔥 Exact headers from your screenshot (No cookies needed!)
HEADERS = {
    "Host": "www.irctc.co.in",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Bmirak": "webbm",
    "Content-Type": "application/json",
    "Origin": "https://reservationchart.online",
    "Referer": "https://reservationchart.online/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 IRCTC Superfast Chart Bot में आपका स्वागत है!\n\nTrain Number दर्ज करें (उदा. 12191):")
    return TRAIN_NO

async def receive_train_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id] = {'train_no': update.message.text.strip()}
    await update.message.reply_text("🗓️ Journey Date दर्ज करें (YYYY-MM-DD, उदा. 2026-07-31):")
    return DATE

async def receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data_store[update.message.chat_id]['date'] = update.message.text.strip()
    await update.message.reply_text("🚉 Boarding Station Code दर्ज करें (उदा. SRID, MML, NZM):")
    return STATION

async def receive_station(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_data_store[chat_id]['station'] = update.message.text.strip().upper()
    await update.message.reply_text("🚃 किस Coach की खाली सीटें देखना चाहते हैं?\nउदा. S6, B2, A1 या ALL दर्ज करें:")
    return COACH_INPUT

def parse_coach_json(json_data):
    berth_type_map = {'L': 'Lower', 'M': 'Middle', 'U': 'Upper', 'R': 'Side Lower', 'P': 'Side Upper', 'SL': 'Side Lower', 'SU': 'Side Upper'}
    vacant_list = []

    if not json_data or 'bdd' not in json_data:
        return []

    for berth in json_data.get('bdd', []):
        berth_no = berth.get('berthNo')
        berth_code = berth_type_map.get(berth.get('berthCode'), berth.get('berthCode'))
        
        for seg in berth.get('bsd', []):
            if not seg.get('occupancy'):
                from_stn = seg.get('from')
                to_stn = seg.get('to')
                quota = seg.get('quota', 'GN')
                vacant_list.append(f"Berth {berth_no} ({berth_code}) | {from_stn} ➔ {to_stn} [{quota}]")

    return vacant_list

async def fetch_chart_api(train_no, date, station, coach_input):
    # http2=False forces HTTP/1.1 to bypass the HTTP/2 stream error
    async with httpx.AsyncClient(http2=False, verify=False, timeout=30.0) as client:
        try:
            # Directly hitting the API (No warm-up / no cookies!)
            comp_url = "https://www.irctc.co.in/online-charts/api/trainComposition"
            payload = {
                "trainNo": train_no,
                "jDate": date,
                "boardStn": station
            }
            
            resp = await client.post(comp_url, json=payload, headers=HEADERS)
            if resp.status_code != 200:
                return f"❌ IRCTC Error ({resp.status_code}): चार्ट अभी तैयार नहीं हुआ है या स्टेशन कोड गलत है।"

            comp_data = resp.json()
            coaches = comp_data.get("cdd", [])
            target_coach = coach_input.strip().upper()

            res = f"🚆 RESERVATION CHART STATUS 🚆\n"
            res += f"Train: {train_no} | Date: {date} | Boarding: {station}\n"
            res += "───────────────────────────\n\n"

            if coaches:
                res += "📊 Coaches Overview:\n"
                for c in coaches[:15]:
                    res += f"  • {c.get('coachName')} ({c.get('classCode')}): {c.get('vacantBerths')} Vacant\n"
                res += "\n"

            coaches_to_fetch = []
            if target_coach == "ALL":
                coaches_to_fetch = [c.get('coachName') for c in coaches if c.get('vacantBerths', 0) > 0][:3]
                if not coaches_to_fetch and coaches:
                    coaches_to_fetch = [coaches[0].get('coachName')]
            else:
                coaches_to_fetch = [target_coach]

            coach_url = "https://www.irctc.co.in/online-charts/api/coachComposition"
            
            for c_name in coaches_to_fetch:
                coach_payload = {
                    "trainNo": train_no,
                    "jDate": date,
                    "boardStn": station,
                    "coachName": c_name
                }

                coach_resp = await client.post(coach_url, json=coach_payload, headers=HEADERS)
                if coach_resp.status_code == 200:
                    coach_json = coach_resp.json()
                    vacant_seats = parse_coach_json(coach_json)

                    if vacant_seats:
                        res += f"💺 Vacant Seats in Coach {c_name}:\n"
                        for idx, seat in enumerate(vacant_seats[:30], 1):
                            res += f"{idx}. 📌 {seat}\n"
                        res += "\n"
                    else:
                        res += f"⚠️ Coach {c_name} में कोई खाली सीट नहीं मिली।\n\n"

            return res

        except httpx.ReadTimeout:
            return "❌ Timeout Error: IRCTC सर्वर धीमा चल रहा है। कृपया कुछ देर बाद प्रयास करें।"
        except Exception as e:
            return f"❌ Connection Error: {str(e)}"

async def receive_coach_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    coach_choice = update.message.text.strip()
    user_data_store[chat_id]['coach'] = coach_choice
    t_info = user_data_store[chat_id]

    status_msg = await update.message.reply_text("⚡ IRCTC API से डेटा निकाला जा रहा है...")

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
    print("🚀 Bot Active - HTTP/1.1 No-Cookie API Bypass!")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
