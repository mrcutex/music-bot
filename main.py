import asyncio
import logging
import os
import time
import threading
from datetime import timedelta
from flask import Flask
from pyrogram import Client, filters, idle, enums
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from youtubesearchpython import VideosSearch
import aiohttp
import random
import requests
import subprocess

# Flask app
flask_app = Flask(__name__)

@flask_app.route('/health')
def health():
    return 'OK', 200

def run_server():
    flask_app.run(host='0.0.0.0', port=8000)

# Start Flask server in a thread
threading.Thread(target=run_server).start()

# Telegram Bot API Credentials
api_id = int(os.getenv("REAL_API_ID"))
api_hash = os.getenv("REAL_API_HASH")
session_string = os.getenv("REAL_SESSION_STRING")

# Initialize Pyrogram client and PyTgCalls
app = Client("RealAccount", api_id=api_id, api_hash=api_hash, session_string=session_string)
real_pytgcalls = PyTgCalls(app)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables for streaming and queues
stream_running = {}
queues = {}
looping = {}
PREFIX = ["/", "#", "!", "."]

app.set_parse_mode(enums.ParseMode.MARKDOWN)

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

            # High-resolution thumbnail URLs
            max_res_thumbnail = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            hq_thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            thumbnail_url = max_res_thumbnail if requests.get(max_res_thumbnail).status_code == 200 else hq_thumbnail

            link = f"https://www.youtube.com/watch?v={video_id}"
            return title, duration, link, thumbnail_url
        else:
            return None, None, None, None
    except Exception as e:
        logger.error(f"search_yt error: {e}")
        return None, None, None, None

async def ytdl(format, link):
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies", "cookies.txt",  
            "-g",
            "-f", f"{format}",
            f"{link}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if stdout:
            audio_link = stdout.decode().strip()
            logger.info(f"Audio link retrieved: {audio_link}")
            return 1, audio_link
        else:
            error_message = stderr.decode().strip()
            logger.error(f"yt-dlp stderr: {error_message}")
            return 0, error_message
    except Exception as e:
        logger.error(f"Exception occurred in ytdl: {e}")
        return 0, str(e)

async def download_thumbnail(url, retries=3):
    """Download the thumbnail from YouTube with retry logic."""
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        img_data = await resp.read()
                        img_name = f"thumb_{random.randint(1, 10000)}.jpg"
                        with open(img_name, 'wb') as f:
                            f.write(img_data)
                        return img_name
                    else:
                        logger.warning(f"Failed to download thumbnail (Attempt {attempt+1}/{retries}), Status: {resp.status}")
            except aiohttp.ClientError as e:
                logger.error(f"Error during thumbnail download (Attempt {attempt+1}/{retries}): {e}")
            await asyncio.sleep(2)  # Delay between retries
    return None

def convert_duration(duration_str):
    parts = duration_str.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    else:
        return int(parts[0])

async def add_to_queue(chat_id, title, duration, link, media_type):
    if chat_id not in queues:
        queues[chat_id] = []
    queues[chat_id].append({"title": title, "duration": duration, "link": link, "type": media_type})
    logger.info(f"Added to queue: {title} (Duration: {duration}) in chat {chat_id}")

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

