# BOT VERSION: v2.3 - FIXED: Double message removed, Instagram retry with fallback
#!/usr/bin/env python3
import os
import logging
import subprocess
import tempfile
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import whisper
import asyncio
from collections import deque
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TARGET_CHAT_ID = 1767777754

video_queue = deque()
processing = False
queue_lock = asyncio.Lock()

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not configured!")
    raise ValueError("BOT_TOKEN environment variable is required")

if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY not configured!")
    raise ValueError("OPENAI_API_KEY environment variable is required")

client = OpenAI(api_key=OPENAI_API_KEY)

logger.info("Loading Whisper model...")
whisper_model = whisper.load_model("small")
logger.info("Whisper model loaded successfully!")

def load_prompt_template():
    """Load PROMPT.txt or use default"""
    try:
        prompt_path = Path("PROMPT.txt")
        if prompt_path.exists():
            with open(prompt_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    logger.info(f"PROMPT.txt loaded: {len(content)} chars")
                    return content
    except Exception as e:
        logger.warning(f"Error loading PROMPT.txt: {e}")
    
    logger.info("Using default PROMPT")
    return """You are an experienced editor for viral content on TikTok/Instagram.
Your task:
1. Reshape the text into an emotional and impactful narrative
2. DO NOT LOSE the original meaning
3. Maximum 1 minute duration
4. Quality of life, mental health, psychology, motivation, self-love, respect, care, love
5. Ideal partnerships if applicable
6. Appropriate punctuation
7. Natural Portuguese
8. Remove stuttering and repetitions
9. RESPOND ONLY WITH THE IMPROVED TEXT, WITHOUT EXPLANATIONS
Original text:
{TEXTO_ORIGINAL}"""

prompt_template = load_prompt_template()

async def refine_text_with_gpt(transcript: str) -> str:
    """Refine text with GPT-3.5-turbo"""
    logger.info(f"[REFINE] Starting refinement. Original text: {len(transcript)} chars")
    
    if not transcript or len(transcript.strip()) == 0:
        logger.warning("[REFINE] Empty text received")
        return transcript
    
    try:
        prompt = prompt_template.replace("{TEXTO_ORIGINAL}", transcript)
        logger.info(f"[REFINE] Sending to OpenAI. Prompt: {len(prompt)} chars")
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You are a specialized editor for viral social media content. Refine ONLY the provided text, without adding explanations."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        refined = response.choices[0].message.content.strip()
        logger.info(f"[REFINE] Success! Refined text: {len(refined)} chars")
        
        if refined == transcript:
            logger.warning("[REFINE] WARNING: Refined text equal to original!")
        
        return refined
        
    except Exception as e:
        logger.error(f"[REFINE] ERROR: {str(e)}", exc_info=True)
        logger.warning(f"[REFINE] Returning original text without refinement")
        return transcript

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"[START] /start command received from user {update.effective_user.id}")
    await update.message.reply_text(
        "Hello! Send a TikTok or Instagram Reels link to transcribe.\n"
        "I will transcribe the video and refine it with AI.\n"
        "Example: https://www.tiktok.com/@user/video/123456789"
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process TikTok/Instagram links"""
    url = update.message.text.strip()
    user_id = update.effective_user.id
    
    logger.info(f"[HANDLE] URL received from user {user_id}: {url}")
    
    if not ("tiktok.com" in url or "instagram.com" in url):
        logger.info(f"[HANDLE] Invalid URL (not TikTok/Instagram)")
        await update.message.reply_text("\u274c Please send a valid TikTok or Instagram link")
        return
    
    async with queue_lock:
        video_queue.append({"url": url, "user_id": user_id, "update": update, "context": context})
        logger.info(f"[QUEUE] Video added to queue. Queue size: {len(video_queue)}")
    
    await update.message.reply_text(f"\u23f3 Your video was added to the queue. Position: {len(video_queue)}")

async def download_video(url: str, video_file: str) -> bool:
    """Download video using yt-dlp with Instagram support"""
    try:
        logger.info(f"[DOWNLOAD] Attempting standard download: {url}")
        result = subprocess.run(
            ["yt-dlp", "-q", "--no-warnings", "-o", video_file, url],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            logger.info(f"[DOWNLOAD] Standard download successful")
            return True
        
        logger.warning(f"[DOWNLOAD] Standard failed, retrying with --no-check-certificates")
        result = subprocess.run(
            ["yt-dlp", "-q", "--no-warnings", "--no-check-certificates", "-o", video_file, url],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            logger.info(f"[DOWNLOAD] Retry with --no-check-certificates successful")
            return True
        
        logger.error(f"[DOWNLOAD] Both attempts failed. stderr: {result.stderr}")
        return False
        
    except subprocess.TimeoutExpired:
        logger.error(f"[DOWNLOAD] Timeout (>300s)")
        return False
    except Exception as e:
        logger.error(f"[DOWNLOAD] Exception: {str(e)}")
        return False

async def process_video_queue_worker(app):
    """Worker that processes videos from queue one by one"""
    global processing
    
    while True:
        try:
            async with queue_lock:
                if video_queue and not processing:
                    processing = True
                    video_data = video_queue.popleft()
                else:
                    await asyncio.sleep(1)
                    continue
            
            url = video_data["url"]
            update = video_data["update"]
            context = video_data["context"]
            
            logger.info(f"[QUEUE] Processing: {url}")
            
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    video_file = os.path.join(tmpdir, "video.mp4")
                    audio_file = os.path.join(tmpdir, "audio.mp3")
                    
                    if not await download_video(url, video_file):
                        logger.error(f"[HANDLE] Failed to download video after retries")
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text="\u274c Failed to download video"
                        )
                        processing = False
                        continue
                    
                    logger.info(f"[HANDLE] Extracting audio")
                    subprocess.run(
                        ["ffmpeg", "-i", video_file, "-q:a", "9", "-n", audio_file],
                        capture_output=True,
                        timeout=300
                    )
                    
                    if not os.path.exists(audio_file):
                        logger.error(f"[HANDLE] Audio file was not created")
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text="\u274c Failed to extract audio"
                        )
                        processing = False
                        continue
                    
                    logger.info(f"[HANDLE] Transcribing with Whisper")
                    result = whisper_model.transcribe(audio_file, language="pt")
                    transcript = result["text"].strip()
                    
                    if not transcript:
                        transcript = "[No audio or language not recognized]"
                    
                    logger.info(f"[HANDLE] Transcription obtained: {len(transcript)} chars")
                    logger.info(f"[HANDLE] Text: {transcript[:80]}...")
                    
                    logger.info(f"[HANDLE] Refining with OpenAI...")
                    refined_transcript = await refine_text_with_gpt(transcript)
                    logger.info(f"[HANDLE] Refinement completed")
                    
                    logger.info(f"[HANDLE] Sending to TARGET_CHAT_ID: {TARGET_CHAT_ID}")
                    logger.info(f"[HANDLE] Text to send ({len(refined_transcript)} chars): {refined_transcript[:100]}...")
                    
                    await context.bot.send_message(
                        chat_id=TARGET_CHAT_ID,
                        text=refined_transcript
                    )
                    
                    logger.info(f"[HANDLE] Message sent successfully to {TARGET_CHAT_ID}")
                    
            except Exception as e:
                logger.error(f"[HANDLE] ERROR PROCESSING: {str(e)}", exc_info=True)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"\u274c Error: {str(e)[:200]}"
                )
            finally:
                processing = False
                
        except Exception as e:
            logger.error(f"[QUEUE] Worker error: {str(e)}", exc_info=True)
            processing = False
            await asyncio.sleep(5)

logger.info("Building application...")
app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

if __name__ == "__main__":
    logger.info("="*80)
    logger.info("BOT STARTED SUCCESSFULLY")
    logger.info(f"TARGET_CHAT_ID: {TARGET_CHAT_ID}")
    logger.info("="*80)
    
    app.job_queue.run_repeating(process_video_queue_worker, interval=1, first=0.1, data=app)
    
    app.run_polling()
