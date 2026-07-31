import os
import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

# --- Request Models ---
class ChartRequest(BaseModel):
    train_no: str
    date: str
    station: str
    coach: str

# --- Headers for Chart API (Browser Impersonation) ---
CHART_HEADERS = {
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

# --- Headers for Train Info API (Mobile App Impersonation Bypass) ---
INFO_HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SQ3A.220705.004)",
    "Accept-Encoding": "gzip",
    "Connection": "Keep-Alive"
}

# --- 1. Train Schedule API (Auto-fetch) ---
@app.get("/api/train_info/{train_no}")
async def get_train_info(train_no: str):
    url = f"https://www.irctc.co.in/eticketing/protected/mapps1/trnscheduleenquiry/{train_no}"
    async with httpx.AsyncClient(http2=False, verify=False, timeout=10.0) as client:
        try:
            resp = await client.get(url, headers=INFO_HEADERS)
            if resp.status_code == 200:
                data = resp.json()
                if "trainName" in data:
                    return {
                        "trainName": data.get("trainName", "Unknown Train"),
                        "stations": [{"code": stn["stationCode"], "name": stn["stationName"]} for stn in data.get("stationList", [])]
                    }
            return {"error": "Train not found or API blocked"}
        except Exception as e:
            return {"error": str(e)}

# --- 2. Parse Coach JSON ---
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
                vacant_list.append(f"Seat {berth_no} ({berth_code}) | {from_stn} ➔ {to_stn} [{quota}]")

    return vacant_list

# --- 3. Chart API Core Logic ---
async def fetch_chart_api(train_no, date, station, coach_input):
    async with httpx.AsyncClient(http2=False, verify=False, timeout=30.0) as client:
        try:
            comp_url = "https://www.irctc.co.in/online-charts/api/trainComposition"
            payload = {"trainNo": train_no, "jDate": date, "boardStn": station}
            
            resp = await client.post(comp_url, json=payload, headers=CHART_HEADERS)
            if resp.status_code != 200:
                return f"<div class='text-red-500 font-bold'>❌ IRCTC Error ({resp.status_code}): चार्ट अभी तैयार नहीं हुआ है या स्टेशन कोड गलत है।</div>"

            comp_data = resp.json()
            coaches = comp_data.get("cdd", [])
            chart_time = comp_data.get("chartOneDate", "N/A")
            target_coach = coach_input.strip().upper()

            res = f"<h3 class='text-lg font-bold text-gray-800 border-b pb-2 mb-3'>🚆 {train_no} | {date} | {station}</h3>"
            res += f"<p class='text-xs text-gray-500 mb-4'>🕒 Chart Prepared: {chart_time}</p>"

            if coaches:
                res += "<div class='mb-4'><b class='text-sm text-gray-700'>📊 Coaches Summary:</b><div class='flex flex-wrap gap-2 mt-2'>"
                for c in coaches[:20]:
                    bg = "bg-green-100 text-green-800" if c.get('vacantBerths', 0) > 0 else "bg-red-50 text-red-400"
                    res += f"<span class='text-xs font-semibold px-2 py-1 rounded {bg}'>{c.get('coachName')} : {c.get('vacantBerths')}</span>"
                res += "</div></div>"

            coaches_to_fetch = []
            if target_coach == "ALL":
                coaches_to_fetch = [c.get('coachName') for c in coaches if c.get('vacantBerths', 0) > 0][:5]
                if not coaches_to_fetch and coaches:
                    coaches_to_fetch = [coaches[0].get('coachName')]
            else:
                coaches_to_fetch = [target_coach]

            coach_url = "https://www.irctc.co.in/online-charts/api/coachComposition"
            for c_name in coaches_to_fetch:
                coach_payload = {"trainNo": train_no, "jDate": date, "boardStn": station, "coachName": c_name}
                coach_resp = await client.post(coach_url, json=coach_payload, headers=CHART_HEADERS)
                
                if coach_resp.status_code == 200:
                    coach_json = coach_resp.json()
                    vacant_seats = parse_coach_json(coach_json)

                    if vacant_seats:
                        res += f"<div class='bg-blue-50 border-l-4 border-blue-500 p-3 mb-3'><b class='text-blue-700'>💺 Coach {c_name} (Vacant Seats):</b><ul class='mt-2 space-y-1 text-sm'>"
                        for seat in vacant_seats[:50]:
                            res += f"<li class='flex items-center'>📌 <span class='ml-2 font-medium'>{seat}</span></li>"
                        res += "</ul></div>"
                    else:
                        res += f"<div class='bg-red-50 text-red-600 p-2 rounded mb-2 text-sm'>⚠️ Coach {c_name} में कोई खाली सीट नहीं मिली।</div>"

            return res

        except Exception as e:
            return f"<div class='text-red-500'>❌ API Connection Error: {str(e)}</div>"

@app.post("/api/get_chart")
async def get_chart(req: ChartRequest):
    result = await fetch_chart_api(req.train_no, req.date, req.station, req.coach)
    return {"html_result": result}

