import asyncio
import logging
import os
import time
from datetime import timedelta
from pyrogram import Client, filters, idle, enums
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from youtubesearchpython import VideosSearch
from pyrogram.types import Message
from pyrogram.errors import ChatAdminRequired, FloodWait, UserNotParticipant, UserAdminInvalid
import json
from flask import Flask
import threading


# Flask health check
app = Flask(__name__)

@app.route('/health')
def health():
    return 'OK', 200

def run_server():
    app.run(host='0.0.0.0', port=8000)

# Start Flask server
if __name__ == "__main__":
    threading.Thread(target=run_server).start()

# Configuration
api_id = int(os.getenv("REAL_API_ID"))
api_hash = os.getenv("REAL_API_HASH")
session_string = os.getenv("REAL_SESSION_STRING")

real_app = Client("RealAccount", api_id=api_id, api_hash=api_hash, session_string=session_string)
real_pytgcalls = PyTgCalls(real_app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stream_running = {}
queues = {}
looping = {}
bot_start_time = time.time()

PREFIX = ["/", "#", "!", "."]
PING_COMMAND = ["alive", "ping", "pong", "status"]

real_app.set_parse_mode(enums.ParseMode.MARKDOWN)

# Helper functions
async def search_yt(query):
    try:
        search = VideosSearch(query, limit=1)
        result = search.result()
        if 'result' in result and result['result']:
            video = result['result'][0]
            title = video['title']
            duration = video['duration']
            video_id = video['id']
            link = f"https://www.youtube.com/watch?v={video_id}"
            return title, duration, link
        else:
            return None, None, None
    except Exception as e:
        logger.error(f"search_yt error: {e}")
        return None, None, None

async def ytdl(format, link):
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies", "cookies.txt",  
            "-g",
            "-f",
            format,
            link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        else:
            return 0, stderr.decode()
    except Exception as e:
        logger.error(f"ytdl error: {e}")
        return 0, str(e)

async def poll_stream_status(chat_id):
    while chat_id in stream_running:
        await asyncio.sleep(5)
        current_time = time.time()
        stream_info = stream_running.get(chat_id)
        if not stream_info:
            break

        elapsed_time = current_time - stream_info["start_time"]
        if elapsed_time > stream_info["duration"]:  # Comparison works correctly now
            if chat_id in looping and looping[chat_id] > 0:
                looping[chat_id] -= 1
                await play_or_queue_media(chat_id, **stream_info, from_loop=True)
            elif chat_id in queues and queues[chat_id]:
                next_track = queues[chat_id].pop(0)
                await play_or_queue_media(chat_id, **next_track, media_type=next_track['media_type'])
            else:
                # End the stream
                stream_running.pop(chat_id, None)
                if chat_id in looping:
                    looping.pop(chat_id)
                await real_pytgcalls.leave_call(chat_id)
                break

# Command Handlers
@real_app.on_message(filters.command(["play", "vplay"], PREFIX))
async def play_media(client, message):
    chat_id = message.chat.id
    command = message.command[0]
    media_type = "video" if command == "vplay" else "audio"

    if len(message.command) < 2:
        await message.reply("❌ Please provide a song name or link to play.")
        return

    query = message.text.split(" ", 1)[1]
    indicator_message = await message.reply(f"🔍 Searching for the {media_type}...")

    try:
        title, duration, link = await search_yt(query)
        if not link:
            await indicator_message.edit("❌ No results found for the given query.")
            return

        if chat_id in stream_running:
            await add_to_queue(chat_id, title, duration, link, media_type)
            await indicator_message.edit(f"**Added to Queue:** [{title}]({link})\n**Duration:** {duration}")
        else:
            await play_or_queue_media(chat_id, title, duration, link, media_type, message)
            await indicator_message.edit(
                f"**▶️ Now Playing:** [{title}]({link})\n**Duration:** {duration}\n**Requested By:** {message.from_user.mention}",
                disable_web_page_preview=True,
            )
    except Exception as e:
        await indicator_message.edit(f"⚠️ Error: {e}")

async def play_or_queue_media(chat_id, title, duration, link, media_type=None, message=None, from_loop=False):
    
    try:
        duration_seconds = sum(
            int(x) * 60 ** i
            for i, x in enumerate(reversed(duration.split(":")))
        )
    except ValueError:
        duration_seconds = 0  # Default if duration is unavailable or invalid

    resp, media_link = await ytdl("bestaudio" if media_type == "audio" else "best", link)
    if resp != 1:
        if message:
            await message.reply("❌ Unable to fetch media link.")
        return

    media_stream = MediaStream(media_link, video_flags=MediaStream.Flags.IGNORE if media_type == "audio" else None)

    if not from_loop:
        stream_running[chat_id] = {
            "title": title,
            "duration": duration_seconds,  # Store as numeric value
            "link": link,
            "media_type": media_type,
            "start_time": time.time(),
        }

    await real_pytgcalls.play(chat_id, media_stream)
    asyncio.create_task(poll_stream_status(chat_id))


async def add_to_queue(chat_id, title, duration, link, media_type):
    if chat_id not in queues:
        queues[chat_id] = []

    if not any(item['link'] == link for item in queues[chat_id]):
        queues[chat_id].append({
            "title": title,
            "duration": duration,
            "link": link,
            "media_type": media_type,  # Use 'media_type' instead of 'type'
        })
        logger.info(f"Added to queue: {title}")

@real_app.on_message(filters.command("skip", PREFIX))
async def skip(client, message):
    chat_id = message.chat.id
    if chat_id in stream_running:
        await play_next_from_queue(chat_id)
        await message.reply("⏭️ Skipping current song.")
    else:
        await message.reply("❌ No song is currently playing.")

@real_app.on_message(filters.command("stop", PREFIX))
async def stop(client, message):
    chat_id = message.chat.id
    if chat_id in stream_running:
        stream_running.pop(chat_id, None)
        queues.pop(chat_id, None)
        looping.pop(chat_id, None)
        await real_pytgcalls.leave_call(chat_id)
        await message.reply("⏹️ Stream stopped and queue cleared.")
    else:
        await message.reply("❌ No song is currently playing.")

@real_app.on_message(filters.command("queue", PREFIX))
async def show_queue(client, message):
    chat_id = message.chat.id

    if chat_id in queues and queues[chat_id]:
        queue_message = "📋 **Current Queue:**\n"
        for idx, track in enumerate(queues[chat_id], 1):
            queue_message += f"{idx}. [{track['title']}]({track['link']}) - {track['duration']}\n"
        await message.reply(queue_message, disable_web_page_preview=True)
    else:
        await message.reply("📭 **Queue is empty.**")

@real_app.on_message(filters.command(PING_COMMAND, PREFIX))
async def ping(client, message):
    latency = time.time() - bot_start_time
    await message.reply(f"🏓 Pong! Latency: {latency:.2f} seconds.")

# Run the bot
async def main():
    await real_app.start()
    await real_pytgcalls.start()
    print("Bot started!")
    await idle()
    await real_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
