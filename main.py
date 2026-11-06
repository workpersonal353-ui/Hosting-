import os
import subprocess
import logging
import time
import random
import string
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8928252124:AAEOUQsphkMYBnsjnakOoM5h0Aq9x7sewMA"
ADMIN_ID = 8879326948
UPDATES_CHANNEL = "https://t.me/+sk7oV8dh14s1M2Zl"
OWNER_USERNAME = "@Tanveerpy"

PLANS_DATA = {
    "Basic": {"files": 8, "cpu": "1 CPU", "ram": "1 GB RAM", "ssd": "30 GB SSD", "price": "₹45"},
    "Standard": {"files": 25, "cpu": "2 CPU", "ram": "3 GB RAM", "ssd": "80 GB SSD", "price": "₹79"},
    "Pro": {"files": 60, "cpu": "4 CPU", "ram": "6 GB RAM", "ssd": "200 GB SSD", "price": "₹149"},
    "Premium": {"files": 250, "cpu": "8 CPU", "ram": "12 GB RAM", "ssd": "900 GB SSD", "price": "₹2499"}
}

def init_db():
    conn = sqlite3.connect('flexyy_hosting.db', check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tier TEXT DEFAULT 'Free',
            max_files INTEGER DEFAULT 4,
            expiry_date TEXT,
            is_banned INTEGER DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'referrals' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN referrals INTEGER DEFAULT 0")
    if 'expiry_date' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN expiry_date TEXT")
    if 'is_banned' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    if 'points' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            tier TEXT,
            max_files INTEGER,
            days INTEGER,
            uses_left INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            status TEXT DEFAULT 'Pending'
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_user_db(user_id):
    conn = sqlite3.connect('flexyy_hosting.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT tier, max_files, expiry_date, is_banned, referrals FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    now = datetime.now()
    if not row:
        cursor.execute("INSERT OR IGNORE INTO users (user_id, tier, max_files, expiry_date, is_banned, referrals) VALUES (?, 'Free', 4, NULL, 0, 0)", (user_id,))
        conn.commit()
        tier, max_files, expiry_date, is_banned, referrals = 'Free', 4, None, 0, 0
    else:
        tier, max_files, expiry_date, is_banned, referrals = row
        if expiry_date and tier != 'Free' and expiry_date != 'Lifetime':
            try:
                exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d %H:%M:%S")
                if now > exp_dt:
                    cursor.execute("UPDATE users SET tier = 'Free', max_files = 4, expiry_date = NULL WHERE user_id = ?", (user_id,))
                    conn.commit()
                    tier, max_files, expiry_date = 'Free', 4, None
            except:
                pass
    conn.close()
    return {"tier": tier, "max_files": max_files, "expiry": expiry_date, "is_banned": is_banned, "referrals": referrals}

def update_user_db(user_id, tier, max_files, days):
    conn = sqlite3.connect('flexyy_hosting.db', check_same_thread=False)
    cursor = conn.cursor()
    if days == 9999 or days == 0:
        expiry_dt = 'Lifetime'
    else:
        expiry_dt = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT OR REPLACE INTO users (user_id, tier, max_files, expiry_date, is_banned, referrals) VALUES (?, ?, ?, ?, (SELECT COALESCE((SELECT is_banned FROM users WHERE user_id = ?), 0)), (SELECT COALESCE((SELECT referrals FROM users WHERE user_id = ?), 0)))", (user_id, tier, max_files, expiry_dt, user_id, user_id))
    conn.commit()
    conn.close()

def set_ban_db(user_id, status):
    conn = sqlite3.connect('flexyy_hosting.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()

def save_coupon_db(code, tier, max_files, days, uses):
    conn = sqlite3.connect('flexyy_hosting.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO coupons (code, tier, max_files, days, uses_left) VALUES (?, ?, ?, ?, ?)", (code, tier, max_files, days, uses))
    conn.commit()
    conn.close()

def use_coupon_db(code):
    conn = sqlite3.connect('flexyy_hosting.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT tier, max_files, days, uses_left FROM coupons WHERE code = ?", (code,))
    row = cursor.fetchone()
    if row:
        tier, max_files, days, uses_left = row
        if uses_left > 1:
            cursor.execute("UPDATE coupons SET uses_left = uses_left - 1 WHERE code = ?", (code,))
        else:
            cursor.execute("DELETE FROM coupons WHERE code = ?", (code,))
        conn.commit()
        conn.close()
        return {"tier": tier, "max_files": max_files, "days": days}
    conn.close()
    return None

active_processes = {}  
user_states = {}
maintenance_mode = False

def get_user_files(user_id):
    user_dir = f"uploads/{user_id}"
    if not os.path.exists(user_dir):
        return []
    return [f for f in os.listdir(user_dir) if f.endswith('.py') or f.endswith('.js')]

def get_admin_files():
    admin_dir = "uploads/admin_special"
    if not os.path.exists(admin_dir):
        return []
    return [f for f in os.listdir(admin_dir) if f.endswith('.py') or f.endswith('.js')]

def generate_random_coupon():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    u_info = get_user_db(user_id)
    
    if u_info["is_banned"] and user_id != ADMIN_ID:
        await update.effective_message.reply_text("⛔ *Access Denied:* Aapko admin dwara ban kar diya gaya hai.")
        return

    if maintenance_mode and user_id != ADMIN_ID:
        await update.effective_message.reply_text("🛠️ *Maintenance Mode Active:* Bot abhi update ho raha hai. Kripya thodi der baad try karein.")
        return

    running_count = len(active_processes.get(user_id, {}))
    files_count = len(get_user_files(user_id))
    
    welcome_text = (
        f"⚡ ᴘʏᴛʜᴏɴ ʜᴏꜱᴛɪɴɢ ʙᴏᴛ ⚡\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"👤 ᴜꜱᴇʀ : `{user.first_name}`\n"
        f"🆔 ɪᴅ : `{user_id}`\n"
        f"💎 ᴄᴜʀʀᴇɴᴛ ᴘʟᴀɴ : `{u_info['tier']}`\n"
        f"⏳ ᴇxᴘɪʀʏ : `{u_info['expiry'] if u_info['expiry'] else 'Lifetime / Free'}`\n"
        f"📂 ꜰɪʟᴇꜱ ᴜᴘʟᴏᴀᴅᴇᴅ : `{files_count}` | ʀᴜɴɴɪɴɢ : `{running_count} / {u_info['max_files']}`\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"🚀 ʀᴇᴀᴅʏ ᴛᴏ ᴅᴇᴘʟᴏʏ! ᴜᴘʟᴏᴀᴅ ʏᴏᴜʀ ꜱᴄʀɪᴘᴛ 24/7."
    )

    keyboard = [
        [InlineKeyboardButton("📤 Upload Script", callback_data="upload_file"),
         InlineKeyboardButton("📁 My Files Manager", callback_data="check_files")],
        [InlineKeyboardButton("💎 VPS Plans & Specs", callback_data="buy_plans"),
         InlineKeyboardButton("🎟️ Redeem Coupon", callback_data="redeem_coupon")],
        [InlineKeyboardButton("📢 Updates Channel", url=UPDATES_CHANNEL),
         InlineKeyboardButton("🆘 Support", callback_data="support_ticket")]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("📂 Admin Special Files", callback_data="admin_files_menu")])
        keyboard.append([InlineKeyboardButton("🛠️ Admin Control Center", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        try:
            await update.callback_query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
        except:
            await context.bot.send_message(chat_id=user_id, text=welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def refresh_file_menu(query, user_id):
    files = get_user_files(user_id)
    running_files = active_processes.get(user_id, {})
    
    if not files:
        file_text = "📁 *Aapne abhi tak koi file upload nahi ki hai.*"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]])
    else:
        file_text = "📁 **YOUR HOSTED FILES & STATUS:**\n\n"
        keyboard = []
        for f in files:
            p_info = running_files.get(f)
            is_running = p_info is not None and p_info["proc"].poll() is None
            status_icon = "🟢 RUNNING" if is_running else "🔴 STOPPED"
            file_text += f"• `{f}` -> {status_icon}\n"
            
            if is_running:
                keyboard.append([
                    InlineKeyboardButton(f"🛑 Stop", callback_data=f"stop_{user_id}_{f}"),
                    InlineKeyboardButton(f"📋 Logs", callback_data=f"logs_{user_id}_{f}")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(f"▶️ Start", callback_data=f"start_{user_id}_{f}"),
                    InlineKeyboardButton(f"🗑️ Delete", callback_data=f"del_{user_id}_{f}"),
                    InlineKeyboardButton(f"📋 Logs", callback_data=f"logs_{user_id}_{f}")
                ])
                keyboard.append([InlineKeyboardButton(f"📥 Download Backup ({f})", callback_data=f"dl_{user_id}_{f}")])
        
        keyboard.append([InlineKeyboardButton("« Back", callback_data="main_menu")])
        markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.edit_text(file_text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass

async def refresh_admin_file_menu(query):
    files = get_admin_files()
    running_files = active_processes.get(ADMIN_ID, {})
    
    if not files:
        file_text = "📁 *Admin ki koi special file upload nahi hai. Upload button se bhejein!*"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Upload Admin File", callback_data="upload_admin_file")],
            [InlineKeyboardButton("« Back", callback_data="admin_panel")]
        ])
    else:
        file_text = "📁 **ADMIN SPECIAL HOSTED FILES:**\n\n"
        keyboard = [[InlineKeyboardButton("📤 Upload New Admin File", callback_data="upload_admin_file")]]
        for f in files:
            p_info = running_files.get(f)
            is_running = p_info is not None and p_info["proc"].poll() is None
            status_icon = "🟢 RUNNING" if is_running else "🔴 STOPPED"
            file_text += f"• `{f}` -> {status_icon}\n"
            
            if is_running:
                keyboard.append([
                    InlineKeyboardButton(f"🛑 Stop", callback_data=f"admstop_{f}"),
                    InlineKeyboardButton(f"📋 Logs", callback_data=f"admlogs_{f}")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(f"▶️ Start", callback_data=f"admstart_{f}"),
                    InlineKeyboardButton(f"🗑️ Delete", callback_data=f"admdel_{f}"),
                    InlineKeyboardButton(f"📋 Logs", callback_data=f"admlogs_{f}")
                ])
        keyboard.append([InlineKeyboardButton("« Back", callback_data="admin_panel")])
        markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.edit_text(file_text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == "upload_file":
        await query.answer()
        u_info = get_user_db(user_id)
        current_files = len(get_user_files(user_id))
        if current_files >= u_info["max_files"]:
            await query.message.edit_text(
                "❌ *File Limit Reached!* Plan upgrade karne ke liye VPS Plans visit karein.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]]),
                parse_mode="Markdown"
            )
            return

        user_states[user_id] = 'WAITING_FOR_FILE'
        await query.message.edit_text(
            "📤 **UPLOAD SCRIPT**\n\nApni Python (`.py`) ya JavaScript (`.js`) file yahan bhejein (Approval ke baad live hogi):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]]),
            parse_mode="Markdown"
        )

    elif data == "upload_admin_file" and user_id == ADMIN_ID:
        await query.answer()
        user_states[user_id] = 'WAITING_FOR_ADMIN_FILE'
        await query.message.edit_text(
            "📤 **ADMIN FILE UPLOAD**\n\nApni special script (.py ya .js) yahan bhejein:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_files_menu")]]),
            parse_mode="Markdown"
        )
    
    elif data == "admin_files_menu" and user_id == ADMIN_ID:
        await query.answer()
        await refresh_admin_file_menu(query)
    
    elif data == "check_files":
        await query.answer()
        await refresh_file_menu(query, user_id)

    elif data == "redeem_coupon":
        await query.answer()
        user_states[user_id] = 'WAITING_FOR_COUPON'
        await query.message.edit_text(
            "🎟️ **REDEEM COUPON**\n\nApna coupon code yahan type karke bhejein:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]]),
            parse_mode="Markdown"
        )

    elif data == "support_ticket":
        await query.answer()
        user_states[user_id] = 'WAITING_SUPPORT_MSG'
        await query.message.edit_text(
            "🛠️ **SUPPORT TICKET**\n\nAapko kya madad chahiye? Apna message yahan type karke bheejein:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]]),
            parse_mode="Markdown"
        )

    elif data == "buy_plans":
        await query.answer()
        plans_text = (
            "💻 **VPS PLANS & SPECIFICATIONS** 💻\n\n"
            "▫️ **[ BASIC ]**\n"
            f"   • {PLANS_DATA['Basic']['cpu']} • {PLANS_DATA['Basic']['ram']}\n"
            f"   • {PLANS_DATA['Basic']['ssd']} • Max Files: {PLANS_DATA['Basic']['files']}\n"
            f"   • Price: **{PLANS_DATA['Basic']['price']} / mo**\n\n"
            "▫️ **[ STANDARD ]**\n"
            f"   • {PLANS_DATA['Standard']['cpu']} • {PLANS_DATA['Standard']['ram']}\n"
            f"   • {PLANS_DATA['Standard']['ssd']} • Max Files: {PLANS_DATA['Standard']['files']}\n"
            f"   • Price: **{PLANS_DATA['Standard']['price']} / mo**\n\n"
            "▫️ **[ PRO ]**\n"
            f"   • {PLANS_DATA['Pro']['cpu']} • {PLANS_DATA['Pro']['ram']}\n"
            f"   • {PLANS_DATA['Pro']['ssd']} • Max Files: {PLANS_DATA['Pro']['files']}\n"
            f"   • Price: **{PLANS_DATA['Pro']['price']} / mo**\n\n"
            "▫️ **[ PREMIUM ]**\n"
            f"   • {PLANS_DATA['Premium']['cpu']} • {PLANS_DATA['Premium']['ram']}\n"
            f"   • {PLANS_DATA['Premium']['ssd']} • Max Files: {PLANS_DATA['Premium']['files']}\n"
            f"   • Price: **{PLANS_DATA['Premium']['price']} / Lifetime**\n\n"
            f"🛒 Plan purchase karne ke liye admin ko message karein:\n👤 <b>{OWNER_USERNAME}</b>"
        )
        plans_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💻 Basic ({PLANS_DATA['Basic']['price']})", callback_data="buy_Basic"),
             InlineKeyboardButton(f"⚡ Standard ({PLANS_DATA['Standard']['price']})", callback_data="buy_Standard")],
            [InlineKeyboardButton(f"🔥 Pro ({PLANS_DATA['Pro']['price']})", callback_data="buy_Pro"),
             InlineKeyboardButton(f"💎 Premium ({PLANS_DATA['Premium']['price']})", callback_data="buy_Premium")],
            [InlineKeyboardButton("« Back", callback_data="main_menu")]
        ])
        await query.message.edit_text(plans_text, reply_markup=plans_markup, parse_mode="Markdown")

    elif data.startswith("buy_"):
        await query.answer()
        plan_name = data.replace("buy_", "")
        p_info = PLANS_DATA.get(plan_name, {"price": "Custom"})
        buy_info = (
            f"🛒 **CHECKOUT: {plan_name} Plan**\n\n"
            f"🆔 **Your User ID:** `{user_id}`\n"
            f"💰 **Price:** `{p_info['price']}`\n\n"
            f"Payment karne ke baad screenshot aur apna User ID admin ko bhejein:\n👤 <b>{OWNER_USERNAME}</b>"
        )
        await query.message.edit_text(
            buy_info,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="buy_plans")]]),
            parse_mode="Markdown"
        )

    elif data == "admin_panel":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        m_status = "ENABLED 🔴" if maintenance_mode else "DISABLED 🟢"
        admin_text = "🛠️ **[ ADVANCED ADMIN CONTROL CENTER ]** 🛠️\nSelect an option below:"
        admin_kb = [
            [InlineKeyboardButton("🎟️ Create Coupon", callback_data="adm_create_coupon"),
             InlineKeyboardButton("👑 Give Plan", callback_data="adm_give_prem")],
            [InlineKeyboardButton("📊 Bot Stats", callback_data="adm_stats"),
             InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
            [InlineKeyboardButton("🔍 Inspect User", callback_data="adm_inspect"),
             InlineKeyboardButton("📨 View Tickets", callback_data="adm_tickets")],
            [InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"),
             InlineKeyboardButton("✅ Unban User", callback_data="adm_unban")],
            [InlineKeyboardButton(f"🛠️ Maintenance: {m_status}", callback_data="adm_toggle_maint"),
             InlineKeyboardButton("⚡ Server Ping", callback_data="adm_ping")],
            [InlineKeyboardButton("🔄 Restart Bot", callback_data="adm_restart"),
             InlineKeyboardButton("« Main Menu", callback_data="main_menu")]
        ]
        await query.message.edit_text(admin_text, reply_markup=InlineKeyboardMarkup(admin_kb), parse_mode="Markdown")

    elif data == "adm_create_coupon":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        user_states[user_id] = 'WAITING_COUPON_DETAILS'
        await query.message.edit_text(
            "🎟️ **CREATE COUPON SETUP**\n\nFormat bhejo:\n`TIER_NAME MAX_FILES DAYS USES`\n\n💡 *Example:* `Pro 30 30 5`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]),
            parse_mode="Markdown"
        )

    elif data == "adm_give_prem":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        user_states[user_id] = 'WAITING_GIVE_PREM'
        await query.message.edit_text(
            "👑 **GIVE PREMIUM PLAN FORMAT**\n\nFormat bhejo:\n`USER_ID PLAN_NAME DAYS MAX_FILES`\n\n💡 *Example:* `8879326948 Premium 30 250`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]),
            parse_mode="Markdown"
        )

    elif data == "adm_broadcast":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        user_states[user_id] = 'WAITING_BROADCAST_MSG'
        await query.message.edit_text("📢 **BROADCAST MESSAGE**\nEnter message:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]))

    elif data == "adm_inspect":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        user_states[user_id] = 'WAITING_INSPECT_UID'
        await query.message.edit_text("🔍 **INSPECT USER**\nEnter User ID:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]))

    elif data == "adm_ban":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        user_states[user_id] = 'WAITING_BAN_USER'
        await query.message.edit_text("🚫 **BAN USER**\nEnter User ID:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]))

    elif data == "adm_unban":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        user_states[user_id] = 'WAITING_UNBAN_USER'
        await query.message.edit_text("✅ **UNBAN USER**\nEnter User ID:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]))

    elif data == "adm_tickets":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        conn = sqlite3.connect('flexyy_hosting.db')
        c = conn.cursor()
        c.execute("SELECT ticket_id, user_id, message, status FROM support_tickets WHERE status = 'Pending'")
        tickets = c.fetchall()
        conn.close()
        
        if not tickets:
            await query.message.edit_text("📨 *Koi bhi pending support ticket nahi hai.*", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]), parse_mode="Markdown")
        else:
            t_text = "📨 **PENDING SUPPORT TICKETS:**\n\n"
            for tid, tuid, msg, status in tickets:
                t_text += f"🆔 Ticket #{tid} | User: `{tuid}`\n💬 Msg: {msg}\n\n"
            await query.message.edit_text(t_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]), parse_mode="Markdown")

    elif data == "adm_toggle_maint":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        maintenance_mode = not maintenance_mode
        status_text = "Enabled 🔴" if maintenance_mode else "Disabled 🟢"
        await query.answer(f"Maintenance Mode is now {status_text}", show_alert=True)
        await button_handler(update, context)

    elif data == "adm_stats":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        conn = sqlite3.connect('flexyy_hosting.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_u = c.fetchone()[0]
        conn.close()
        total_running = sum(len(p) for p in active_processes.values())
        await query.message.edit_text(f"📊 **STATS**\n👥 Total Users: `{total_u}`\n🚀 Active Running Scripts: `{total_running}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_panel")]]), parse_mode="Markdown")

    elif data == "adm_ping":
        st = time.time()
        lat = round((time.time() - st) * 1000)
        await query.answer(f"🏓 Ultra Fast Ping! {lat}ms", show_alert=True)

    elif data == "adm_restart":
        await query.answer()
        if user_id != ADMIN_ID:
            return
        await query.message.edit_text("🔄 Restarting bot...")
        os.execv(sys.executable, ['python'] + sys.argv)

    elif data.startswith("approve_"):
        if user_id != ADMIN_ID:
            return
        parts = data.split("_", 2)
        t_user = int(parts[1])
        fname = parts[2]
        pending_path = f"pending_uploads/{t_user}/{fname}"
        user_dir = f"uploads/{t_user}"
        os.makedirs(user_dir, exist_ok=True)
        final_path = f"{user_dir}/{fname}"
        
        if os.path.exists(pending_path):
            os.rename(pending_path, final_path)
            await query.answer("✅ File Approved Successfully!", show_alert=True)
            try:
                await context.bot.send_message(t_user, f"🎉 Mubarak ho! Aapki file `{fname}` admin dwara **approve** kar di gayi hai. Ab aap 'My Files Manager' se ise start kar sakte hain!", parse_mode="Markdown")
            except:
                pass
            try:
                await query.message.edit_text(f"✅ File `{fname}` (User ID: `{t_user}`) has been **APPROVED** and user has been notified.", parse_mode="Markdown")
            except:
                pass
        else:
            await query.answer("❌ File not found in pending!", show_alert=True)

    elif data.startswith("reject_"):
        if user_id != ADMIN_ID:
            return
        parts = data.split("_", 2)
        t_user = int(parts[1])
        fname = parts[2]
        pending_path = f"pending_uploads/{t_user}/{fname}"
        if os.path.exists(pending_path):
            os.remove(pending_path)
            await query.answer("❌ File Rejected!", show_alert=True)
            try:
                await context.bot.send_message(t_user, f"❌ Afsos! Aapki file `{fname}` admin dwara **reject** kar di gayi hai.", parse_mode="Markdown")
            except:
                pass
            try:
                await query.message.edit_text(f"❌ File `{fname}` (User ID: `{t_user}`) has been **REJECTED**.", parse_mode="Markdown")
            except:
                pass

    elif data.startswith("admstart_"):
        if user_id != ADMIN_ID:
            return
        fname = data.replace("admstart_", "")
        f_path = f"uploads/admin_special/{fname}"
        if os.path.exists(f_path):
            try:
                log_path = f"{f_path}.log"
                log_file = open(log_path, "w")
                proc = subprocess.Popen(["python3" if fname.endswith('.py') else "node", f_path], stdout=log_file, stderr=log_file)
                if ADMIN_ID not in active_processes:
                    active_processes[ADMIN_ID] = {}
                active_processes[ADMIN_ID][fname] = {"proc": proc, "start_time": time.time()}
                await query.answer("Admin file started!", show_alert=True)
            except Exception as e:
                await query.answer(f"Error: {e}", show_alert=True)
        await refresh_admin_file_menu(query)

    elif data.startswith("admstop_"):
        if user_id != ADMIN_ID:
            return
        fname = data.replace("admstop_", "")
        if ADMIN_ID in active_processes and fname in active_processes[ADMIN_ID]:
            try:
                active_processes[ADMIN_ID][fname]["proc"].terminate()
            except:
                pass
            del active_processes[ADMIN_ID][fname]
            await query.answer("Admin file stopped!", show_alert=True)
        await refresh_admin_file_menu(query)

    elif data.startswith("admdel_"):
        if user_id != ADMIN_ID:
            return
        fname = data.replace("admdel_", "")
        f_path = f"uploads/admin_special/{fname}"
        try:
            if ADMIN_ID in active_processes and fname in active_processes[ADMIN_ID]:
                active_processes[ADMIN_ID][fname]["proc"].terminate()
                del active_processes[ADMIN_ID][fname]
        except:
            pass
        if os.path.exists(f_path):
            os.remove(f_path)
        await query.answer("Admin file deleted!", show_alert=True)
        await refresh_admin_file_menu(query)

    elif data.startswith("admlogs_"):
        if user_id != ADMIN_ID:
            return
        fname = data.replace("admlogs_", "")
        log_path = f"uploads/admin_special/{fname}.log"
        if os.path.exists(log_path):
            with open(log_path, "r") as lf:
                content = lf.read()[-1500:]
            if not content.strip(): content = "No logs."
            await query.message.edit_text(f"📋 **Admin Logs for `{fname}`:**\n```\n{content}\n```", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_files_menu")]]), parse_mode="Markdown")
        else:
            await query.message.edit_text("❌ No logs found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="admin_files_menu")]]), parse_mode="Markdown")

    elif data.startswith("stop_"):
        parts = data.split("_", 2)
        target_user = int(parts[1])
        fname = parts[2]
        if user_id == target_user or user_id == ADMIN_ID:
            if target_user in active_processes and fname in active_processes[target_user]:
                try: active_processes[target_user][fname]["proc"].terminate()
                except: pass
                del active_processes[target_user][fname]
                await query.answer("Stopped!", show_alert=True)
        await refresh_file_menu(query, user_id)

    elif data.startswith("start_"):
        parts = data.split("_", 2)
        target_user = int(parts[1])
        fname = parts[2]
        if user_id == target_user or user_id == ADMIN_ID:
            f_path = f"uploads/{target_user}/{fname}"
            if os.path.exists(f_path):
                try:
                    log_path = f"{f_path}.log"
                    log_file = open(log_path, "w")
                    if fname.endswith('.py'):
                        proc = subprocess.Popen(["python3", f_path], stdout=log_file, stderr=log_file)
                    elif fname.endswith('.js'):
                        proc = subprocess.Popen(["node", f_path], stdout=log_file, stderr=log_file)
                    
                    if target_user not in active_processes:
                        active_processes[target_user] = {}
                    active_processes[target_user][fname] = {"proc": proc, "start_time": time.time()}
                    await query.answer("Started Live!", show_alert=True)
                except Exception as e:
                    await query.answer(f"Error: {e}", show_alert=True)
        await refresh_file_menu(query, user_id)

    elif data.startswith("del_"):
        parts = data.split("_", 2)
        target_user = int(parts[1])
        fname = parts[2]
        if user_id == target_user or user_id == ADMIN_ID:
            f_path = f"uploads/{target_user}/{fname}"
            log_path = f"{f_path}.log"
            try:
                if target_user in active_processes and fname in active_processes[target_user]:
                    active_processes[target_user][fname]["proc"].terminate()
                    del active_processes[target_user][fname]
            except: pass
            if os.path.exists(f_path): os.remove(f_path)
            if os.path.exists(log_path): os.remove(log_path)
            await query.answer("Deleted!", show_alert=True)
        await refresh_file_menu(query, user_id)

    elif data.startswith("dl_"):
        parts = data.split("_", 2)
        target_user = int(parts[1])
        fname = parts[2]
        f_path = f"uploads/{target_user}/{fname}"
        if os.path.exists(f_path):
            await context.bot.send_document(chat_id=user_id, document=open(f_path, "rb"), caption=f"📥 Backup")
            await query.answer("Sent!", show_alert=True)

    elif data.startswith("logs_"):
        await query.answer()
        parts = data.split("_", 2)
        target_user = int(parts[1])
        fname = parts[2]
        log_path = f"uploads/{target_user}/{fname}.log"
        if os.path.exists(log_path):
            with open(log_path, "r") as lf:
                content = lf.read()[-1500:]
            if not content.strip(): content = "No logs."
            await query.message.edit_text(f"📋 **Logs:**\n```\n{content}\n```", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="check_files")]]), parse_mode="Markdown")

    elif data == "help":
        await query.answer()
        await query.message.edit_text(f"🆘 Support: {OWNER_USERNAME}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="main_menu")]]))

    elif data == "main_menu":
        await query.answer()
        user_states.pop(user_id, None)
        await start(update, context)

async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    current_state = user_states.get(user_id)

    if current_state == 'WAITING_FOR_COUPON':
        user_states.pop(user_id, None)
        coupon = use_coupon_db(text)
        if coupon:
            update_user_db(user_id, coupon["tier"], coupon["max_files"], coupon["days"])
            await update.message.reply_text(f"✅ *Success!* Coupon redeemed. Tier upgraded to `{coupon['tier']}`!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ *Invalid or Expired Coupon!*", parse_mode="Markdown")
        await start(update, context)
        return

    elif current_state == 'WAITING_COUPON_DETAILS' and user_id == ADMIN_ID:
        user_states.pop(user_id, None)
        try:
            parts = text.split()
            tier_name = parts[0]
            max_f = int(parts[1])
            days = int(parts[2])
            uses = int(parts[3])
            code = generate_random_coupon()
            save_coupon_db(code, tier_name, max_f, days, uses)
            await update.message.reply_text(f"✅ **Coupon Created Successfully!**\n\nCode: `{code}`\nTier: `{tier_name}`\nMax Files: `{max_f}`\nDays: `{days}`\nUses: `{uses}`", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Format error! Use: `TIER MAX_FILES DAYS USES`\nError: {e}")
        await start(update, context)
        return

    elif current_state == 'WAITING_GIVE_PREM' and user_id == ADMIN_ID:
        user_states.pop(user_id, None)
        try:
            parts = text.split()
            target_uid = int(parts[0])
            plan_name = parts[1]
            days = int(parts[2])
            max_files = int(parts[3])
            
            update_user_db(target_uid, plan_name, max_files, days)
            await update.message.reply_text(
                f"✅ **Plan Assigned Successfully!**\n\n"
                f"🆔 User ID: `{target_uid}`\n"
                f"💎 Plan: `{plan_name}`\n"
                f"⏳ Duration: `{days} Days`\n"
                f"📂 File Limit: `{max_files} Files`",
                parse_mode="Markdown"
            )
            try:
                await context.bot.send_message(target_uid, f"🎉 Aapka account admin dwara `{plan_name}` ({max_files} files) par upgrade kar diya gaya hai!", parse_mode="Markdown")
            except:
                pass
        except Exception as e:
            await update.message.reply_text(f"❌ Format Error! Use:\n`USER_ID PLAN DAYS MAX_FILES`\nError: {e}", parse_mode="Markdown")
        await start(update, context)
        return

    elif current_state == 'WAITING_BROADCAST_MSG' and user_id == ADMIN_ID:
        user_states.pop(user_id, None)
        conn = sqlite3.connect('flexyy_hosting.db')
        c = conn.cursor()
        c.execute("SELECT user_id FROM users")
        all_u = c.fetchall()
        conn.close()
        for u in all_u:
            try: await context.bot.send_message(u[0], f"📢 {text}")
            except: pass
        await update.message.reply_text("✅ Broadcasted to all users!")
        await start(update, context)
        return

    elif current_state == 'WAITING_INSPECT_UID' and user_id == ADMIN_ID:
        user_states.pop(user_id, None)
        try:
            target_uid = int(text)
            u_info = get_user_db(target_uid)
            files = get_user_files(target_uid)
            await update.message.reply_text(
                f"🔍 **USER INSPECTION REPORT**\n\n"
                f"🆔 User ID: `{target_uid}`\n"
                f"💎 Tier: `{u_info['tier']}`\n"
                f"📂 Max Files: `{u_info['max_files']}`\n"
                f"⏳ Expiry: `{u_info['expiry']}`\n"
                f"🚫 Banned: `{u_info['is_banned']}`\n"
                f"📁 Uploaded Files: `{len(files)}` ({', '.join(files) if files else 'None'})",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error inspecting user: {e}")
        await start(update, context)
        return

    elif current_state == 'WAITING_BAN_USER' and user_id == ADMIN_ID:
        user_states.pop(user_id, None)
        try:
            target_uid = int(text)
            set_ban_db(target_uid, 1)
            await update.message.reply_text(f"🚫 User `{target_uid}` successfully banned!", parse_mode="Markdown")
        except:
            await update.message.reply_text("❌ Invalid User ID!")
        await start(update, context)
        return

    elif current_state == 'WAITING_UNBAN_USER' and user_id == ADMIN_ID:
        user_states.pop(user_id, None)
        try:
            target_uid = int(text)
            set_ban_db(target_uid, 0)
            await update.message.reply_text(f"✅ User `{target_uid}` successfully unbanned!", parse_mode="Markdown")
        except:
            await update.message.reply_text("❌ Invalid User ID!")
        await start(update, context)
        return

    elif current_state == 'WAITING_SUPPORT_MSG':
        user_states.pop(user_id, None)
        conn = sqlite3.connect('flexyy_hosting.db')
        c = conn.cursor()
        c.execute("INSERT INTO support_tickets (user_id, message) VALUES (?, ?)", (user_id, text))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"📩 **NEW SUPPORT TICKET**\n\n👤 From: `{user_id}` (@{update.effective_user.username or 'No Username'})\n💬 Message:\n{text}",
                parse_mode="Markdown"
            )
            await update.message.reply_text("✅ Aapka message support team tak pahunch gaya hai!")
        except:
            await update.message.reply_text("❌ Message send karne mein error aayi.")
        await start(update, context)
        return

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    document = update.message.document
    file_name = document.file_name
    
    if not (file_name.endswith('.py') or file_name.endswith('.js')):
        await update.message.reply_text("❌ Sirf .py aur .js files allowed hain.")
        return

    current_state = user_states.get(user_id)

    if current_state == 'WAITING_FOR_ADMIN_FILE' and user_id == ADMIN_ID:
        user_states.pop(user_id, None)
        os.makedirs("uploads/admin_special", exist_ok=True)
        file = await context.bot.get_file(document.file_id)
        file_path = f"uploads/admin_special/{file_name}"
        await file.download_to_drive(file_path)
        await update.message.reply_text("✅ Admin Special File successfully upload ho gayi!", parse_mode="Markdown")
        await start(update, context)
        return

    os.makedirs(f"pending_uploads/{user_id}", exist_ok=True)
    file = await context.bot.get_file(document.file_id)
    file_path = f"pending_uploads/{user_id}/{file_name}"
    await file.download_to_drive(file_path)

    user_states.pop(user_id, None)
    await update.message.reply_text("⏳ Aapki file upload ho gayi hai aur **admin approval** ke liye bhej di gayi hai. Approve hote hi aapko notify kar diya jayega!", parse_mode="Markdown")
    
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}_{file_name}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}_{file_name}")]
    ])
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"📥 **NEW FILE PENDING APPROVAL**\n\n👤 User ID: `{user_id}`\n📂 File Name: `{file_name}`",
            reply_markup=admin_kb,
            parse_mode="Markdown"
        )
    except:
        pass
    
    await start(update, context)