async def play_media(chat_id, track, message, from_loop=False, seek_time=0):
    try:
        title, duration_str, link, media_type, thumbnail_url = track["title"], track["duration"], track["link"], track.get("thumbnail"), track["type"]
        duration = convert_duration(duration_str)
        
        # Fetch the audio or video stream link
        resp, songlink = await ytdl("bestaudio" if media_type == 'audio' else "best", link)
        if resp != 1:
            await message.reply("Error playing the next track in the queue.")
            return
        
        media_stream = MediaStream(songlink, video_flags=MediaStream.Flags.IGNORE if media_type == 'audio' else None)
        await real_pytgcalls.play(chat_id, media_stream)
        
        # Download and send thumbnail if available
        if thumbnail_url:
            thumbnail_file = await download_thumbnail(thumbnail_url)
            if thumbnail_file:
                try:
                    await message.reply_photo(thumbnail_file, caption=f"**Playing:** [{title}]({link})\n**Duration:** {duration_str}")
                except Exception as e:
                    logger.error(f"Error sending thumbnail: {e}")
                    await message.reply(f"**Playing:** [{title}]({link})\n**Duration:** {duration_str} (Thumbnail failed to load)")
                finally:
                    os.remove(thumbnail_file)  # Ensure file cleanup in all cases
            else:
                await message.reply(f"**Playing:** [{title}]({link})\n**Duration:** {duration_str}")
        else:
            await message.reply(f"**Playing:** [{title}]({link})\n**Duration:** {duration_str}")

        # Update stream running details for tracking
        stream_running[chat_id] = {
            "start_time": time.time() - seek_time,
            "duration": duration,
            "title": title,
            "duration_str": duration_str,
            "link": link,
            "type": media_type
        }
        
        logger.info(f"Started playing: {title} (Duration: {duration_str}) in chat {chat_id}")
    
    except Exception as e:
        logger.error(f"Error playing media: {e}")
        await message.reply(f"Error playing media: {e}")

@app.on_message(filters.command("play", PREFIX))
async def play(client, message):
    if len(message.command) < 2:
        await message.reply("Please provide a song name.")
        return

    chat_id = message.chat.id
    query = message.text.split(" ", 1)[1]
    indicator_message = await message.reply("Searching for the song...")

    try:
        await message.delete()
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        
        title, duration, link, thumbnail_url = await search_yt(query)
        
        if title:
            await add_to_queue(chat_id, title, duration, link, 'audio')
            await play_media(chat_id, {"title": title, "duration": duration, "link": link, "type": 'audio', "thumbnail": thumbnail_url}, indicator_message)
            await indicator_message.delete()
        else:
            await indicator_message.edit("No results found.")
    except Exception as e:
        logger.error(f"Error during play command: {e}")
        await indicator_message.edit("An error occurred while searching for the song.")

@app.on_message(filters.command("queue", PREFIX))
async def queue_status(client, message):
    chat_id = message.chat.id
    if chat_id not in queues or not queues[chat_id]:
        await message.reply("The queue is currently empty.")
        return
    
    queue_list = "\n".join([f"{i + 1}. {track['title']} (Duration: {track['duration']})" for i, track in enumerate(queues[chat_id])])
    await message.reply(f"Current Queue:\n{queue_list}")

@app.on_message(filters.command("skip", PREFIX))
async def skip(client, message):
    chat_id = message.chat.id
    if chat_id in queues and queues[chat_id]:
        queues[chat_id].pop(0)  # Remove the current track
        await message.reply("Skipped the current track.")
        await play_media(chat_id, queues[chat_id][0], message)
    else:
        await message.reply("No track to skip.")

@app.on_message(filters.command("loop", PREFIX))
async def toggle_loop(client, message):
    chat_id = message.chat.id
    looping[chat_id] = not looping.get(chat_id, False)
    status = "enabled" if looping[chat_id] else "disabled"
    await message.reply(f"Looping has been {status}.")

@app.on_message(filters.command("stop", PREFIX))
async def stop(client, message):
    chat_id = message.chat.id
    if chat_id in stream_running:
        stream_running.pop(chat_id)
        await real_pytgcalls.leave_call(chat_id)
        await message.reply("Stopped streaming.")
    else:
        await message.reply("Nothing is currently playing.")

@app.on_message(filters.command("clear", PREFIX))
async def clear_queue(client, message):
    chat_id = message.chat.id
    if chat_id in queues:
        queues[chat_id] = []
        await message.reply("Cleared the queue.")
    else:
        await message.reply("The queue is already empty.")

if __name__ == "__main__":
    app.start()
    logger.info("Bot is running...")
    idle()
    app.stop()
