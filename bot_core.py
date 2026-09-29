import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import threading
import re
import sys
import atexit
import requests

# ==================== AYARLAR ====================
TOKEN = os.environ.get("BOT_TOKEN", '8770928390:AAFfncd-e68x7liRV6OcFB8hgsotu7yLtIA').strip()
OWNER_ID = int(os.environ.get("OWNER_ID", 8727961464))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 8727961464))
YOUR_USERNAME = '@rinexdestek'
UPDATE_CHANNEL = 'https://t.me/rinexsorgux'

A4F_API_URL = "https://samuraiapi.in/v1/chat/completions"
A4F_API_KEY = "sk-NK6SS9tpWghyFJwkZLoCis1sMaF6"
A4F_MODEL = "provider10-claude-sonnet-4-20250514(clinesp)"

# ==================== DOSYA SİSTEMİ ====================
# Vercel için /tmp (yazılabilir ama geçici)
BASE_DIR = "/tmp"
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

FREE_USER_LIMIT = 2
SUBSCRIBED_USER_LIMIT = 2
ADMIN_LIMIT = 20
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

# ==================== GLOBAL STATE ====================
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel", "⏱ Uptime"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["📞 Contact Owner", "🤖 MPX Ai"]
]

ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel", "/ping"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["💳 Subscriptions", "📢 Broadcast"],
    ["🔒 Lock Bot", "🟢 Running All Code"],
    ["👑 Admin Panel", "📞 Contact Owner"],
    ["🤖 MPX Ai", "⏱ Uptime"],
]

# ==================== DATABASE ====================
def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit(); conn.close()
    except Exception as e:
        logger.error(f"DB init: {e}")

