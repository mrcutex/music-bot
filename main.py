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


app = Flask(__name__)

@app.route('/health')
def health():
    return 'OK', 200

def run_server():
    app.run(host='0.0.0.0', port=8000)

if __name__ == "__main__":
    threading.Thread(target=run_server).start()

api_id = int(os.getenv("REAL_API_ID"))
api_hash = os.getenv("REAL_API_HASH")
session_string = os.getenv("REAL_SESSION_STRING")

# Initialize Pyrogram client and PyTgCalls
real_app = Client("RealAccount", api_id=api_id, api_hash=api_hash, session_string=session_string)
real_pytgcalls = PyTgCalls(real_app)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stream_running = {}
queues = {}
looping = {}
_boot_ = time.time()

PING_COMMAND = ["alive", "ping", "pong", "status"]
PREFIX = ["/", "#", "!", "."]
OWNER_ID = 6262196413
GROUPS_FILE = "groups.json"
real_app.set_parse_mode(enums.ParseMode.MARKDOWN)
bot_start_time = time.time()

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
            f"{format}",
            f"{link}",
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

async def poll_stream_status(chat_id, message):
    while chat_id in stream_running:
        await asyncio.sleep(5)
        current_time = time.time()
        stream_info = stream_running.get(chat_id)
        if not stream_info:
            break
        elapsed_time = current_time - stream_info["start_time"]
        if elapsed_time > stream_info["duration"]:
            if chat_id in looping and looping[chat_id] > 0:
                looping[chat_id] -= 1
                await play_media(chat_id, stream_info, message, from_loop=True)
            elif chat_id in queues and queues[chat_id]:
                next_track = queues[chat_id].pop(0)
                await play_media(chat_id, next_track, message)
            else:
                stream_running.pop(chat_id, None)
                await real_pytgcalls.leave_call(chat_id)
                await message.reply("Stream has ended.")
                break

# Queue Management and Stream Playing Logic
@real_app.on_message(filters.command(["play", "vplay"], PREFIX))
async def play_media(client, message):
    chat_id = message.chat.id
    command = message.command[0]  # "/play" or "/vplay"
    media_type = "video" if command == "vplay" else "audio"

    if len(message.command) < 2:
        await message.reply("❌ Please provide a song name or link to play.")
        return

    query = message.text.split(" ", 1)[1]
    indicator_message = await message.reply(f"🔍 Searching for the {media_type}...")

    try:
        # Fetch YouTube results
        title, duration, link = await search_yt(query)
        if not link:
            await indicator_message.edit("❌ No results found for the given query.")
            return

        # Check if a stream is already running
        if chat_id in stream_running:
            # If the same song is already playing, don't add it to the queue
            if stream_running[chat_id]["link"] == link:
                await indicator_message.edit(f"🔊 **Currently playing:** [{title}]({link})")
                return

            # Add to the queue if already a song is playing
            await add_to_queue(chat_id, title, duration, link, media_type)
            await indicator_message.edit(f"**Added to Queue:** [{title}]({link})\n**Duration:** {duration}")
        else:
            # If no stream is running, start playing the song
            await play_or_queue_media(chat_id, title, duration, link, media_type, message)
            await indicator_message.edit(
                f"**▶️ Now Playing:** [{title}]({link})\n**Duration:** {duration}\n**Requested By:** {message.from_user.mention}",
                disable_web_page_preview=True,
            )
    except Exception as e:
        await indicator_message.edit(f"⚠️ Error: {e}")

async def play_or_queue_media(chat_id, title, duration, link, media_type, message):
    """Handles playing or queuing media (audio/video)."""
    resp, media_link = await ytdl("bestaudio" if media_type == "audio" else "best", link)
    if resp != 1:
        await message.reply("❌ Unable to fetch media link.")
        return

    media_stream = MediaStream(media_link, video_flags=MediaStream.Flags.IGNORE if media_type == "audio" else None)

    if chat_id in stream_running:
        # Add to queue if song is already playing
        await add_to_queue(chat_id, title, duration, link, media_type)
        return

    await real_pytgcalls.play(chat_id, media_stream)
    stream_running[chat_id] = {
        "title": title,
        "duration": duration,
        "link": link,
        "type": media_type,
    }

async def add_to_queue(chat_id, title, duration, link, media_type):

    if chat_id not in queues:
        queues[chat_id] = []

    if not any(item['link'] == link for item in queues[chat_id]):
        queues[chat_id].append({
            "title": title,
            "duration": duration,
            "link": link,
            "type": media_type,
        })
        logger.info(f"Added to queue: {title}")
        # Automatically try to play the next song if the stream isn't running
        if chat_id not in stream_running or len(queues[chat_id]) > 1:
            await play_next_from_queue(chat_id)
    else:
        logger.info(f"Skipped adding duplicate song to queue: {title}")
    
    # Display indicator message when queue is updated
    await real_app.send_message(chat_id, f"🎶 **Added to Queue:** {title}")


async def play_next_from_queue(chat_id):
    """Play the next media from the queue when the current song finishes."""
    if chat_id not in queues or len(queues[chat_id]) == 0:
        await real_app.send_message(chat_id, "🚫 **Queue is empty.**")
        return

    next_song = queues[chat_id].pop(0)
    title = next_song["title"]
    duration = next_song["duration"]
    link = next_song["link"]
    media_type = next_song["type"]

    await play_or_queue_media(chat_id, title, duration, link, media_type, None)
    

@real_app.on_message(filters.command("skip", PREFIX))
async def skip(client, message):
    chat_id = message.chat.id
    if chat_id in stream_running:
        # Skip current song and play the next one from the queue
        await play_next_from_queue(chat_id)
        await message.reply("⏭️ Skipping current song.")
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
    print("started")
    await idle()
    await real_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