# --- 4. Main GUI (Frontend) ---
@app.get("/", response_class=HTMLResponse)
async def serve_gui():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Reservation Chart Pro</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; }
            .loader { border: 3px solid #f3f3f3; border-top: 3px solid #2563eb; border-radius: 50%; width: 24px; height: 24px; animation: spin 1s linear infinite; display: none; margin: auto;}
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        </style>
    </head>
    <body class="p-4 md:p-8 flex justify-center min-h-screen">
        <div class="bg-white rounded-xl shadow-xl border w-full max-w-lg overflow-hidden flex flex-col h-fit">
            
            <div class="bg-blue-600 p-5 text-center text-white">
                <h1 class="text-2xl font-extrabold tracking-wide">Reservation Chart</h1>
                <p class="text-blue-100 text-sm mt-1">Advanced Auto-Fetch System</p>
            </div>

            <div class="p-6 space-y-5">
                <!-- Train Input -->
                <div class="relative">
                    <label class="block text-sm font-semibold text-gray-700 mb-1">Train Number / Name</label>
                    <input type="text" id="train_no" placeholder="e.g. 22188" maxlength="5" class="w-full px-4 py-2 bg-gray-50 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none transition">
                    
                    <div id="autocomplete_box" class="hidden absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-md shadow-lg overflow-hidden">
                        <div id="train_suggestion" class="px-4 py-3 cursor-pointer hover:bg-blue-50 font-medium text-gray-800 flex flex-col gap-1">
                            <span id="suggestion_text" class="flex items-center gap-2"></span>
                        </div>
                    </div>
                </div>

                <!-- Date -->
                <div>
                    <label class="block text-sm font-semibold text-gray-700 mb-1">Journey Date</label>
                    <input type="date" id="date" class="w-full px-4 py-2 bg-gray-50 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
                </div>

                <!-- Fallback Combo Box (Datalist) -->
                <div>
                    <label class="block text-sm font-semibold text-gray-700 mb-1">Boarding Station</label>
                    <input type="text" id="station" list="station_list" placeholder="e.g. MML, JBP" required class="w-full px-4 py-2 bg-gray-50 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 uppercase focus:outline-none transition">
                    <datalist id="station_list"></datalist>
                </div>

                <!-- Coach Input -->
                <div>
                    <label class="block text-sm font-semibold text-gray-700 mb-1">Coach (e.g. B2, S6 or ALL)</label>
                    <input type="text" id="coach" placeholder="ALL" value="ALL" class="w-full px-4 py-2 bg-gray-50 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 uppercase focus:outline-none">
                </div>
                
                <button id="submitBtn" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-4 rounded-lg shadow transition flex justify-center items-center gap-2">
                    <span>Check Chart 🔍</span>
                    <div id="mainLoader" class="loader !border-white !border-t-blue-600"></div>
                </button>
            </div>

            <!-- Result Area -->
            <div id="resultBox" class="hidden bg-gray-50 border-t p-6 max-h-[400px] overflow-y-auto"></div>
        </div>

        <script>
            document.getElementById('date').valueAsDate = new Date();

            const trainInput = document.getElementById('train_no');
            const stationInput = document.getElementById('station');
            const stationList = document.getElementById('station_list');
            const autocompleteBox = document.getElementById('autocomplete_box');
            const suggestionText = document.getElementById('suggestion_text');
            
            trainInput.addEventListener('input', async (e) => {
                const trNo = e.target.value.trim();
                
                if(trNo.length < 5) {
                    autocompleteBox.classList.add('hidden');
                    return;
                }

                if(trNo.length === 5) {
                    suggestionText.innerHTML = "<span>⏳</span> Searching details...";
                    autocompleteBox.classList.remove('hidden');
                    
                    try {
                        const res = await fetch(`/api/train_info/${trNo}`);
                        const data = await res.json();
                        
                        if(data.trainName) {
                            suggestionText.innerHTML = `<span>🚆</span> <span>${trNo} - ${data.trainName}</span>`;
                            
                            // Populate Combo Box
                            stationList.innerHTML = '';
                            data.stations.forEach(stn => {
                                const opt = document.createElement('option');
                                opt.value = stn.code;
                                opt.innerText = stn.name;
                                stationList.appendChild(opt);
                            });
                            stationInput.placeholder = "Select or type (e.g. " + data.stations[0].code + ")";
                        } else {
                            // If IRCTC blocked it, don't lock the UI!
                            suggestionText.innerHTML = "<span>⚠️</span> <span class='text-sm text-red-600'>Auto-fetch blocked by IRCTC. Please type station code manually below.</span>";
                            stationInput.placeholder = "e.g. MML, JBP";
                        }
                    } catch(err) {
                        suggestionText.innerHTML = "<span>⚠️</span> <span class='text-sm text-red-600'>Network Error. Please type station code manually below.</span>";
                    }
                }
            });

            document.getElementById('train_suggestion').addEventListener('click', () => {
                autocompleteBox.classList.add('hidden');
            });

            document.getElementById('submitBtn').addEventListener('click', async () => {
                const trNo = trainInput.value;
                const dt = document.getElementById('date').value;
                const stn = stationInput.value;
                let ch = document.getElementById('coach').value || "ALL";

                if(!trNo || !dt || !stn) {
                    alert("Please fill Train No, Date and Station!");
                    return;
                }

                const resultBox = document.getElementById('resultBox');
                const loader = document.getElementById('mainLoader');
                
                loader.style.display = 'block';
                resultBox.classList.add('hidden');
                
                try {
                    const response = await fetch('/api/get_chart', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ train_no: trNo, date: dt, station: stn, coach: ch })
                    });
                    
                    const data = await response.json();
                    resultBox.innerHTML = data.html_result;
                } catch (error) {
                    resultBox.innerHTML = '<span class="text-red-500 font-bold">❌ Error connecting to backend!</span>';
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
