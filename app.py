import asyncio
import time
import httpx
import json
import os
import datetime
import threading
from collections import defaultdict
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from cachetools import TTLCache
from typing import Tuple
from google.protobuf import json_format, message
from google.protobuf.message import Message
from Crypto.Cipher import AES
from werkzeug.exceptions import HTTPException

# Image Generation Imports
from PIL import Image
from io import BytesIO
import requests
from concurrent.futures import ThreadPoolExecutor

# === Local Imports ===
from config import Config
from Pb2 import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2

# === Flask App Setup ===
app = Flask(__name__)
CORS(app)

app.json.sort_keys = False 

cache = TTLCache(maxsize=100, ttl=300)
cached_tokens = defaultdict(dict)

# Image Generation Setup
executor = ThreadPoolExecutor(max_workers=15)
session = requests.Session()

IMAGE_SLOT_CONFIG = [
    {"box": 1, "x": 350, "y": 30,  "w": 150, "h": 150, "name": "Hair",           "type": "clothes", "index": 3, "fallback": "211000000"},
    {"box": 2, "x": 135, "y": 130, "w": 150, "h": 150, "name": "Mask",           "type": "clothes", "index": 4, "fallback": "214000000"},
    {"box": 3, "x": 575, "y": 130, "w": 150, "h": 150, "name": "FacePaint",      "type": "clothes", "index": 5, "fallback": "208000000"},
    {"box": 4, "x": 665, "y": 350, "w": 150, "h": 150, "name": "LobbyAnimation", "type": "weapon",  "index": 1, "fallback": "900000015"},
    {"box": 5, "x": 575, "y": 550, "w": 150, "h": 150, "name": "WeaponSkin",     "type": "weapon",  "index": 0, "fallback": "0"},
    {"box": 6, "x": 350, "y": 654, "w": 150, "h": 150, "name": "Shoes",          "type": "clothes", "index": 1, "fallback": "205000000"},
    {"box": 7, "x": 135, "y": 570, "w": 150, "h": 150, "name": "Pants",          "type": "clothes", "index": 2, "fallback": "204000000"},
    {"box": 8, "x": 45,  "y": 350, "w": 150, "h": 150, "name": "Shirt",          "type": "clothes", "index": 0, "fallback": "203000000"}
]

# === Helper Functions ===
def pad(text: bytes) -> bytes:
    padding_length = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([padding_length] * padding_length)

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    return aes.encrypt(pad(plaintext))

def decode_protobuf(encoded_data: bytes, message_type: message.Message) -> message.Message:
    instance = message_type()
    instance.ParseFromString(encoded_data)
    return instance

