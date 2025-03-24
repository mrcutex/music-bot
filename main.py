import asyncio
import logging
import os
import time
import random
from datetime import timedelta
from pyrogram import Client, filters, idle, enums
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from youtubesearchpython import VideosSearch
from pyrogram.types import Message
from pyrogram.errors import ChatAdminRequired, FloodWait, UserNotParticipant, UserAdminInvalid
import json
from PIL import Image, ImageDraw, ImageFont
import textwrap
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

real_app = Client("RealAccount", api_id=api_id, api_hash=api_hash, session_string=session_string)
real_pytgcalls = PyTgCalls(real_app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stream_running = {}
queues = {}
looping = {}
bot_start_time = time.time()

PREFIX = ["/", "."]
PING_COMMAND = ["alive", "ping", "pong", "status"]

real_app.set_parse_mode(enums.ParseMode.MARKDOWN)


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
            "-f", f"{format}",
            f"{link}",
            "--no-proxy",  
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if stdout:
            audio_link = stdout.decode().split("\n")[0]
            logger.info(f"Audio link retrieved: {audio_link}")
            return 1, audio_link
        else:
            error_message = stderr.decode()
            logger.error(f"yt-dlp stderr: {error_message}")
            return 0, error_message
    except Exception as e:
        logger.error(f"Exception occurred in ytdl: {e}")
        return 0, str(e)





async def play_or_queue_media(chat_id, title, duration, link, media_type=None, message=None, from_loop=False):
    try:
        duration_seconds = sum(
            int(x) * 60 ** i
            for i, x in enumerate(reversed(duration.split(":")))
        )
    except ValueError:
        duration_seconds = 0  

    
    format_type = "bestaudio" if media_type == "audio" else "best"
    resp, media_link = await ytdl(format_type, link)
    
    if resp != 1:
        if message:
            await message.reply("❌ Unable to fetch media link.")
        return

    
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





@real_app.on_message(filters.command("loop", PREFIX))
async def loop_command(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in stream_running:
        await message.reply("❌ No song is currently playing to loop.")
        return

    args = message.command
    if len(args) < 2:
        
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
            looping[chat_id] = loop_count - 1  
            await message.reply(f"🔂 Loop set to {loop_count} plays.")
    except ValueError:
        await message.reply("❌ Invalid argument. Use a number, 'inf', or 'infinite'.")
        





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
            if loop_count != 0:  
                if loop_count > 0:
                    looping[chat_id] -= 1
                
                await play_or_queue_media(chat_id, **stream_info, from_loop=True)
            elif chat_id in queues and queues[chat_id]:
                next_track = queues[chat_id].pop(0)
                await play_or_queue_media(chat_id, **next_track)
            else:
                
                stream_running.pop(chat_id, None)
                looping.pop(chat_id, None)
                await real_pytgcalls.leave_call(chat_id)
                break


async def generate_queue_image(queue, chat_title):
    # Modern Dark Theme Parameters
    BG_COLOR = (18, 18, 18)
    CARD_COLOR = (30, 30, 30)
    ACCENT_COLOR = (0, 230, 118)
    TEXT_COLOR = (255, 255, 255)
    META_COLOR = (170, 170, 170)
    
    # Image dimensions
    WIDTH, HEIGHT = 1080, 1920
    
    # Create canvas
    img = Image.new('RGB', (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Font settings (using default fonts)
    title_font = ImageFont.truetype("arialbd.ttf", 70) if os.path.exists("arialbd.ttf") else ImageFont.load_default(54)
    track_font = ImageFont.truetype("arial.ttf", 42) if os.path.exists("arial.ttf") else ImageFont.load_default(42)
    meta_font = ImageFont.truetype("arial.ttf", 36) if os.path.exists("arial.ttf") else ImageFont.load_default(36)
    
    # Header Section
    header_height = 220
    draw.rectangle((0, 0, WIDTH, header_height), fill=ACCENT_COLOR)
    
    # Chat Title with Three Dots
    draw.text(
        (80, 80), 
        f"✦ {chat_title[:27]}", 
        fill=TEXT_COLOR, 
        font=title_font
    )
    
    
    # Track List Container
    card_padding = 40
    card_width = WIDTH - 2*card_padding
    draw.rounded_rectangle(
        (card_padding, header_height + 50, 
         WIDTH - card_padding, HEIGHT - 100),
        radius=25,
        fill=CARD_COLOR
    )
    
    # Track Items
    y_position = header_height + 100
    max_tracks = 8
    track_height = 150
    
    for idx, track in enumerate(queue[:max_tracks], 1):
        # Track Card
        track_card_top = y_position
        track_card_bottom = y_position + track_height
        
        # Alternate Background
        if idx % 2 == 0:
            draw.rounded_rectangle(
                (card_padding + 20, track_card_top,
                 WIDTH - card_padding - 20, track_card_bottom),
                radius=15,
                fill=BG_COLOR
            )
        
        # Track Number
        draw.text(
            (card_padding + 50, track_card_top + 45), 
            f"{idx}.", 
            fill=ACCENT_COLOR, 
            font=track_font
        )
        
        # Track Title
        title = textwrap.shorten(track['title'], width=32, placeholder="..")
        draw.text(
            (card_padding + 150, track_card_top + 40), 
            title, 
            fill=TEXT_COLOR, 
            font=track_font
        )
        
        # Metadata Line
        metadata_y = track_card_top + 85
        draw.text(
            (card_padding + 150, metadata_y), 
            f"⏱︎ {track['duration']} | {track['media_type'].upper()}", 
            fill=META_COLOR, 
            font=meta_font
        )
        
        # Waveform Visualization
        waveform_start = 700
        for i in range(14):
            bar_width = 15
            spacing = 5
            x = waveform_start + (i * (bar_width + spacing))
            height = random.randint(30, 80)
            draw.rounded_rectangle(
                (x, track_card_top + 50,
                 x + bar_width, track_card_top + 50 + height),
                radius=3,
                fill=ACCENT_COLOR
            )
        
        y_position += track_height + 20
    
    # Save Image
    img_path = f"queue_{chat_title.replace(' ', '_')}.png"
    img.save(img_path, quality=95)
    return img_path

    
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
    return len(queues[chat_id])  

# Modified play_media handler
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
            position = await add_to_queue(chat_id, title, duration, link, media_type)
            await indicator_message.edit(
                f"**Added to Queue (Position {position}):** `{title}`\n"
                f"**Duration:** {duration}",
                disable_web_page_preview=True
            )
        else:
            await play_or_queue_media(chat_id, title, duration, link, media_type, message)
            await indicator_message.edit(
                f"**▶️ Now Playing:** `{title}`\n"
                f"**Duration:** {duration}\n"
                f"**Requested By:** {message.from_user.mention}",
                disable_web_page_preview=True
            )
    except Exception as e:
        await indicator_message.edit(f"⚠️ Error: {e}")

# Modified skip handler
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
            reply_text = "⏭️ Skipped current song."
            if queues.get(chat_id):
                next_track = queues[chat_id][0]
                reply_text += f"\n\n**Now Playing:** `{next_track['title']}`"
            await message.reply(reply_text)
        else:
            await message.reply("❌ No song is currently playing.")
        return

    # Handle index-based skip
    try:
        requested_index = int(args[1])
        queue = queues.get(chat_id, [])
        
        if requested_index < 1:
            await message.reply("🚫 Index must be ≥ 1")
            return
            
        if requested_index > len(queue) + 1:
            await message.reply(f"🚫 Only {len(queue)} songs in queue")
            return

        # Calculate actual position in queue
        actual_position = requested_index - 1  # Convert to 0-based index
        
        if requested_index > 1:
            # Keep from requested index onward
            queues[chat_id] = queue[actual_position:]
        
        # Stop current stream
        if chat_id in stream_running:
            stream_running.pop(chat_id)
            await real_pytgcalls.leave_call(chat_id)
        
        # Play next from modified queue
        if queues[chat_id]:
            await play_next_from_queue(chat_id)
            new_track = queues[chat_id][0]
            await message.reply(
                f"⏩ Skipped to position {requested_index}\n"
                f"**Now Playing:** `{new_track['title']}`"
            )
        else:
            await message.reply("✅ Queue cleared")
    except ValueError:
        await message.reply("❌ Invalid index format. Use /skip 2")

# Modified queue handler with image
@real_app.on_message(filters.command("queue", PREFIX))
async def image_queue(client, message):
    chat_id = message.chat.id
    queue = queues.get(chat_id, [])
    
    if not queue:
        await message.reply("📭 Queue is empty")
        return

    try:
        chat = await client.get_chat(chat_id)
        chat_title = chat.title if chat.title else "This Chat"
        img_path = await generate_queue_image(queue, chat_title)
        
        caption = f"**Current Queue for {chat_title}**\n"
        caption += f"Total {len(queue)} songs in queue"
        
        await message.reply_photo(
            photo=img_path,
            caption=caption,
            quote=True
        )
        os.remove(img_path)
    except Exception as e:
        logger.error(f"Queue image error: {e}")
        await message.reply("⚠️ Failed to generate queue image, showing text version:")
        msg = "📋 **Current Queue:**\n"
        for idx, track in enumerate(queue, 1):
            msg += f"{idx}. `{track['title']}` - {track['duration']}\n"
        await message.reply(msg[:4000])

async def play_next_from_queue(chat_id):
    if chat_id in queues and queues[chat_id]:
        next_track = queues[chat_id].pop(0)
        await play_or_queue_media(chat_id, **next_track)
    else:
        stream_running.pop(chat_id, None)
        await real_pytgcalls.leave_call(chat_id)




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


async def main():
    await real_app.start()
    await real_pytgcalls.start()
    print("Bot started!")
    await idle()
    await real_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
