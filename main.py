#!/usr/bin/env python3
"""
AI Teaching Assistant – WhatsApp‑Web edition (Python 3.10+)

▪ Google Sheets for course plan + student roster
▪ Gemini (google‑generativeai) for answers
▪ WhatsApp Web via WPP‑Whatsapp

Run once → scan the QR code → session is cached for future runs.
"""

import os
import textwrap
import time
from datetime import datetime

import asyncio
import dotenv
import gspread
from google.oauth2 import service_account
import google.generativeai as gemini
from WPP_Whatsapp import Create                               # pip install WPP-Whatsapp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ───────────────────────  ENV & CONSTANTS  ────────────────────────
dotenv.load_dotenv(".env", override=False)

GOOGLE_SHEET_ID  = os.environ["GOOGLE_SHEET_ID"]
COURSE_SHEET     = os.getenv("COURSE_PLAN_SHEET_NAME", "Course Plan")
STUDENT_SHEET    = os.getenv("STUDENT_SHEET_NAME",     "Student")
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
SA_KEY_PATH      = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]      # path or JSON string
WHATSAPP_SESSION = os.getenv("WHATSAPP_SESSION", "classroom")

SYSTEM_PROMPT = textwrap.dedent("""
You are a school *{SUBJECT}* educator for a classroom. Design an Universal design 
learning topic recap for class *{CLASS}* student explaining the *{TOPIC}*
which has been taught by *{TEACHER}* in the class. Tailer the lession for an 
inquiry based science class and include an engaging real world analogy. 
You can make the learning visual wherever relevant to explain the topic. 
You should ensure that you are grounded in curriculum. Answers questions *only* about today’s
lesson topic.

- Do not use Markdown formatting. Just use plain text with asterisks (e.g., *important*) for emphasis.
- When emphasizing words, wrap them in single asterisks like *this*, not double asterisks.

Instructions:
- After your first explanation, send a follow-up message asking the student if they would like examples or have any doubts.
- Send a follow-up message to the student asking if they need a recap or have any doubts.
- Answer questions related to today’s topic with clear, concise explanations.
- Politely decline questions about unrelated topics.
- If the student sends message which doesn't contain any words, respond with:
  "Please send your question in words so I can help you better 😊

  
✅ **You SHOULD:**
- Explain key points simply using analogies and plain language.
- Be conversational: speak directly to one student (use *you*, not *everyone*).
- Give bullet-point recaps if asked for a summary.
- Encourage curiosity and gently guide the student if they’re confused.
- Stick to factual and educational content.
- Handle messages with only emojis or no text by replying:
  "Please send your question in words so I can help you better 😊"


❌ **You SHOULD NOT:**
- Do not answer questions unrelated to the topic. Instead reply:
  "This question is about a different topic. Please ask about today’s topic: {TOPIC}."
- Do not use complex academic terms without explanation.
- Do not mention AI, Gemini, or that you're a language model.
- Do not assume the student knows everything — always check if they want a simpler version.
- Do not answer for other subjects or days.
- Avoid giving opinions, emotional support, or life advice.
- Do not ask for any personal information from the student.
- Avoid sensitive topics or triggering content.


Audience:
- You are speaking directly to one student in a private message.
- Be friendly, warm, and conversational — use "you" instead of "everyone".
- Assume they are a student who may need simplified explanations.

Today’s topic is: *{TOPIC}*
Only answer questions about today’s topic.

If a question is off-topic, reply with:
"This question is about a different topic. Please ask about today’s topic: {TOPIC}."


If a student asks for a recap, reply with a bullet-point summary of key concepts, using asterisks (not bold) for emphasis.

At the end of every response, show:
📚 *{TOPIC}*  
📘 *{SUBJECT}*
""").strip()


