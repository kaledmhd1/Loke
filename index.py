from flask import Flask, request, jsonify
import json
import requests
import os
import logging
import asyncio
import aiohttp
from collections import defaultdict
from datetime import datetime
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import traceback

# إعداد اللوج
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)

# ملفات
ACCS_FILE = "accs.txt"
TOKENS = {}
KEY_LIMIT = 150
token_tracker = defaultdict(lambda: [0, 0])

# إعداد requests مع retry
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"]
)
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)


# =================== تحميل الحسابات ===================
def load_accounts():
    if not os.path.exists(ACCS_FILE):
        logging.error(f"{ACCS_FILE} not found!")
        return {}
    try:
        with open(ACCS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logging.info(f"Loaded {len(data)} accounts")
        return data
    except Exception as e:
        logging.error(f"Failed to load accounts: {e}")
        return {}


# =================== جلب JWT ===================
def get_jwt(uid, password):
    api_url = f"https://jwt-gen-api-v2.onrender.com/token?uid={uid}&password={password}"
    try:
        r = session.get(api_url, verify=False, timeout=15)
        if r.status_code == 200 and "token" in r.json():
            token = r.json()["token"]
            logging.info(f"[OK] JWT for {uid}")
            return token
        else:
            logging.error(f"[ERROR] {r.status_code} for {uid}: {r.text}")
    except Exception as e:
        logging.error(f"[Exception] {e}")
    return None


def refresh_tokens():
    accounts = load_accounts()
    new_tokens = {}
    for uid, pw in accounts.items():
        token = get_jwt(uid, pw)
        if token:
            new_tokens[uid] = token
    global TOKENS
    TOKENS = new_tokens
    logging.info(f"Tokens refreshed: {len(TOKENS)} active")


# =================== أدوات ===================
def get_today_midnight_timestamp():
    now = datetime.now()
    midnight = datetime(now.year, now.month, now.day)
    return midnight.timestamp()


def make_request(uid, server_name):
    url = f"https://razor-info.vercel.app/player-info?uid={uid}&region={server_name.lower()}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return {"error": f"Server returned {r.status_code}", "raw": r.text}
        return r.json()
    except Exception as e:
        return {"error": str(e)}


async def send_request(uid, token, url):
    headers = {
        'User-Agent': "Dalvik/2.1.0",
        'Authorization': f"Bearer {token}"
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data={"uid": uid}, headers=headers) as resp:
            return resp.status


async def send_multiple_requests(uid, token, url):
    tasks = [send_request(uid, token, url) for _ in range(100)]
    return await asyncio.gather(*tasks)


# =================== الراوت ===================
@app.route("/")
def home():
    return jsonify({"status": "live", "tokens_loaded": len(TOKENS)})


@app.route("/tokens")
def show_tokens():
    return jsonify(TOKENS if TOKENS else {"error": "No tokens"})


@app.route("/like")
def handle_like():
    try:
        uid = request.args.get("uid")
        server_name = request.args.get("server_name", "").upper()
        key = request.args.get("key")

        if key != "jenil":
            return jsonify({"error": "Invalid API key"}), 403
        if not uid or not server_name:
            return jsonify({"error": "uid and server_name required"}), 400

        if not TOKENS:
            return jsonify({"error": "No tokens available"}), 503

        token = list(TOKENS.values())[0]
        before = make_request(uid, server_name)
        before_like = int(before.get('basicInfo', {}).get('liked', 0))
        name = before.get('basicInfo', {}).get('nickname', 'Unknown')

        url = "https://clientbp.ggblueshark.com/LikeProfile"
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_multiple_requests(uid, token, url))
        loop.close()

        after = make_request(uid, server_name)
        after_like = int(after.get('basicInfo', {}).get('liked', 0))
        like_given = after_like - before_like

        return jsonify({
            "LikesGivenByAPI": like_given,
            "LikesafterCommand": after_like,
            "LikesbeforeCommand": before_like,
            "PlayerNickname": name,
            "UID": uid
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# جلب التوكنات عند التشغيل
refresh_tokens()

# لا نضع app.run() لأن Vercel سيستدعيه كـ WSGI