async def json_to_proto(json_data: str, proto_message: Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def format_timestamp(ts):
    try:
        if not ts or str(ts) == "0": return "N/A"
        return datetime.datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %I:%M:%S %p')
    except:
        return str(ts)

def get_detailed_time_diff(timestamp):
    """বছর, মাস, সপ্তাহ, দিন, ঘণ্টা, মিনিট, সেকেন্ড ক্যালকুলেট করার জেনেরিক ফাংশন"""
    if not timestamp or str(timestamp) == "0":
        return "N/A"
    try:
        ts = int(timestamp)
        now = int(time.time())
        diff = now - ts
        
        if diff < 0:
            return "Just now"

        years = diff // 31536000
        diff %= 31536000

        months = diff // 2592000
        diff %= 2592000

        weeks = diff // 604800
        diff %= 604800

        days = diff // 86400
        diff %= 86400

        hours = diff // 3600
        diff %= 3600

        minutes = diff // 60
        seconds = diff % 60

        parts = []
        if years > 0: parts.append(f"{years} Years")
        if months > 0: parts.append(f"{months} Months")
        if weeks > 0: parts.append(f"{weeks} Weeks")
        if days > 0: parts.append(f"{days} Days")
        if hours > 0: parts.append(f"{hours} Hours")
        if minutes > 0: parts.append(f"{minutes} Minutes")
        if seconds > 0 or not parts: parts.append(f"{seconds} Seconds")

        return " ".join(parts)
    except Exception:
        return "N/A"

# === Image Generator Logic ===
def generate_outfit_image(data):
    if not data or not isinstance(data, dict):
        raise Exception("Invalid player data structure.")

    basic_info = data.get("basicInfo") or {}
    profile_info = data.get("profileInfo") or {}

    clothes_list = profile_info.get("clothes", [])
    weapons_list = basic_info.get("weaponSkinShows", [])
    
    if not clothes_list: clothes_list = []
    if not weapons_list: weapons_list = []

    def fetch_image_for_slot(slot):
        item_id = 0
        try:
            if slot["type"] == "clothes" and len(clothes_list) > slot["index"]:
                item_id = clothes_list[slot["index"]]
            elif slot["type"] == "weapon" and len(weapons_list) > slot["index"]:
                item_id = weapons_list[slot["index"]]
        except Exception:
            item_id = 0

        if not item_id or str(item_id) == "0":
            item_id = slot["fallback"]

        if not item_id or str(item_id) == "0":
            return None 

        image_url = f'https://iconapi.wasmer.app/{item_id}'
        try:
            resp = session.get(image_url, timeout=7)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content)).convert("RGBA")
            return img
        except Exception:
            return None

    futures = []
    for slot in IMAGE_SLOT_CONFIG:
        futures.append((slot, executor.submit(fetch_image_for_slot, slot)))

    bg_path = os.path.join(os.path.dirname(__file__), "outfit.png")
    try:
        canvas = Image.open(bg_path).convert("RGBA")
    except FileNotFoundError:
        raise Exception("Error: 'outfit.png' not found. Please place the image in the root directory.")

    for slot, future in futures:
        item_img = future.result()
        if item_img:
            item_img = item_img.resize((slot["w"], slot["h"]), Image.LANCZOS)
            canvas.paste(item_img, (slot["x"], slot["y"]), item_img)

    output = BytesIO()
    canvas.save(output, format='PNG')
    output.seek(0)
    return output

# === APIs and Data Fetching ===
async def check_ban_status_garena(uid):
    ban_url = f'https://ff.garena.com/api/antihack/check_banned?lang=en&uid={uid}'
    ban_headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'authority': 'ff.garena.com',
        'referer': 'https://ff.garena.com/en/support/',
        'x-requested-with': 'B6FksShzIgjfrYImLpTsadjS86sddhFH',
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(ban_url, headers=ban_headers)
            data = resp.json()
            if data.get("status") == "success" and "data" in data:
                return data["data"].get("is_banned", 0)
    except Exception:
        pass
    return 0

