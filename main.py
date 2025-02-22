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


# ... [Keep the existing imports and setup code] ...

# Modify the play_or_queue_media function to always update stream_running
async def play_or_queue_media(chat_id, title, duration, link, media_type=None, message=None, from_loop=False):
    try:
        duration_seconds = sum(
            int(x) * 60 ** i
            for i, x in enumerate(reversed(duration.split(":")))
        )
    except ValueError:
        duration_seconds = 0  # Handle invalid duration

    # Determine the media type (audio or video)
    format_type = "bestaudio" if media_type == "audio" else "best"
    resp, media_link = await ytdl(format_type, link)
    
    if resp != 1:
        if message:
            await message.reply("❌ Unable to fetch media link.")
        return

    # Always update stream_running with current track info
    stream_running[chat_id] = {
        "title": title,
        "duration": duration_seconds,
        "link": link,
        "media_type": media_type,
        "start_time": time.time(),
    }

    media_stream = MediaStream(media_link, video_flags=MediaStream.Flags.IGNORE if media_type == "audio" else None)
    await real_pytgcalls.play(chat_id, media_stream)
    asyncio.create_task(poll_stream_status(chat_id))

# Modify poll_stream_status to handle loop counts


# Add the loop command handler
@real_app.on_message(filters.command("loop", PREFIX))
async def loop_command(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in stream_running:
        await message.reply("❌ No song is currently playing to loop.")
        return

    args = message.command
    if len(args) < 2:
        # Default to infinite loop
        looping[chat_id] = -1
        await message.reply("🔂 Loop enabled infinitely.")
        return

    arg = args[1].lower()
    if arg in ("inf", "infinite"):
        looping[chat_id] = -1
        await message.reply("🔂 Loop enabled infinitely.")
        return

    try:
        loop_count = int(arg)
        if loop_count < 0:
            await message.reply("❌ Loop count cannot be negative.")
        elif loop_count == 0:
            looping.pop(chat_id, None)
            await message.reply("🔂 Loop disabled.")
        else:
            looping[chat_id] = loop_count - 1  # Subtract 1 for current play
            await message.reply(f"🔂 Loop set to {loop_count} plays.")
    except ValueError:
        await message.reply("❌ Invalid argument. Use a number, 'inf', or 'infinite'.")
        
# ... [Keep the rest of the existing code, like command handlers and main loop] ...




async def poll_stream_status(chat_id):
    while chat_id in stream_running:
        await asyncio.sleep(5)
        current_time = time.time()
        stream_info = stream_running.get(chat_id)
        if not stream_info:
            break

        elapsed_time = current_time - stream_info["start_time"]
        if elapsed_time > stream_info["duration"]:
            loop_count = looping.get(chat_id, 0)
            if loop_count != 0:  # Check for active loop (finite or infinite)
                if loop_count > 0:
                    looping[chat_id] -= 1
                # Play the same track again
                await play_or_queue_media(chat_id, **stream_info, from_loop=True)
            elif chat_id in queues and queues[chat_id]:
                next_track = queues[chat_id].pop(0)
                await play_or_queue_media(chat_id, **next_track)
            else:
                # End the stream
                stream_running.pop(chat_id, None)
                looping.pop(chat_id, None)
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


async def add_to_queue(chat_id, title, duration, link, media_type):
    if chat_id not in queues:
        queues[chat_id] = []
    
    track = {
        "title": title,
        "duration": duration,
        "link": link,
        "media_type": media_type,
    }
    queues[chat_id].append(track)
    return len(queues[chat_id])  # Returns position (1-based index)

@real_app.on_message(filters.command("skip", PREFIX))
async def enhanced_skip(client, message: Message):
    chat_id = message.chat.id
    args = message.command
    
    if not stream_running.get(chat_id) and not queues.get(chat_id):
        await message.reply("❌ Nothing is playing or in queue.")
        return

    # Default skip (no index)
    if len(args) == 1:
        if chat_id in stream_running:
            await play_next_from_queue(chat_id)
            await message.reply("⏭️ Skipped current song.")
        else:
            await message.reply("❌ No song is currently playing.")
        return

    # Handle index-based skip
    try:
        requested_index = int(args[1])
        if requested_index < 1:
            await message.reply("🚫 Index must be ≥ 1")
            return

        queue = queues.get(chat_id, [])
        if requested_index > len(queue) + 1:  # +1 to account for current song
            await message.reply(f"🚫 Only {len(queue)} songs in queue")
            return

        # Clear queue up to requested index
        if requested_index > 1:
            new_index = requested_index - 2  # Convert to 0-based index
            queues[chat_id] = queue[new_index:]
        
        # Stop current stream and play from queue
        if chat_id in stream_running:
            stream_running.pop(chat_id)
            await real_pytgcalls.leave_call(chat_id)
        
        await play_next_from_queue(chat_id)
        await message.reply(f"⏩ Skipped to position {requested_index}")
    except ValueError:
        await message.reply("❌ Invalid index format. Use /skip 2")

async def play_next_from_queue(chat_id):
    if chat_id in queues and queues[chat_id]:
        next_track = queues[chat_id].pop(0)
        await play_or_queue_media(chat_id, **next_track)
    else:
        stream_running.pop(chat_id, None)
        await real_pytgcalls.leave_call(chat_id)

@real_app.on_message(filters.command("queue", PREFIX))
async def detailed_queue(client, message):
    chat_id = message.chat.id
    queue = queues.get(chat_id, [])

    if not queue:
        await message.reply("📭 Queue is empty")
        return

    msg = "📋 **Current Queue:**\n"
    for idx, track in enumerate(queue, 1):
        msg += f"{idx}. [{track['title']}]({track['link']}) - {track['duration']}\n"
    
    # Show next 5 songs if available
    await message.reply(msg[:4000], disable_web_page_preview=True)  # Telegram limit


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