def auto_restart_saved_scripts():
    if os.path.exists("uploads"):
        for user_folder in os.listdir("uploads"):
            if user_folder.isdigit():
                user_id = int(user_folder)
                user_dir = f"uploads/{user_id}"
                if os.path.isdir(user_dir):
                    for fname in os.listdir(user_dir):
                        if fname.endswith('.py') or fname.endswith('.js'):
                            f_path = os.path.join(user_dir, fname)
                            try:
                                log_path = f"{f_path}.log"
                                log_file = open(log_path, "w")
                                proc = subprocess.Popen(["python3" if fname.endswith('.py') else "node", f_path], stdout=log_file, stderr=log_file)
                                if user_id not in active_processes:
                                    active_processes[user_id] = {}
                                active_processes[user_id][fname] = {"proc": proc, "start_time": time.time()}
                            except Exception as e:
                                logger.error(f"Failed to auto-start {fname}: {e}")
    admin_dir = "uploads/admin_special"
    if os.path.exists(admin_dir):
        for fname in os.listdir(admin_dir):
            if fname.endswith('.py') or fname.endswith('.js'):
                f_path = os.path.join(admin_dir, fname)
                try:
                    log_path = f"{f_path}.log"
                    log_file = open(log_path, "w")
                    proc = subprocess.Popen(["python3" if fname.endswith('.py') else "node", f_path], stdout=log_file, stderr=log_file)
                    if ADMIN_ID not in active_processes:
                        active_processes[ADMIN_ID] = {}
                    active_processes[ADMIN_ID][fname] = {"proc": proc, "start_time": time.time()}
                except Exception as e:
                    logger.error(f"Failed to auto-start admin file {fname}: {e}")

async def run_bot():
    auto_restart_saved_scripts()
    
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_input))

    print("⚡ Pythoneer Hosting Bot v8.4 is fully active...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    # Keep running
    stop_signal = asyncio.Event()
    await stop_signal.wait()

def main():
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
