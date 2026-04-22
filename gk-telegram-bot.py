import logging
import re
import sqlite3
import asyncio
import sys
import os
import time
from telegram import Update, Poll, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    PollAnswerHandler,
)

# --- WINDOWS SPEED FIX (Harmless on Linux/Render) ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- CONFIGURATION ---
TOKEN = "8653449129:AAGGbWi7UxLcGRqCgi3qIziADuMhMymP5y0"
OWNER_ID = 6527942155

# Spam Words
BANNED_WORDS = ["scam", "fraud", "casino", "illegal", "bitcoin", "gali", "badword1", "badword2", "badword3", "join fast", "investment"]

# Subjects aur unki files mapping
SUBJECTS_FILES = {
    "physics": "physics.txt",
    "chemistry": "chemistry.txt",
    "gk": "gk.txt"
}

ACTIVE_POLLS = {}

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("quiz_scores.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            chat_id INTEGER,
            user_id INTEGER,
            full_name TEXT,
            points INTEGER DEFAULT 0,
            last_time REAL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    # Purane database ko update karne ke liye try-except block
    try:
        cursor.execute("ALTER TABLE scores ADD COLUMN last_time REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Column pehle se hai

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_state (
            chat_id INTEGER PRIMARY KEY,
            current_index INTEGER DEFAULT 0,
            subject TEXT DEFAULT 'gk'
        )
    """)
    conn.commit()
    conn.close()

def get_quiz_state(chat_id):
    conn = sqlite3.connect("quiz_scores.db")
    cursor = conn.cursor()
    cursor.execute("SELECT current_index, subject FROM quiz_state WHERE chat_id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    if result: return result[0], result[1]
    return 0, 'gk'

def update_quiz_state(chat_id, new_index, subject=None):
    conn = sqlite3.connect("quiz_scores.db")
    cursor = conn.cursor()
    if subject is None:
        _, subject = get_quiz_state(chat_id)
    cursor.execute("""
        INSERT INTO quiz_state (chat_id, current_index, subject) 
        VALUES (?, ?, ?) 
        ON CONFLICT(chat_id) DO UPDATE SET current_index = ?, subject = ?
    """, (chat_id, new_index, subject, new_index, subject))
    conn.commit()
    conn.close()

def reset_scores(chat_id):
    conn = sqlite3.connect("quiz_scores.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM scores WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def add_points(chat_id, user_id, full_name, points_to_add):
    conn = sqlite3.connect("quiz_scores.db")
    cursor = conn.cursor()
    current_time = time.time() # Speed track karne ke liye
    
    cursor.execute("SELECT points FROM scores WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    data = cursor.fetchone()
    if data:
        new_points = data[0] + points_to_add
        cursor.execute("UPDATE scores SET points = ?, full_name = ?, last_time = ? WHERE chat_id = ? AND user_id = ?", 
                       (new_points, full_name, current_time, chat_id, user_id))
    else:
        cursor.execute("INSERT INTO scores (chat_id, user_id, full_name, points, last_time) VALUES (?, ?, ?, ?, ?)", 
                       (chat_id, user_id, full_name, points_to_add, current_time))
    conn.commit()
    conn.close()

def get_top_scorers(chat_id):
    conn = sqlite3.connect("quiz_scores.db")
    cursor = conn.cursor()
    # Pura game change: Jiske marks zyada wo upar. Agar marks same hain, toh jisne jaldi jawab diya (last_time ASC) wo upar!
    cursor.execute("SELECT full_name, points FROM scores WHERE chat_id = ? ORDER BY points DESC, last_time ASC LIMIT 10", (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- FILE READING ---
def load_questions(subject):
    file_name = SUBJECTS_FILES.get(subject, "gk.txt")
    questions = []
    if not os.path.exists(file_name):
        return []
    
    try:
        with open(file_name, "r", encoding="utf-8-sig") as f:
            for line in f:
                if not line.strip() or line.startswith("#"): continue
                parts = line.strip().split("|")
                if len(parts) == 6:
                    q_text = parts[0].strip()
                    options = [p.strip() for p in parts[1:5]]
                    try:
                        correct_idx = int(parts[5].strip()) - 1
                        questions.append({
                            "q": q_text,
                            "options": options,
                            "correct": correct_idx
                        })
                    except Exception as inner_e:
                        print(f"Error parsing index in line: {line}. Error: {inner_e}")
    except Exception as e:
        print(f"File Error [{file_name}]: {e}")
    return questions

# --- PERMISSION CHECK ---
async def is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user_id = update.effective_user.id
    if user_id == OWNER_ID: return True
    if chat.type == 'private': return True
    try:
        member = await context.bot.get_chat_member(chat.id, user_id)
        if member.status in ['creator', 'administrator']: return True
    except: pass
    return False

# --- MODERATION LOGIC ---
async def moderate_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    text = update.message.text.lower()
    user = update.message.from_user
    chat = update.effective_chat
    if chat.type == 'private' or user.id == OWNER_ID: return

    if re.search(r"(https?://|t\.me/|www\.|bit\.ly|\.com|\.in|\.net)", text):
        try:
            await update.message.delete()
            warning = await context.bot.send_message(chat_id=chat.id, text=f"🚫 {user.first_name}, Links allowed nahi hain!")
            await asyncio.sleep(5)
            await warning.delete()
        except: pass
        return

    if any(word in text for word in BANNED_WORDS):
        try:
            await update.message.delete()
            warning = await context.bot.send_message(chat_id=chat.id, text=f"⚠️ {user.first_name}, Gali ya Spam allowed nahi hai!")
            await asyncio.sleep(5)
            await warning.delete()
        except: pass

# --- QUIZ LOGIC ---
async def send_sequential_quiz(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    current_idx, subject = get_quiz_state(chat_id)
    questions = load_questions(subject)
    
    if not questions:
        await context.bot.send_message(chat_id, f"⚠️ {SUBJECTS_FILES.get(subject)} file khali hai, format galat hai, ya mili nahi!")
        current_jobs = context.job_queue.get_jobs_by_name(f"quiz_{chat_id}")
        for job in current_jobs: job.schedule_removal()
        return

    # Jab saare sawal khatam ho jayein toh leaderboard dikhao aur quiz stop kardo
    if current_idx >= len(questions):
        current_jobs = context.job_queue.get_jobs_by_name(f"quiz_{chat_id}")
        for job in current_jobs: job.schedule_removal()
        
        stop_jobs = context.job_queue.get_jobs_by_name(f"stop_{chat_id}")
        for job in stop_jobs: job.schedule_removal()
        
        top_users = get_top_scorers(chat_id)
        msg = "🏁 ALL QUESTIONS COMPLETED! 🏁\n\n🏅 Final Leaderboard:\n"
        if top_users:
            for idx, (name, points) in enumerate(top_users, 1):
                msg += f"{idx}. {name} -> {points} Marks\n"
        else:
            msg += "Kisi ne sahi jawab nahi diya."
        await context.bot.send_message(chat_id, msg)
        return
    
    question_data = questions[current_idx]
    
    try:
        total_q = len(questions)
        q_num = current_idx + 1
        sub_title = subject.capitalize()
        
        message = await context.bot.send_poll(
            chat_id=chat_id,
            question=f"🎯 {sub_title} Quiz {q_num}/{total_q} 🎯\n\n{question_data['q']}",
            options=question_data['options'],
            type=Poll.QUIZ,
            correct_option_id=question_data['correct'],
            is_anonymous=False,
            open_period=8 # 8 Second Timer
        )
        ACTIVE_POLLS[message.poll.id] = {'correct': question_data['correct'], 'chat_id': chat_id}
        update_quiz_state(chat_id, current_idx + 1, subject)
        
    except Exception as e:
        print(f"Quiz Error in Chat {chat_id}: {e}")
        await context.bot.send_message(chat_id, f"⚠️ Question bhejne mein dikkat aayi (Error: {e}). \nKripya apne .txt file ka format check karein (Poll option 100 character se chota hona chahiye).")
        current_jobs = context.job_queue.get_jobs_by_name(f"quiz_{chat_id}")
        for job in current_jobs: job.schedule_removal()

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = answer.user.id
    user_name = answer.user.full_name
    selected_option = answer.option_ids[0]

    if poll_id in ACTIVE_POLLS:
        poll_info = ACTIVE_POLLS[poll_id]
        correct_option = poll_info['correct']
        chat_id = poll_info['chat_id']
        
        if selected_option == correct_option:
            add_points(chat_id, user_id, user_name, 2)

async def stop_competition_auto(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    current_jobs = context.job_queue.get_jobs_by_name(f"quiz_{chat_id}")
    for job in current_jobs: job.schedule_removal()
    
    top_users = get_top_scorers(chat_id)
    msg = "🏁 COMPETITION OVER! (Time Up) 🏁\n\n🏅 Final Leaderboard:\n"
    if top_users:
        for idx, (name, points) in enumerate(top_users, 1):
            msg += f"{idx}. {name} -> {points} Marks\n"
    else:
        msg += "Kisi ne sahi jawab nahi diya."
    await context.bot.send_message(chat_id, msg)

# --- COMMANDS ---
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "👋 Welcome to the Ultimate Quiz Bot!\n\n"
        "Main aapko Physics, Chemistry aur General Knowledge sikhne me madad karunga.\n\n"
        "📜 **My Commands:**\n"
        "🔹 /startcomp - Start a new quiz competition\n"
        "🔹 /stop - Stop an ongoing quiz\n"
        "🔹 /resetq - Reset question sequence to 1\n\n"
        "Niche command pe click karein ya menu se select karein! 🚀"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def show_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update, context):
        await update.message.reply_text("🚫 Group mein sirf Admins aur Owner hi Quiz start kar sakte hain.")
        return

    chat_id = update.effective_chat.id
    if context.job_queue.get_jobs_by_name(f"quiz_{chat_id}"):
        await update.message.reply_text("⚠️ Is chat mein competition pehle se chal raha hai! Pehle /stop karein.")
        return

    keyboard = [
        [InlineKeyboardButton("⚛️ Physics", callback_data="start_physics")],
        [InlineKeyboardButton("🧪 Chemistry", callback_data="start_chemistry")],
        [InlineKeyboardButton("🌍 Gen Knowledge", callback_data="start_gk")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📚 **Choose a Subject to Start Quiz:**\n\n(Aapka purana score reset ho jayega)", reply_markup=reply_markup, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    chat = query.message.chat

    is_admin = False
    if chat.type == 'private' or user_id == OWNER_ID:
        is_admin = True
    else:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ['creator', 'administrator']: is_admin = True
        except: pass

    if not is_admin:
        await query.answer("🚫 Sirf admin yeh button use kar sakte hain!", show_alert=True)
        return

    data = query.data
    if data.startswith("start_"):
        subject = data.split("_")[1]
        
        # JAISE HI NAYA QUIZ START HOGA, PURANE SCORES DELETE HO JAYENGE (FRESH SCOREBOARD)
        reset_scores(chat_id)
        
        current_idx, _ = get_quiz_state(chat_id)
        update_quiz_state(chat_id, current_idx, subject)
        
        await query.edit_message_text(f"🚀 {subject.capitalize()} COMPETITION START! 🚀\n⏱️ Duration: 10 Minutes\n⚡ Har 10 Second me Naya Sawal\n\nTaiyar ho jao! 🏁")
        
        # Scheduling the Quiz
        context.job_queue.run_repeating(send_sequential_quiz, interval=10, first=2, chat_id=chat_id, name=f"quiz_{chat_id}")
        context.job_queue.run_once(stop_competition_auto, when=600, chat_id=chat_id, name=f"stop_{chat_id}")

async def reset_question_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update, context):
        await update.message.reply_text("🚫 Not authorized.")
        return
    chat_id = update.effective_chat.id
    update_quiz_state(chat_id, 0)
    await update.message.reply_text("✅ Sequence reset to Question 1.")

async def stop_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update, context):
        await update.message.reply_text("🚫 Group mein sirf Admins rok sakte hain.")
        return
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(f"quiz_{chat_id}")
    if not jobs:
        await update.message.reply_text("⚠️ Abhi koi quiz nahi chal raha.")
        return
    for job in jobs: job.schedule_removal()
    
    top_users = get_top_scorers(chat_id)
    msg = "🛑 Competition manually rok diya gaya.\n\n🏅 Current Leaderboard:\n"
    if top_users:
        for idx, (name, points) in enumerate(top_users, 1): msg += f"{idx}. {name} -> {points} Marks\n"
    else:
        msg += "Kisi ne point nahi banaya."
    await update.message.reply_text(msg)

async def setup_commands(application: Application):
    commands = [
        BotCommand("start", "Welcome message dekhein"),
        BotCommand("startcomp", "Quiz competition start karein"),
        BotCommand("stop", "Current quiz ko stop karein"),
        BotCommand("resetq", "Question sequence reset karein")
    ]
    await application.bot.set_my_commands(commands)

# --- MAIN RUNNER ---
def main():
    init_db()
    print("🚀 Bot starting on Cloud Server...")
    
    for sub, file in SUBJECTS_FILES.items():
        if not os.path.exists(file): print(f"⚠️ Warning: '{file}' nahi mili!")
        else: print(f"✅ '{file}' loaded.")

    app = Application.builder().token(TOKEN).post_init(setup_commands).build()

    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(CommandHandler("startcomp", show_quiz_menu)) 
    app.add_handler(CommandHandler("stop", stop_now))
    app.add_handler(CommandHandler("resetq", reset_question_number))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, moderate_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    print("✅ Bot Live Hai! Telegram par ja kar /start dabayein.")
    app.run_polling()

if __name__ == "__main__":
    main()