# ───────────────────────  GOOGLE SHEETS  ───────────────────────────
def make_gspread():
    """Return an authenticated gspread client."""
    if SA_KEY_PATH.strip().startswith("{"):
        creds = service_account.Credentials.from_service_account_info(
            eval(SA_KEY_PATH),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            SA_KEY_PATH,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
    return gspread.authorize(creds)

def today_topic(gc):
    """Return today's lesson dict or None."""
    doc   = gc.open_by_key(GOOGLE_SHEET_ID)
    sheet = doc.worksheet(COURSE_SHEET)
    today = datetime.now().strftime("%Y-%m-%d")

    for row in sheet.get_all_records():
        if str(row.get("Schedule Date")) == today:
            return {
                "topic":   row.get("Topic"),
                "class":   row.get("Class"),
                "teacher": row.get("Teacher"),
                "subject": row.get("Subject")
            }
    return None

def students_for_class(gc, class_name):
    """List of dicts: {name, phone} for the given class."""
    doc   = gc.open_by_key(GOOGLE_SHEET_ID)
    sheet = doc.worksheet(STUDENT_SHEET)
    return [
        {
            "name" : r["Student Name"],
            "phone": str(r["Whatsapp Number"]).lstrip("+"),  # digits only
        }
        for r in sheet.get_all_records()
        if r["Class"] == class_name
    ]

# ───────────────────────────  GEMINI  ──────────────────────────────
gemini.configure(api_key=GEMINI_API_KEY)

GEN_CONFIG = gemini.GenerationConfig(max_output_tokens=4096)

def new_chat(topic: str,subject: str, teacher: str, clas: str):
    # prompt = SYSTEM_PROMPT.replace("{TOPIC}", topic)
    prompt = SYSTEM_PROMPT.format(
        TOPIC=topic,
        SUBJECT=subject,
        TEACHER=teacher,
        CLASS=clas
    )

    return gemini.GenerativeModel("gemini-2.5-pro").start_chat(
        history=[
            {"role": "user",  "parts": [prompt]},
            {"role": "model", "parts": ["Understood. I only answer questions about today's topic."]},
        ]
    )


# ──────────────────────────  WHATSAPP  ─────────────────────────────
creator = Create(session=WHATSAPP_SESSION)   # opens Chrome on first run
client  = creator.start()

print("[⏳ Waiting for WhatsApp login…]")
while not client.isLoggedIn:
    time.sleep(1)
print("[✅ WhatsApp logged in]")

# One Gemini chat per student (in‑memory, resets when script restarts)
chat_pool = {}

async def push_daily_summary():
    gc   = make_gspread()
    info = today_topic(gc)
    if not info:
        print("No class scheduled today — skipping push.")
        return

    topic, clas, teacher = info["topic"], info["class"], info["teacher"]
    roster = students_for_class(gc, clas)

    for stu in roster:
        jid  = f"91{stu['phone']}"
        body = (
            f"👋 Hello {stu['name']},\n\n"
            f"Today in class *{clas}* with {teacher}, in mathematics we covered:\n"
            f"📚 *{topic}*\n\n"
            "Need a recap or have doubts?\n"
            "Just reply here!"
        )
        try:
            client.sendText(jid, body)
            print(f"[Push] {stu['name']} ({stu['phone']})")
        except Exception as e:
            print(f"[Push‑ERR] {stu['phone']}: {e}")

def incoming_listener(message):
    """Handle private incoming messages."""
    if not message or message.get("isGroupMsg"):
        return

    jid  = message["from"]                       # '91xxxxxxxxxx@c.us'
    text = message.get("body", "").strip()

    gc   = make_gspread()
    info = today_topic(gc)
    if not info:
        client.reply(jid, "No class today — enjoy your break 🎉", message["id"])
        return

    topic, clas, teacher, subject = info["topic"], info["class"], info["teacher"], info["subject"]

    # Gemini chat instance per phone
    chat = chat_pool.setdefault(jid, new_chat(topic,subject, teacher, clas))


    # -------------------------------------------------- Testing
    def format_history(chat_session):
        """Return the entire thread as a single string."""
        lines = []
        for turn in chat_session.history:   # list[Content] proto objects
            role  = turn.role               # "user" | "model"
            # combine all .text parts (there can be multiple if streaming)
            body  = "\n".join(
                getattr(p, "text", str(p)) for p in turn.parts
            )
            lines.append(f"{role.upper()} ----> {body}")
        return "\n\n".join(lines)
    full_convo = format_history(chat) 
    print(f"\n=========== Conversation with {jid} ===============\n")      # <= string of whole dialogue
    print(f"\n{full_convo}\n")
    print(f"\n===================================================\n")  
    # --------------------------------------------------


    try:
        response = chat.send_message(text, generation_config=GEN_CONFIG)
        answer   = response.text
    except Exception as e:
        print(f"[Gemini‑ERR] {e}")
        answer = "Sorry, I hit a snag. Please try again?"


    client.reply(jid, answer, message["id"])

# Register the message callback
creator.client.onMessage(incoming_listener)

# ───────────────────────  SCHEDULER & LOOP  ────────────────────────
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

scheduler = AsyncIOScheduler(event_loop=loop, timezone="Asia/Kolkata")
scheduler.add_job(push_daily_summary, "cron", hour=16, minute=41)  # 4 PM IST

loop.run_until_complete(push_daily_summary())
scheduler.start()
print("Scheduler started — running forever (CTRL+C to stop).")

try:
    loop.run_forever()
except KeyboardInterrupt:
    print("\nShutting down…")
finally:
    scheduler.shutdown(wait=False)
    creator.close()
