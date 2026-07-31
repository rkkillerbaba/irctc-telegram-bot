import os
import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

# --- Request Model ---
class ChartRequest(BaseModel):
    train_no: str
    date: str
    station: str
    coach: str

# --- IRCTC Headers (Bypass) ---
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

# --- Core Scraping Logic ---
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
    async with httpx.AsyncClient(http2=False, verify=False, timeout=30.0) as client:
        try:
            comp_url = "https://www.irctc.co.in/online-charts/api/trainComposition"
            payload = {"trainNo": train_no, "jDate": date, "boardStn": station}
            
            resp = await client.post(comp_url, json=payload, headers=HEADERS)
            if resp.status_code != 200:
                return f"❌ IRCTC Error ({resp.status_code}): चार्ट अभी तैयार नहीं हुआ है या स्टेशन कोड गलत है।"

            comp_data = resp.json()
            coaches = comp_data.get("cdd", [])
            target_coach = coach_input.strip().upper()

            res = f"<h3>🚆 Train: {train_no} | Date: {date} | Boarding: {station}</h3><hr>"

            if coaches:
                res += "<b>📊 Coaches Overview:</b><ul>"
                for c in coaches[:15]:
                    res += f"<li>{c.get('coachName')} ({c.get('classCode')}): {c.get('vacantBerths')} Vacant</li>"
                res += "</ul><br>"

            coaches_to_fetch = []
            if target_coach == "ALL":
                coaches_to_fetch = [c.get('coachName') for c in coaches if c.get('vacantBerths', 0) > 0][:3]
                if not coaches_to_fetch and coaches:
                    coaches_to_fetch = [coaches[0].get('coachName')]
            else:
                coaches_to_fetch = [target_coach]

            coach_url = "https://www.irctc.co.in/online-charts/api/coachComposition"
            
            for c_name in coaches_to_fetch:
                coach_payload = {"trainNo": train_no, "jDate": date, "boardStn": station, "coachName": c_name}
                coach_resp = await client.post(coach_url, json=coach_payload, headers=HEADERS)
                
                if coach_resp.status_code == 200:
                    coach_json = coach_resp.json()
                    vacant_seats = parse_coach_json(coach_json)

                    if vacant_seats:
                        res += f"<b>💺 Vacant Seats in Coach {c_name}:</b><ul>"
                        for seat in vacant_seats[:30]:
                            res += f"<li>📌 {seat}</li>"
                        res += "</ul><br>"
                    else:
                        res += f"⚠️ Coach {c_name} में कोई खाली सीट नहीं मिली।<br><br>"

            return res

        except Exception as e:
            return f"❌ Connection Error: {str(e)}"

# --- Web Endpoints ---
@app.post("/api/get_chart")
async def get_chart(req: ChartRequest):
    result = await fetch_chart_api(req.train_no, req.date, req.station, req.coach)
    return {"html_result": result}

@app.get("/", response_class=HTMLResponse)
async def serve_gui():
    # Modern HTML + CSS (Tailwind) + JS GUI
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>IRCTC Fast Chart API</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background-color: #f3f4f6; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            .loader { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; display: none; margin: auto;}
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        </style>
    </head>
    <body class="flex items-center justify-center min-h-screen p-4">
        <div class="bg-white p-8 rounded-xl shadow-lg w-full max-w-md">
            <h1 class="text-2xl font-bold text-center text-blue-600 mb-6">🚆 IRCTC Chart Finder</h1>
            
            <form id="chartForm" class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700">Train Number</label>
                    <input type="text" id="train_no" placeholder="e.g. 12191" required class="mt-1 w-full px-4 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">Journey Date</label>
                    <input type="date" id="date" required class="mt-1 w-full px-4 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">Boarding Station</label>
                    <input type="text" id="station" placeholder="e.g. SRID, MML" required class="mt-1 w-full px-4 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">Coach (or ALL)</label>
                    <input type="text" id="coach" placeholder="e.g. S6, B2, ALL" required class="mt-1 w-full px-4 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500">
                </div>
                
                <button type="submit" class="w-full bg-blue-600 text-white font-bold py-2 px-4 rounded hover:bg-blue-700 transition">
                    Check Chart
                </button>
            </form>

            <div id="loader" class="loader mt-6"></div>
            
            <div id="resultBox" class="mt-6 p-4 bg-gray-50 border rounded-md hidden max-h-96 overflow-y-auto text-sm text-gray-800">
                <!-- Results will appear here -->
            </div>
        </div>

        <script>
            document.getElementById('chartForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                
                const resultBox = document.getElementById('resultBox');
                const loader = document.getElementById('loader');
                
                // Show loader, hide old result
                loader.style.display = 'block';
                resultBox.classList.add('hidden');
                
                const payload = {
                    train_no: document.getElementById('train_no').value,
                    date: document.getElementById('date').value,
                    station: document.getElementById('station').value.toUpperCase(),
                    coach: document.getElementById('coach').value.toUpperCase()
                };

                try {
                    const response = await fetch('/api/get_chart', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    
                    const data = await response.json();
                    resultBox.innerHTML = data.html_result;
                } catch (error) {
                    resultBox.innerHTML = '<span class="text-red-500">❌ Error connecting to server!</span>';
                } finally {
                    loader.style.display = 'none';
                    resultBox.classList.remove('hidden');
                }
            });
        </script>
    </body>
    </html>
    """
    return html_content

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