def load_data():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for uid, exp in c.fetchall():
            try: user_subscriptions[uid] = {'expiry': datetime.fromisoformat(exp)}
            except: pass
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for uid, fn, ft in c.fetchall():
            if uid not in user_files: user_files[uid] = []
            user_files[uid].append((fn, ft))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(u for (u,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(u for (u,) in c.fetchall())
        conn.close()
    except Exception as e:
        logger.error(f"Load: {e}")

# ============================================================
# ================ BOT OLUŞTUR (create_bot) ==================
# ============================================================
def create_bot(token):
    init_db()
    load_data()
    bot = telebot.TeleBot(token, threaded=False, parse_mode=None)

    # ---------- YARDIMCI FONKSİYONLAR ----------
    def get_user_folder(user_id):
        f = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
        os.makedirs(f, exist_ok=True)
        return f

    def get_user_file_limit(user_id):
        if user_id == OWNER_ID: return OWNER_LIMIT
        if user_id in admin_ids: return ADMIN_LIMIT
        if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
            return SUBSCRIBED_USER_LIMIT
        return FREE_USER_LIMIT

    def get_user_file_count(user_id):
        return len(user_files.get(user_id, []))

    def is_bot_running(script_owner_id, file_name):
        key = f"{script_owner_id}_{file_name}"
        info = bot_scripts.get(key)
        if info and info.get('process'):
            try:
                proc = psutil.Process(info['process'].pid)
                return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            except:
                return False
        return False

    def kill_process_tree(process_info):
        try:
            if 'log_file' in process_info and not process_info['log_file'].closed:
                try: process_info['log_file'].close()
                except: pass
            process = process_info.get('process')
            if process and hasattr(process, 'pid') and process.pid:
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try: child.terminate()
                        except:
                            try: child.kill()
                            except: pass
                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        try: p.kill()
                        except: pass
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired: parent.kill()
                    except:
                        try: parent.kill()
                        except: pass
                except: pass
        except Exception as e:
            logger.error(f"Kill tree: {e}")

    TELEGRAM_MODULES = {
        'telebot': 'pyTelegramBotAPI',
        'telegram': 'python-telegram-bot',
        'aiogram': 'aiogram',
        'pyrogram': 'pyrogram',
        'telethon': 'telethon',
        'bs4': 'beautifulsoup4',
        'requests': 'requests',
        'pillow': 'Pillow',
        'cv2': 'opencv-python',
        'yaml': 'PyYAML',
        'dotenv': 'python-dotenv',
        'dateutil': 'python-dateutil',
        'pandas': 'pandas',
        'numpy': 'numpy',
        'flask': 'Flask',
        'sqlalchemy': 'SQLAlchemy',
        'psutil': 'psutil',
    }

    def attempt_install_pip(module_name, message):
        package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
        try:
            bot.reply_to(message, f"Module `{module_name}` yükleniyor...", parse_mode='Markdown')
            r = subprocess.run([sys.executable, '-m', 'pip', 'install', package_name],
                               capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
            if r.returncode == 0:
                bot.reply_to(message, f"✅ `{package_name}` yüklendi.")
                return True
            else:
                bot.reply_to(message, f"❌ Yüklenemedi: {r.stderr[:500]}")
                return False
        except Exception as e:
            bot.reply_to(message, f"Error: {e}")
            return False

    # ---------- SCRIPT ÇALIŞTIRMA (Python) ----------
    def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
        key = f"{script_owner_id}_{file_name}"
        try:
            if not os.path.exists(script_path):
                bot.reply_to(message_obj, f"❌ Dosya bulunamadı: {file_name}")
                return

            if attempt == 1:
                try:
                    chk = subprocess.Popen([sys.executable, script_path], cwd=user_folder,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding='utf-8', errors='ignore')
                    stdout, stderr = chk.communicate(timeout=5)
                    if chk.returncode != 0 and stderr:
                        m = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                        if m:
                            mod = m.group(1).strip().strip("'\"")
                            if attempt_install_pip(mod, message_obj):
                                time.sleep(2)
                                threading.Thread(target=run_script,
                                    args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt+1)).start()
                                return
                        bot.reply_to(message_obj, f"❌ Hata:\n```\n{stderr[:500]}\n```", parse_mode='Markdown')
                        return
                except subprocess.TimeoutExpired:
                    try: chk.kill()
                    except: pass
                except Exception as e:
                    logger.error(f"Pre-check: {e}")

            log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
            log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')

            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder,
                stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                encoding='utf-8', errors='ignore'
            )

            bot_scripts[key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj.chat.id, 'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder,
                'type': 'py', 'script_key': key
            }
            bot.reply_to(message_obj, f"✅ `{file_name}` başlatıldı! PID: {process.pid}")
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Çalıştırma hatası: {e}")

    # ---------- SCRIPT ÇALIŞTIRMA (JS) ----------
    def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
        key = f"{script_owner_id}_{file_name}"
        try:
            if not os.path.exists(script_path):
                bot.reply_to(message_obj, f"❌ Dosya bulunamadı: {file_name}")
                return

            log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
            log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')

            process = subprocess.Popen(
                ['node', script_path], cwd=user_folder,
                stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                encoding='utf-8', errors='ignore'
            )

            bot_scripts[key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj.chat.id, 'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder,
                'type': 'js', 'script_key': key
            }
            bot.reply_to(message_obj, f"✅ `{file_name}` (JS) başlatıldı! PID: {process.pid}")
        except FileNotFoundError:
            bot.reply_to(message_obj, "❌ 'node' bulunamadı")
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Çalıştırma hatası: {e}")

    # ---------- DB KAYIT FONKSİYONLARI ----------
    def save_user_file(user_id, file_name, file_type='py'):
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit(); conn.close()
            if user_id not in user_files: user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e:
            logger.error(f"Save file: {e}")

    def remove_user_file_db(user_id, file_name):
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit(); conn.close()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
        except Exception as e:
            logger.error(f"Remove file: {e}")

    def add_active_user(user_id):
        active_users.add(user_id)
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit(); conn.close()
        except: pass

    def save_subscription(user_id, expiry):
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                      (user_id, expiry.isoformat()))
            conn.commit(); conn.close()
            user_subscriptions[user_id] = {'expiry': expiry}
        except: pass

    def remove_subscription_db(user_id):
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit(); conn.close()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
        except: pass

    def add_admin_db(admin_id):
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit(); conn.close()
            admin_ids.add(admin_id)
        except: pass

    def remove_admin_db(admin_id):
        if admin_id == OWNER_ID: return False
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit(); conn.close()
            admin_ids.discard(admin_id)
            return True
        except: return False

    # ---------- KLAVYELER ----------
    def create_main_menu_inline(user_id):
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [
            types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL),
            types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
            types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
            types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'),
            types.InlineKeyboardButton('🤖 MPX AI', callback_data='mpx_ai')
        ]
        if user_id in admin_ids:
            admin_buttons = [
                types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
                types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
                types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot',
                                         callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
                types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'),
                types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts')
            ]
            markup.add(buttons[0])
            markup.add(buttons[1], buttons[2])
            markup.add(buttons[3], admin_buttons[0])
            markup.add(buttons[4], admin_buttons[1])
            markup.add(admin_buttons[2], admin_buttons[4])
            markup.add(admin_buttons[3])
            markup.add(buttons[5], buttons[6])
        else:
            markup.add(buttons[0])
            markup.add(buttons[1], buttons[2])
            markup.add(buttons[3])
            markup.add(buttons[4])
            markup.add(buttons[5], buttons[6])
        markup.add(types.InlineKeyboardButton('⏱ Uptime', callback_data='uptime'))
        return markup

    def create_control_buttons(script_owner_id, file_name, is_running=True):
        markup = types.InlineKeyboardMarkup(row_width=2)
        if is_running:
            markup.row(
                types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
                types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
            )
            markup.row(
                types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
                types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
            )
        else:
            markup.row(
                types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
                types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
            )
            markup.row(
                types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
            )
        markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
        return markup

    def create_admin_panel():
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.row(
            types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
            types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
        )
        markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
        markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
        return markup

    def create_subscription_menu():
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.row(
            types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
            types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
        )
        markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
        markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
        return markup

    # ---------- HANDLERS ----------
    @bot.message_handler(commands=['start', 'help'])
    def command_send_welcome(message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        user_name = message.from_user.first_name
        user_username = message.from_user.username

        if bot_locked and user_id not in admin_ids:
            bot.send_message(chat_id, "Bot locked by admin.")
            return

        if user_id not in active_users:
            add_active_user(user_id)
            try:
                bot.send_message(OWNER_ID,
                    f"New user!\nName: {user_name}\nUser: @{user_username or 'N/A'}\nID: `{user_id}`",
                    parse_mode='Markdown')
            except: pass

        file_limit = get_user_file_limit(user_id)
        current = get_user_file_count(user_id)
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"

        if user_id == OWNER_ID: status = "Owner"
        elif user_id in admin_ids: status = "Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            status = "Premium"
        else: status = "Free User"

        welcome = (f"Welcome, {user_name}!\n\n"
                   f"User ID: `{user_id}`\n"
                   f"Status: {status}\n"
                   f"Files: {current}/{limit_str}\n\n"
                   f"Use buttons below:")
        bot.send_message(chat_id, welcome, reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')

    @bot.message_handler(commands=['ping'])
    def ping(message):
        t = time.time()
        msg = bot.reply_to(message, "Pong!")
        bot.edit_message_text(f"Pong! {round((time.time()-t)*1000, 2)} ms",
                              message.chat.id, msg.message_id)

    @bot.message_handler(commands=['mpx'])
    def handle_mpx_command(message):
        if len(message.text.split()) < 2:
            bot.reply_to(message, "Kullanım: `/mpx soru`", parse_mode='Markdown')
            return
        query = message.text.split(' ', 1)[1]
        bot.send_chat_action(message.chat.id, 'typing')
        try:
            r = requests.post(A4F_API_URL,
                headers={"Authorization": f"Bearer {A4F_API_KEY}", "Content-Type": "application/json"},
                json={"model": A4F_MODEL, "messages": [{"role":"user","content":query}], "temperature":0.7},
                timeout=25)
            result = r.json()
            ans = result.get('choices', [{}])[0].get('message', {}).get('content', 'No response')
            if len(ans) > 4000:
                for x in range(0, len(ans), 4000):
                    bot.reply_to(message, ans[x:x+4000])
            else:
                bot.reply_to(message, ans)
        except Exception as e:
            bot.reply_to(message, f"Error: {e}")

    @bot.message_handler(commands=['status'])
    def command_status(message):
        total_users = len(active_users)
        total_files = sum(len(v) for v in user_files.values())
        running = sum(1 for k, v in bot_scripts.items() if is_bot_running(int(k.split('_')[0]), v['file_name']))
        bot.reply_to(message,
            f"Bot Statistics:\n\nTotal Users: {total_users}\n"
            f"Total Files: {total_files}\nActive Bots: {running}")

    @bot.message_handler(commands=['uptime'])
    def command_uptime(message):
        bot.reply_to(message, "Uptime: Vercel Serverless")

    # ---------- DOSYA YÜKLEME ----------
    @bot.message_handler(content_types=['document'])
    def handle_file_upload_doc(message):
        user_id = message.from_user.id
        chat_id = message.chat.id
        doc = message.document

        if bot_locked and user_id not in admin_ids:
            bot.reply_to(message, "Bot locked.")
            return

        file_limit = get_user_file_limit(user_id)
        if get_user_file_count(user_id) >= file_limit:
            bot.reply_to(message, f"File limit reached ({file_limit}).")
            return

        file_name = doc.file_name
        if not file_name:
            bot.reply_to(message, "No file name.")
            return
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ['.py', '.js', '.zip']:
            bot.reply_to(message, "Only `.py`, `.js`, `.zip` allowed.")
            return

        max_size = 20 * 1024 * 1024
        if doc.file_size > max_size:
            bot.reply_to(message, "File too large (max 20MB).")
            return

        try:
            bot.forward_message(OWNER_ID, chat_id, message.message_id)
        except: pass

        wait = bot.reply_to(message, f"Downloading `{file_name}`...")
        try:
            info = bot.get_file(doc.file_id)
            data = bot.download_file(info.file_path)
            bot.edit_message_text(f"Downloaded. Processing...", chat_id, wait.message_id)

            user_folder = get_user_folder(user_id)

            if ext == '.zip':
                temp_dir = tempfile.mkdtemp(prefix=f"zip_{user_id}_")
                try:
                    zip_path = os.path.join(temp_dir, file_name)
                    with open(zip_path, 'wb') as f: f.write(data)
                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        zf.extractall(temp_dir)

                    items = os.listdir(temp_dir)
                    py_files = [f for f in items if f.endswith('.py')]
                    js_files = [f for f in items if f.endswith('.js')]
                    req = 'requirements.txt' if 'requirements.txt' in items else None

                    if req:
                        try:
                            subprocess.run([sys.executable, '-m', 'pip', 'install', '-r',
                                          os.path.join(temp_dir, req)],
                                          capture_output=True, text=True, timeout=120)
                        except: pass

                    main = None; ftype = None
                    for p in ['main.py', 'bot.py', 'app.py']:
                        if p in py_files: main = p; ftype = 'py'; break
                    if not main:
                        for p in ['index.js', 'main.js', 'bot.js']:
                            if p in js_files: main = p; ftype = 'js'; break
                    if not main:
                        if py_files: main = py_files[0]; ftype = 'py'
                        elif js_files: main = js_files[0]; ftype = 'js'
                    if not main:
                        bot.reply_to(message, "No script found in ZIP.")
                        return

                    for item in os.listdir(temp_dir):
                        src = os.path.join(temp_dir, item)
                        dst = os.path.join(user_folder, item)
                        if os.path.isdir(dst): shutil.rmtree(dst)
                        elif os.path.exists(dst): os.remove(dst)
                        shutil.move(src, dst)

                    save_user_file(user_id, main, ftype)
                    bot.reply_to(message, f"✅ ZIP açıldı. Ana script: `{main}`", parse_mode='Markdown')
                finally:
                    if os.path.exists(temp_dir): shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                file_path = os.path.join(user_folder, file_name)
                with open(file_path, 'wb') as f: f.write(data)
                ftype = 'py' if ext == '.py' else 'js'
                save_user_file(user_id, file_name, ftype)
                bot.reply_to(message, f"✅ `{file_name}` yüklendi.", parse_mode='Markdown')
        except Exception as e:
            bot.reply_to(message, f"Error: {e}")

    # ---------- CALLBACK HANDLER ----------
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callbacks(call):
        user_id = call.from_user.id
        data = call.data
        chat_id = call.message.chat.id
        try: bot.answer_callback_query(call.id)
        except: pass

        if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats', 'uptime']:
            bot.answer_callback_query(call.id, "Bot locked.", show_alert=True)
            return

        try:
            if data == 'upload':
                bot.send_message(chat_id, "Send `.py`, `.js`, or `.zip` file.")

            elif data == 'check_files':
                uf = user_files.get(user_id, [])
                if not uf:
                    bot.send_message(chat_id, "No files uploaded.")
                    return
                markup = types.InlineKeyboardMarkup(row_width=1)
                for fn, ft in sorted(uf):
                    running = is_bot_running(user_id, fn)
                    icon = "🟢" if running else "🔴"
                    markup.add(types.InlineKeyboardButton(f"{icon} {fn} ({ft})",
                                                          callback_data=f'file_{user_id}_{fn}'))
                markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
                bot.send_message(chat_id, "Your files:", reply_markup=markup)

            elif data.startswith('file_'):
                _, owner_id_str, fname = data.split('_', 2)
                owner_id = int(owner_id_str)
                if user_id != owner_id and user_id not in admin_ids:
                    bot.answer_callback_query(call.id, "Permission denied.", show_alert=True)
                    return
                running = is_bot_running(owner_id, fname)
                bot.send_message(chat_id, f"Controls for `{fname}`:",
                                 reply_markup=create_control_buttons(owner_id, fname, running),
                                 parse_mode='Markdown')

            elif data.startswith('start_'):
                _, oid_str, fname = data.split('_', 2)
                oid = int(oid_str)
                if user_id != oid and user_id not in admin_ids:
                    bot.answer_callback_query(call.id, "Permission denied.", show_alert=True)
                    return
                folder = get_user_folder(oid)
                fpath = os.path.join(folder, fname)
                if not os.path.exists(fpath):
                    bot.send_message(chat_id, f"❌ File missing.")
                    return
                ft = next((f[1] for f in user_files.get(oid, []) if f[0] == fname), 'py')
                if ft == 'py':
                    threading.Thread(target=run_script, args=(fpath, oid, folder, fname, call.message)).start()
                elif ft == 'js':
                    threading.Thread(target=run_js_script, args=(fpath, oid, folder, fname, call.message)).start()
                time.sleep(1.5)
                running = is_bot_running(oid, fname)
                bot.edit_message_reply_markup(chat_id, call.message.message_id,
                    reply_markup=create_control_buttons(oid, fname, running))

            elif data.startswith('stop_'):
                _, oid_str, fname = data.split('_', 2)
                oid = int(oid_str)
                if user_id != oid and user_id not in admin_ids: return
                key = f"{oid}_{fname}"
                info = bot_scripts.get(key)
                if info:
                    kill_process_tree(info)
                    if key in bot_scripts: del bot_scripts[key]
                bot.edit_message_reply_markup(chat_id, call.message.message_id,
                    reply_markup=create_control_buttons(oid, fname, False))

            elif data.startswith('restart_'):
                _, oid_str, fname = data.split('_', 2)
                oid = int(oid_str)
                if user_id != oid and user_id not in admin_ids: return
                key = f"{oid}_{fname}"
                if key in bot_scripts:
                    kill_process_tree(bot_scripts[key])
                    del bot_scripts[key]
                    time.sleep(1)
                folder = get_user_folder(oid)
                fpath = os.path.join(folder, fname)
                ft = next((f[1] for f in user_files.get(oid, []) if f[0] == fname), 'py')
                if ft == 'py':
                    threading.Thread(target=run_script, args=(fpath, oid, folder, fname, call.message)).start()
                elif ft == 'js':
                    threading.Thread(target=run_js_script, args=(fpath, oid, folder, fname, call.message)).start()

            elif data.startswith('delete_'):
                _, oid_str, fname = data.split('_', 2)
                oid = int(oid_str)
                if user_id != oid and user_id not in admin_ids: return
                key = f"{oid}_{fname}"
                if key in bot_scripts:
                    kill_process_tree(bot_scripts[key])
                    del bot_scripts[key]
                folder = get_user_folder(oid)
                for p in [os.path.join(folder, fname),
                          os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")]:
                    if os.path.exists(p):
                        try: os.remove(p)
                        except: pass
                remove_user_file_db(oid, fname)
                bot.send_message(chat_id, f"🗑️ {fname} deleted.")

            elif data.startswith('logs_'):
                _, oid_str, fname = data.split('_', 2)
                oid = int(oid_str)
                if user_id != oid and user_id not in admin_ids: return
                folder = get_user_folder(oid)
                lp = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
                if not os.path.exists(lp):
                    bot.send_message(chat_id, "No logs.")
                    return
                try:
                    with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()[-35:]
                    content = "".join(lines)[:3500]
                    bot.send_message(chat_id, f"Logs `{fname}`:\n```\n{content}\n```", parse_mode='Markdown')
                except Exception as e:
                    bot.send_message(chat_id, f"Error: {e}")

            elif data == 'speed':
                bot.send_message(chat_id, f"Bot Status: Vercel Serverless")

            elif data == 'stats':
                bot.send_message(chat_id,
                    f"Total Users: {len(active_users)}\n"
                    f"Total Files: {sum(len(v) for v in user_files.values())}")

            elif data == 'uptime':
                bot.send_message(chat_id, "Vercel Serverless")

            elif data == 'back_to_main':
                bot.send_message(chat_id, "Main Menu:", reply_markup=create_main_menu_inline(user_id))

            elif data == 'admin_panel':
                if user_id not in admin_ids: return
                bot.send_message(chat_id, "Admin Panel:", reply_markup=create_admin_panel())

            elif data == 'lock_bot':
                if user_id not in admin_ids: return
                globals()['bot_locked'] = True
                bot.send_message(chat_id, "Bot locked.")

            elif data == 'unlock_bot':
                if user_id not in admin_ids: return
                globals()['bot_locked'] = False
                bot.send_message(chat_id, "Bot unlocked.")

            elif data == 'subscription':
                if user_id not in admin_ids: return
                bot.send_message(chat_id, "Subscription Menu:", reply_markup=create_subscription_menu())

            elif data == 'broadcast':
                if user_id not in admin_ids: return
                bot.send_message(chat_id, "Broadcast not supported on Vercel.")

            elif data == 'run_all_scripts':
                if user_id not in admin_ids: return
                bot.send_message(chat_id, "Not supported on Vercel.")

            elif data == 'mpx_ai':
                bot.send_message(chat_id, "Use `/mpx <soru>`", parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Callback error '{data}': {e}")
            try: bot.answer_callback_query(call.id, "Error.", show_alert=True)
            except: pass

    return bot