async def get_access_token(account: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = account + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {
        'User-Agent': Config.USER_AGENT, 
        'Connection': "Keep-Alive", 
        'Accept-Encoding': "gzip", 
        'Content-Type': "application/x-www-form-urlencoded"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=payload, headers=headers)
        data = resp.json()
        return data.get("access_token", "0"), data.get("open_id", "0")

async def create_jwt(region: str):
    account = Config.get_account(region)
    token_val, open_id = await get_access_token(account)
    body = json.dumps({"open_id": open_id, "open_id_type": "4", "login_token": token_val, "orign_platform_type": "4"})
    proto_bytes = await json_to_proto(body, FreeFire_pb2.LoginReq())
    payload = aes_cbc_encrypt(Config.MAIN_KEY, Config.MAIN_IV, proto_bytes)
    url = "https://loginbp.ggblueshark.com/MajorLogin"
    headers = {
        'User-Agent': Config.USER_AGENT, 
        'Connection': "Keep-Alive", 
        'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream", 
        'Expect': "100-continue",
        'X-Unity-Version': Config.UNITY_VERSION, 
        'X-GA': "v1 1", 
        'ReleaseVersion': Config.RELEASE_VERSION
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=payload, headers=headers)
        msg = json.loads(json_format.MessageToJson(decode_protobuf(resp.content, FreeFire_pb2.LoginRes)))
        cached_tokens[region] = {
            'token': f"Bearer {msg.get('token','0')}",
            'region': msg.get('lockRegion','0'),
            'server_url': msg.get('serverUrl','0'),
            'expires_at': time.time() + 25200
        }

async def initialize_tokens():
    tasks = [create_jwt(r) for r in Config.SUPPORTED_REGIONS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r, res in zip(Config.SUPPORTED_REGIONS, results):
        if isinstance(res, Exception):
            print(f"Warning: Failed to load token for region {r}: {res}")

def bg_token_refresh():
    while True:
        time.sleep(25200)
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(initialize_tokens())
            loop.close()
        except Exception as e:
            print(f"Token refresh error: {e}")

async def get_token_info(region: str) -> Tuple[str, str, str]:
    info = cached_tokens.get(region)
    if info and time.time() < info['expires_at']:
        return info['token'], info['region'], info['server_url']
    await create_jwt(region)
    info = cached_tokens[region]
    return info['token'], info['region'], info['server_url']

async def GetAccountInformation(uid, unk, region, endpoint):
    payload = await json_to_proto(json.dumps({'a': uid, 'b': unk}), main_pb2.GetPlayerPersonalShow())
    data_enc = aes_cbc_encrypt(Config.MAIN_KEY, Config.MAIN_IV, payload)
    token, lock, server = await get_token_info(region)
    headers = {
        'User-Agent': Config.USER_AGENT, 
        'Connection': "Keep-Alive", 
        'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream", 
        'Expect': "100-continue",
        'Authorization': token, 
        'X-Unity-Version': Config.UNITY_VERSION, 
        'X-GA': "v1 1",
        'ReleaseVersion': Config.RELEASE_VERSION
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(server + endpoint, data=data_enc, headers=headers)
        return json.loads(json_format.MessageToJson(decode_protobuf(resp.content, AccountPersonalShow_pb2.AccountPersonalShowInfo)))

def format_response(data, is_banned):
    basic_info = data.get("basicInfo", {})
    profile_info = data.get("profileInfo", {})
    clan_info = data.get("clanBasicInfo", {})
    captain_info = data.get("captainBasicInfo", {})
    pet_info = data.get("petInfo", {})
    social_info = data.get("socialInfo", {})
    
    create_ts = basic_info.get("createAt", "0")
    login_ts = basic_info.get("lastLoginAt", "0")

    return {
        "PlayerInfo": {
            "AccountName": basic_info.get("nickname", "N/A"),
            "AccountId": social_info.get("accountId", "N/A"),
            "AccountLevel": basic_info.get("level", 0),
            "AccountLikes": basic_info.get("liked", 0),
            "AccountEXP": basic_info.get("exp", 0),
            "AccountRegion": basic_info.get("region", "N/A"),
            "Gender": str(social_info.get("gender", "N/A")).replace("Gender_", ""),
            "Language": str(social_info.get("language", "N/A")).replace("Language_", ""),
            "AccountAvatarId": basic_info.get("headPic", 0),
            "AccountBannerId": basic_info.get("bannerId", 0),
            "AccountCreateDate": format_timestamp(create_ts),
            "AccountLastLoginDate": format_timestamp(login_ts),
            "Signature": social_info.get("signature", "N/A")
        },
        "BanCheckInfo": {
            "Ban_Status": "Account Banned ⛔" if is_banned else "Not Banned ✅",
            "BanDuration": get_detailed_time_diff(login_ts) if is_banned else "N/A",
            "AccountAge": get_detailed_time_diff(create_ts)
        },
        "PlayerRankInfo": {
            "BrRankPoint": basic_info.get("rankingPoints", 0),
            "BrMaxRank": basic_info.get("maxRank", 0),
            "CsRankPoint": basic_info.get("csRankingPoints", 0),
            "CsMaxRank": basic_info.get("csMaxRank", 0)
        },
        "PetInfo": {
            "PetId": pet_info.get("id", 0),
            "PetLevel": pet_info.get("level", 0),
            "PetExp": pet_info.get("exp", 0),
            "SelectedSkillId": pet_info.get("selectedSkillId", 0)
        },
        "GuildInfo": {
            "GuildName": clan_info.get("clanName", "N/A"),
            "GuildID": str(clan_info.get("clanId", "N/A")),
            "GuildLevel": clan_info.get("clanLevel", 0),
            "GuildMember": clan_info.get("memberNum", 0),
            "GuildCapacity": clan_info.get("capacity", 0),
            "GuildOwner": str(clan_info.get("captainId", "N/A"))
        },
        "GuildLeaderInfo": {
            "LeaderName": captain_info.get("nickname", "N/A"),
            "LeaderId": captain_info.get("accountId", "N/A"),
            "LeaderLevel": captain_info.get("level", 0),
            "LeaderLikes": captain_info.get("liked", 0),
            "LeaderAvatarId": captain_info.get("headPic", 0),
            "LeaderBrRankPoint": captain_info.get("rankingPoints", 0),
            "LeaderCsRankPoint": captain_info.get("csRankingPoints", 0),
            "LeaderCreateDate": format_timestamp(captain_info.get("createAt")),
            "LeaderLastLoginDate": format_timestamp(captain_info.get("lastLoginAt"))
        }
    }

# === Global Error Handler ===
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    import traceback
    traceback.print_exc()
    return jsonify({"error": f"Server Error: {str(e)}"}), 500

# === API Routes ===
@app.route('/')
def serve_index():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        index_path = os.path.join(base_dir, 'index.html')
        
        if not os.path.exists(index_path):
            return f"<h1>Error: index.html not found!</h1>", 404
            
        return send_file(index_path)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get')
def get_account_info():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "Please provide UID."}), 400
    
    try:
        region = "BD" 
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # প্লেয়ার ইনফো এবং ব্যান স্ট্যাটাস একসাথেই ফেচ করা হচ্ছে
        player_info_task = GetAccountInformation(uid, "7", region, "/GetPlayerPersonalShow")
        ban_status_task = check_ban_status_garena(uid)
        
        return_data, is_banned = loop.run_until_complete(asyncio.gather(player_info_task, ban_status_task))
        loop.close()
        
        formatted = format_response(return_data, is_banned)
        return jsonify(formatted), 200
    
    except Exception as e:
        print(f"Error for UID {uid}: {e}")
        return jsonify({"error": f"Data fetch failed: {str(e)}"}), 500

@app.route('/outfit_image')
def serve_outfit_image():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "Please provide UID."}), 400
    
    try:
        region = "BD"
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        raw_data = loop.run_until_complete(GetAccountInformation(uid, "7", region, "/GetPlayerPersonalShow"))
        loop.close()
        
        if not raw_data or not raw_data.get("basicInfo"):
            return jsonify({"error": "Player not found or invalid UID."}), 404
            
        image_io = generate_outfit_image(raw_data)
        return send_file(image_io, mimetype='image/png')
        
    except Exception as e:
        return jsonify({"error": f"Image generation failed: {str(e)}"}), 500

@app.route('/refresh', methods=['GET', 'POST'])
def refresh_tokens_endpoint():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(initialize_tokens())
        loop.close()
        return jsonify({'message': 'Tokens refreshed for all regions.'}), 200
    except Exception as e:
        return jsonify({'error': f'Refresh failed: {e}'}), 500

# === Startup ===
def start_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(initialize_tokens())
    loop.close()
    
    threading.Thread(target=bg_token_refresh, daemon=True).start()

if __name__ == '__main__':
    start_server()
    app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG)
