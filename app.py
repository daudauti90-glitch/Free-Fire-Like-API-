from flask import Flask, request, jsonify, render_template_string
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
import binascii
import aiohttp
import requests
import json
import os
import like_pb2
import like_count_pb2
import uid_generator_pb2
from google.protobuf.message import DecodeError
import base64

app = Flask(__name__)

WEB_UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Free Fire Like Tool</title>
    <style>
        body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; }
        .container { max-width: 560px; margin: 64px auto; padding: 24px; background: #111827; border-radius: 14px; }
        h1 { margin-top: 0; font-size: 1.5rem; }
        p { color: #94a3b8; }
        label { display: block; margin-top: 12px; margin-bottom: 6px; }
        input { width: 100%; padding: 10px; border: 1px solid #334155; border-radius: 10px; background: #0b1220; color: #e2e8f0; }
        button { margin-top: 16px; width: 100%; padding: 12px; background: #2563eb; color: #fff; border: 0; border-radius: 10px; cursor: pointer; }
        button:hover { background: #1d4ed8; }
        #result { margin-top: 16px; white-space: pre-wrap; background: #0b1220; padding: 12px; border-radius: 10px; min-height: 48px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Free Fire Like API Web Panel</h1>
        <p>UID dalo, optional server choose karo, aur direct result pao.</p>
        <label for="uid">UID</label>
        <input id="uid" type="text" placeholder="e.g. 123456789" />
        <label for="server_name">Server (optional)</label>
        <input id="server_name" type="text" placeholder="e.g. IND, BD, BR" />
        <label for="repeat_count">Auto Repeat Count</label>
        <input id="repeat_count" type="number" min="1" max="20" value="3" />
        <button onclick="submitLike()">Start Auto Like</button>
        <div id="result">Result yahan dikhega...</div>
    </div>

    <script>
        async function submitLike() {
            const uid = document.getElementById('uid').value.trim();
            const server = document.getElementById('server_name').value.trim();
            const resultBox = document.getElementById('result');

            if (!uid) {
                resultBox.textContent = 'UID required hai.';
                return;
            }

            try {
                const repeatCount = parseInt(document.getElementById('repeat_count').value || '1', 10);
                let url = `/auto-like?uid=${encodeURIComponent(uid)}&count=${encodeURIComponent(repeatCount)}`;
                if (server) {
                    url += `&server_name=${encodeURIComponent(server)}`;
                }

                resultBox.textContent = 'Processing...';
                const res = await fetch(url);
                const data = await res.json();
                resultBox.textContent = JSON.stringify(data, null, 2);
            } catch (err) {
                resultBox.textContent = `Request failed: ${err}`;
            }
        }
    </script>
</body>
</html>
"""

def load_tokens():
    try:
        with open("tokens.json", "r") as f:
            tokens = json.load(f)
        return tokens
    except Exception as e:
        app.logger.error(f"Error loading tokens: {e}")
        return None

def send_telegram_message(message_text):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        app.logger.info("Telegram config missing, skipping Telegram notification.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_text
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            app.logger.error(f"Telegram send failed: {response.status_code} - {response.text}")
            return False
        return True
    except Exception as e:
        app.logger.error(f"Error while sending Telegram message: {e}")
        return False

def build_telegram_message(player_name, player_uid, region, target_likes, given_likes, before_like, after_like, used_calls):
    daily_limit = int(os.getenv("DAILY_API_LIMIT", "80"))
    total_days = int(os.getenv("PLAN_TOTAL_DAYS", "30"))
    days_remaining = int(os.getenv("PLAN_DAYS_REMAINING", "29"))
    remain_limit = max(daily_limit - used_calls, 0)
    failed_likes = max(target_likes - given_likes, 0)

    return (
        "🎮 FREE FIRE AUTO-LIKE SUCCESS\n"
        "────────────────────\n"
        f"🎯 Player: {player_name}\n"
        f"🆔 UID: {player_uid}\n"
        f"🌍 Region: {region}\n\n"
        f"🎯 Target Likes: {target_likes}\n"
        f"✅ Given by API: {given_likes}\n"
        f"📉 Failed to Send: {failed_likes}\n\n"
        f"📊 Before Like: {before_like}\n"
        f"📈 After Like: {after_like}\n\n"
        f"🔄 Remain API Limit: {remain_limit}/{daily_limit}\n"
        "────────────────────\n"
        f"Total Days: {total_days}\n"
        f"Days Remaining: {days_remaining}"
    )

def encrypt_message(plaintext):
    try:
        key = b'Yg&tc%DEuh6%Zc^8'
        iv = b'6oyZDr22E3ychjM%'
        cipher = AES.new(key, AES.MODE_CBC, iv)
        padded_message = pad(plaintext, AES.block_size)
        encrypted_message = cipher.encrypt(padded_message)
        return binascii.hexlify(encrypted_message).decode('utf-8')
    except Exception as e:
        app.logger.error(f"Error encrypting message: {e}")
        return None

def create_protobuf_message(user_id, region):
    try:
        message = like_pb2.like()
        message.uid = int(user_id)
        message.region = region
        return message.SerializeToString()
    except Exception as e:
        app.logger.error(f"Error creating protobuf message: {e}")
        return None

async def send_request(encrypted_uid, token, url):
    try:
        edata = bytes.fromhex(encrypted_uid)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'Expect': "100-continue",
            'X-Unity-Version': "2018.4.11f1",
            'X-GA': "v1 1",
            'ReleaseVersion': "OB52"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers) as response:
                if response.status != 200:
                    app.logger.error(f"Request failed with status code: {response.status}")
                    return response.status
                return await response.text()
    except Exception as e:
        app.logger.error(f"Exception in send_request: {e}")
        return None

async def send_multiple_requests(uid, server_name, url):
    try:
        region = server_name
        protobuf_message = create_protobuf_message(uid, region)
        if protobuf_message is None:
            app.logger.error("Failed to create protobuf message.")
            return None
        encrypted_uid = encrypt_message(protobuf_message)
        if encrypted_uid is None:
            app.logger.error("Encryption failed.")
            return None
        tasks = []
        tokens = load_tokens()
        if tokens is None:
            app.logger.error("Failed to load tokens.")
            return None
        for i in range(100):
            token = tokens[i % len(tokens)]["token"]
            tasks.append(send_request(encrypted_uid, token, url))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results
    except Exception as e:
        app.logger.error(f"Exception in send_multiple_requests: {e}")
        return None

def create_protobuf(uid):
    try:
        message = uid_generator_pb2.uid_generator()
        message.saturn_ = int(uid)
        message.garena = 1
        return message.SerializeToString()
    except Exception as e:
        app.logger.error(f"Error creating uid protobuf: {e}")
        return None

def enc(uid):
    protobuf_data = create_protobuf(uid)
    if protobuf_data is None:
        return None
    encrypted_uid = encrypt_message(protobuf_data)
    return encrypted_uid

def make_request(encrypt, server_name, token):
    try:
        if server_name == "IND":
            url = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            url = "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
        else:
            url = "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"
        edata = bytes.fromhex(encrypt)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'Expect': "100-continue",
            'X-Unity-Version': "2018.4.11f1",
            'X-GA': "v1 1",
            'ReleaseVersion': "OB52"
        }
        response = requests.post(url, data=edata, headers=headers, verify=False)
        hex_data = response.content.hex()
        binary = bytes.fromhex(hex_data)
        decode = decode_protobuf(binary)
        if decode is None:
            app.logger.error("Protobuf decoding returned None.")
        return decode
    except Exception as e:
        app.logger.error(f"Error in make_request: {e}")
        return None

def decode_protobuf(binary):
    try:
        items = like_count_pb2.Info()
        items.ParseFromString(binary)
        return items
    except DecodeError as e:
        app.logger.error(f"Error decoding Protobuf data: {e}")
        return None
    except Exception as e:
        app.logger.error(f"Unexpected error during protobuf decoding: {e}")
        return None

@app.route('/', methods=['GET'])
def index():
    return render_template_string(WEB_UI_TEMPLATE)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "credit": "https://t.me/paglu_dev",
        "message": "Welcome to the Free Fire Like API",
        "status": "API is running",
        "endpoints": "/like?uid=<uid> or /like?uid=<uid>&server_name=<server_name>",
        "example": "/like?uid=123456789 or /like?uid=123456789&server_name=bd"
})


@app.route('/like', methods=['GET'])
def handle_requests():
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "UID is required"}), 400

    try:
        target_likes = int(request.args.get("target_likes", 200))
        return process_like_request(uid, request.args.get("server_name", ""), target_likes=target_likes, used_calls=1)
    except Exception as e:
        app.logger.error(f"Error processing request: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/auto-like', methods=['GET'])
def auto_like_requests():
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "UID is required"}), 400

    server_name_input = request.args.get("server_name", "")
    try:
        target_likes = int(request.args.get("target_likes", 200))
    except ValueError:
        return jsonify({"error": "target_likes must be a number"}), 400
    try:
        count = int(request.args.get("count", 3))
    except ValueError:
        return jsonify({"error": "count must be a number between 1 and 20"}), 400

    if count < 1 or count > 20:
        return jsonify({"error": "count must be between 1 and 20"}), 400

    run_results = []
    total_likes = 0
    for i in range(count):
        result = process_like_request(uid, server_name_input, target_likes=target_likes, used_calls=i + 1)
        payload = result.get_json(silent=True) if hasattr(result, "get_json") else None
        if payload is None:
            payload = {"error": "Unexpected response format"}
        run_results.append({
            "run": i + 1,
            "response": payload
        })
        if isinstance(payload, dict):
            total_likes += int(payload.get("LikesGivenByAPI", 0) or 0)

    return jsonify({
        "UID": int(uid),
        "RepeatCount": count,
        "TotalLikesGiven": total_likes,
        "runs": run_results
    })


def process_like_request(uid, server_name_input="", target_likes=200, used_calls=1):
    try:
        tokens = load_tokens()
        if tokens is None or not tokens:
            return jsonify({"error": "Failed to load tokens."})
        token = tokens[0]['token']
        
        # Extract server_name (lock_region) from token if not provided
        server_name = server_name_input.upper()
        if not server_name:
            try:
                payload = token.split('.')[1]
                payload += '=' * (-len(payload) % 4)
                decoded_payload = base64.urlsafe_b64decode(payload).decode('utf-8')
                parsed_payload = json.loads(decoded_payload)
                server_name = parsed_payload.get('lock_region', '').upper()
            except Exception as e:
                app.logger.error(f"Error decoding token payload: {e}")
        
        if not server_name:
            return jsonify({"error": "server_name could not be determined from token or input"})
        
        encrypted_uid = enc(uid)
        if encrypted_uid is None:
            return jsonify({"error": "Encryption of UID failed."})

        # Get before likes count
        before = make_request(encrypted_uid, server_name, token)
        if before is None:
            return jsonify({"error": "Failed to retrieve player info. There are no valid token found! please update tokens.json with valid tokens"})
        
        data_before = json.loads(MessageToJson(before))
        before_like = int(data_before.get('AccountInfo', {}).get('Likes', 0) or 0)
        app.logger.info(f"Likes before: {before_like}")

        # Determine URL based on server
        if server_name == "IND":
            url = "https://client.ind.freefiremobile.com/LikeProfile"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            url = "https://client.us.freefiremobile.com/LikeProfile"
        else:
            url = "https://clientbp.ggpolarbear.com/LikeProfile"

        # Send like requests
        requests_sent = asyncio.run(send_multiple_requests(uid, server_name, url))
        app.logger.info(f"Requests sent: {requests_sent}")

        # Get after likes count
        after = make_request(encrypted_uid, server_name, token)
        if after is None:
            return jsonify({"error": "Failed to retrieve player info after likes."})
        
        data_after = json.loads(MessageToJson(after))
        account_info = data_after.get('AccountInfo', {})
        after_like = int(account_info.get('Likes', 0) or 0)
        player_uid = int(account_info.get('UID', 0) or 0)
        player_name = str(account_info.get('PlayerNickname', ''))
        
        like_given = after_like - before_like
        response_payload = {
            "credit": "https://t.me/paglu_dev",
            "LikesGivenByAPI": like_given,
            "LikesafterCommand": after_like,
            "LikesbeforeCommand": before_like,
            "PlayerNickname": player_name,
            "Region": server_name,
            "UID": player_uid,
            "status": 1 if like_given > 0 else 2
        }

        telegram_message = build_telegram_message(
            player_name=player_name,
            player_uid=player_uid,
            region=server_name,
            target_likes=target_likes,
            given_likes=like_given,
            before_like=before_like,
            after_like=after_like,
            used_calls=used_calls
        )
        send_telegram_message(telegram_message)

        return jsonify(response_payload)
    except Exception as e:
        app.logger.error(f"Error in process_like_request: {e}")
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
