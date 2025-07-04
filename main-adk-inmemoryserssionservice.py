

import asyncio
import inspect
import os
import textwrap
import time
from datetime import datetime
from typing import Any, Dict

import dotenv
import gspread
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from google.adk.agents import LlmAgent
from google.adk.memory import InMemoryMemoryService
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import load_memory
from google.genai.types import Content, Part, GenerateContentConfig
from google.oauth2 import service_account
from WPP_Whatsapp import Create

# ────────────────────────── ENV & CONSTANTS ─────────────────────────
dotenv.load_dotenv(".env", override=False)

GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
COURSE_SHEET = os.getenv("COURSE_PLAN_SHEET_NAME", "Course Plan")
STUDENT_SHEET = os.getenv("STUDENT_SHEET_NAME", "Student")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SA_KEY_PATH = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
WHATSAPP_SESSION = os.getenv("WHATSAPP_SESSION", "classroom")

APP_NAME = "ai_teaching_assistant"
MODEL_NAME = "gemini-2.5-pro"
USER_ID_HEADER = "91"

SYSTEM_PROMPT_TMPL = textwrap.dedent(
"""
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
- When asking for recap then only send explanation of the full topic, with now example.
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
- Use simple english words to explain.
- Encourage curiosity and gently guide the student if they’re confused.
- Stick to factual and educational content.
- Handle messages with only emojis or no text by replying:
  "Please send your question in words so I can help you better 😊"


❌ **You SHOULD NOT:**
- Do not answer questions unrelated to the topic. Instead reply:
  "This question is about a different topic. Please ask about today’s topic: {TOPIC}."
- Do not use complex academic terms without explanation.
- Do not use complex english words.
- Do not give example first when student want explanation, always give explanation of the first 
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
"""
).strip()

# ────────────────────────── GOOGLE SHEETS ───────────────────────────

def make_gspread():
    if SA_KEY_PATH.strip().startswith("{"):
        creds = service_account.Credentials.from_service_account_info(
            eval(SA_KEY_PATH), scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            SA_KEY_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    return gspread.authorize(creds)

def today_topic(gc):
    sheet = gc.open_by_key(GOOGLE_SHEET_ID).worksheet(COURSE_SHEET)
    today = datetime.now().strftime("%Y-%m-%d")
    for row in sheet.get_all_records():
        if str(row.get("Schedule Date")) == today:
            return {
                "topic": row.get("Topic"),
                "class": row.get("Class"),
                "teacher": row.get("Teacher"),
                "subject": row.get("Subject"),
            }
    return None

def students_for_class(gc, class_name):
    sheet = gc.open_by_key(GOOGLE_SHEET_ID).worksheet(STUDENT_SHEET)
    return [
        {"name": r["Student Name"], "phone": str(r["Whatsapp Number"]).lstrip("+")}
        for r in sheet.get_all_records() if r["Class"] == class_name
    ]

# ─────────────────────────── AGENT SETUP ────────────────────────────
gc = make_gspread()
data = today_topic(gc)
topic, class_, teacher, subject = (
            data["topic"], data["class"], data["teacher"], data["subject"]
        )
system_prompt = SYSTEM_PROMPT_TMPL.format(
    TOPIC=topic, SUBJECT=subject, TEACHER=teacher, CLASS=class_
)

# memory_service = InMemoryMemoryService()
session_service = InMemorySessionService()



model = Gemini(model_name=MODEL_NAME, api_key=GEMINI_API_KEY)

gen_cfg = GenerateContentConfig(
    max_output_tokens=4096,     # ← set the hard limit you want
    temperature=0.7,           # (optional) any other sampling knobs …
    top_p=0.9,
)

agent = LlmAgent(
    name="ai_teaching_assistant",
    model=model,
    instruction=system_prompt,
    generate_content_config=gen_cfg,
    # tools=[load_memory],
)

runner = Runner(
    agent=agent,
    app_name=APP_NAME,
    session_service=session_service,
    # memory_service=memory_service,
)

# ────────────────────────── WHATSAPP LOGIN ──────────────────────────
creator = Create(session=WHATSAPP_SESSION)
client = creator.start()
print("[⏳ Waiting for WhatsApp login…]")
while not client.isLoggedIn:
    time.sleep(1)
print("[✅ WhatsApp logged in]")

loop = asyncio.get_event_loop()


# ───────────────────────── DAILY PUSH SUMMARY ───────────────────────

async def push_daily_summary():
    gc = make_gspread()
    info = today_topic(gc)
    if not info:
        print("No class scheduled today — skipping push.")
        return

    topic, class_, teacher, subject = (
            data["topic"], data["class"], data["teacher"], data["subject"]
        )
    for stu in students_for_class(gc, class_):
        jid = USER_ID_HEADER + stu["phone"] + "@c.us"
        body = (
            f"👋 Hello {stu['name']},\n\n"
            f"Today in class *{class_}* with {teacher}, we covered:\n"
            f"📚 *{topic}*\n\n"
            "Need a recap or have doubts?\nJust reply here!"
        )
        try:
            stateful_session = session_service.create_session(
                app_name=APP_NAME,
                user_id=jid,
                session_id=jid,
                state={
                    "todays topic": topic,
                    "class": class_,
                    "teacher": teacher,
                    "subject": subject
                }
            )
            print("state ful session ----> ", stateful_session)
            client.sendText(jid, body)
            print(f"[Push] {stu['name']} ({stu['phone']})")
        except Exception as e:
            print(f"[Push‑ERR] {stu['phone']}: {e}")

# ─────────────────────────── MESSAGE HANDLER ─────────────────────────

async def handle_message(message):
    if not message or message.get("isGroupMsg"):
        return

    jid = message["from"]
    text = (message.get("body") or "").strip()
    if not text:
        return

    try:
        gc = make_gspread()
        info = today_topic(gc)
        if not info:
            client.reply(jid, "No class today — enjoy your break 🎉", message["id"])
            return

        topic, class_, teacher, subject = (
            info["topic"], info["class"], info["teacher"], info["subject"]
        )


        messages = Content(parts=[Part(text=text)], role="user")
        
        history = session_service.get_session(app_name=APP_NAME, user_id=jid, session_id=jid)
        print(f"==================================================")
        print(f"Message history: {history}")
        print(f"==================================================")
        reply_text = "I couldn't generate a response. Please try again."
        try:
            for event in runner.run(
                user_id=jid,
                session_id=jid,
                new_message=messages,
            ):
                if (
                    event.is_final_response()
                    and event.content
                    and event.content.parts
                ):
                    reply_text = event.content.parts[0].text
                    break
        except Exception as e:
            print(f"[LLM Error] {e}")
            reply_text = "I encountered an error processing your request."

        client.reply(jid, reply_text, message["id"])


    except Exception as e:
        print(f"[Handler Error] {e}")
        client.reply(jid, "Sorry, I encountered an error.", message["id"])

creator.client.onMessage(
    lambda msg: asyncio.run_coroutine_threadsafe(handle_message(msg), loop)
)

scheduler = AsyncIOScheduler(event_loop=loop, timezone="Asia/Kolkata")
scheduler.add_job(push_daily_summary, "cron", hour=16, minute=0)
loop.create_task(push_daily_summary())
scheduler.start()

print("Scheduler started — CTRL+C to stop.")

try:
    loop.run_forever()
except KeyboardInterrupt:
    print("\nShutting down…")
finally:
    scheduler.shutdown(wait=False)
    creator.close()
