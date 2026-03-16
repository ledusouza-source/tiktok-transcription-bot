# BOT VERSION: v2.4 - OPTIMIZED: OpenAI Whisper API, no local whisper install
#!/usr/bin/env python3
import os
import logging
import subprocess
import tempfile
import aiofiles
import requests
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
TARGET_CHAT_ID = os.environ.get('TARGET_CHAT_ID')
client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

app = Application.builder().token(BOT_TOKEN).build()

PROMPT_REFINAMENTO = """Melhore a seguinte transcricao de um video de TikTok ou Instagram.
Corrija erros, adicione pontuacao e organize o texto de forma clara e natural.
Responda apenas com a transcricao refinada, sem explicacoes adicionais."""

async def baixar_video(link):
    logger.info(f'Baixando video de: {link}')
    try:
        temp_dir = tempfile.mkdtemp()
        if 'tiktok' in link:
            subprocess.run(['yt-dlp', '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best', '--merge-output-format', 'mp4', '-o', os.path.join(temp_dir, 'video.%(ext)s'), link], check=True, timeout=300)
        elif 'instagram' in link:
            subprocess.run(['yt-dlp', '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best', '--merge-output-format', 'mp4', '-o', os.path.join(temp_dir, 'video.%(ext)s'), link], check=True, timeout=300)
        for f in os.listdir(temp_dir):
            if f.endswith('.mp4'):
                return os.path.join(temp_dir, f)
        return None
    except Exception as e:
        logger.error(f'Erro ao baixar video: {e}')
        return None

async def extrair_audio(video_path):
    logger.info(f'Extraindo audio de: {video_path}')
    try:
        temp_dir = os.path.dirname(video_path)
        audio_path = os.path.join(temp_dir, 'audio.mp3')
        subprocess.run(['ffmpeg', '-i', video_path, '-vn', '-acodec', 'libmp3lame', '-q:a', '2', audio_path], check=True, timeout=120)
        return audio_path
    except Exception as e:
        logger.error(f'Erro ao extrair audio: {e}')
        return None

async def transcrever_audio(audio_path):
    logger.info(f'Transcrevendo audio: {audio_path}')
    try:
        with open(audio_path, 'rb') as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        return transcript.text
    except Exception as e:
        logger.error(f'Erro na transcreveo: {e}')
        return None

async def refinar_texto(texto):
    logger.info('Refinando texto com OpenAI GPT')
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": PROMPT_REFINAMENTO},
                {"role": "user", "content": texto}
            ],
            max_tokens=1500
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f'Erro ao refinar texto: {e}')
        return texto

async def processar_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    await update.message.reply_text('Processando video... aguarde...')
    video_path = await baixar_video(texto)
    if not video_path:
        await update.message.reply_text('Erro ao baixar o video.')
        return
    audio_path = await extrair_audio(video_path)
    if not audio_path:
        await update.message.reply_text('Erro ao extrair o audio.')
        return
    transcricao = await transcrever_audio(audio_path)
    if not transcricao:
        await update.message.reply_text('Erro na transcreveo do audio.')
        return
    texto_refinado = await refinar_texto(transcricao)
    await update.message.reply_text(f'*Transcrio:*\n\n{transcricao}\n\n*Texto refinado:*\n\n{texto_refinado}', parse_mode='Markdown')
    if TARGET_CHAT_ID:
        try:
            await app.bot.send_message(chat_id=TARGET_CHAT_ID, text=f'*Link:* {texto}\n\n*Transcrio:*\n{transcricao}\n\n*Texto refinado:*\n{texto_refinado}', parse_mode='Markdown')
        except Exception as e:
            logger.error(f'Erro ao enviar para chat alvo: {e}')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Ola! Envie um link de TikTok ou Instagram para transcrever.')

async def mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    if 'tiktok' in texto.lower() or 'instagram' in texto.lower():
        await processar_video(update, context)
    else:
        await update.message.reply_text('Envie um link valido de TikTok ou Instagram.')

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensagem))

if __name__ == '__main__':
    logger.info('Iniciando o bot...')
    app.run_polling()
