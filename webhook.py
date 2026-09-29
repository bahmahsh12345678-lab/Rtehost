import sys
import os
from flask import Flask, request, jsonify
import telebot

# bot_core.py aynı klasörde olduğu için sys.path gerekmez
from bot_core import TOKEN, ADMIN_ID, create_bot

app = Flask(__name__)
_BOT_CACHE = {}


def get_bot(token):
    if token not in _BOT_CACHE:
        _BOT_CACHE[token] = create_bot(token)
    return _BOT_CACHE[token]


def process_update(token, update_json):
    try:
        bot = get_bot(token)
        update = telebot.types.Update.de_json(update_json)
        bot.process_new_updates([update])
        return True
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
        return False


@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "bot": "Rinex Hosting",
        "admin_id": ADMIN_ID,
        "mode": "vercel-serverless"
    })


@app.route("/webhook/<bot_token>", methods=["POST"])
def webhook(bot_token):
    update = request.get_json(force=True)
    if not update:
        return jsonify({"ok": False, "error": "empty"}), 400
    if process_update(bot_token, update):
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 500


@app.route("/setwebhook", methods=["GET"])
def set_webhook():
    base_url = request.host_url.rstrip("/")
    try:
        bot = get_bot(TOKEN)
        url = f"{base_url}/webhook/{TOKEN}"
        bot.remove_webhook()
        bot.set_webhook(url=url, drop_pending_updates=True)
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/resetwebhook", methods=["GET"])
def reset_webhook():
    try:
        bot = get_bot(TOKEN)
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
        return jsonify({"ok": True, "mesaj": "Webhook silindi"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


handler = app
