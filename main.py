import asyncio
import logging
import os
import time
import random
from datetime import timedelta
from pyrogram import Client, filters, idle, enums
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import Message
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from youtubesearchpython import VideosSearch
from PIL import Image, ImageDraw, ImageFont
import textwrap
from flask import Flask
import threading

# Flask health check
app = Flask(__name__)

@app.route('/health')
def health():
    return 'OK', 200

def run_server():
    app.run(host='0.0.0.0', port=8000)

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()

# Bot setup
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

# Cached fonts
FONTS = {
    "title": ImageFont.truetype("arialbd.ttf", 70) if os.path.exists("arialbd.ttf") else ImageFont.load_default(),
    "track": ImageFont.truetype("arial.ttf", 42) if os.path.exists("arial.ttf") else ImageFont.load_default(),
    "meta": ImageFont.truetype("arial.ttf", 36) if os.path.exists("arial.ttf") else ImageFont.load_default(),
}

# Utility Functions
async def is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

async def search_yt(query: str):
    try:
        search = VideosSearch(query, limit=1)
        result = search.result()["result"]
        if result:
            video = result[0]
            return video["title"], video["duration"], f"https://www.youtube.com/watch?v={video['id']}"
        return None, None, None
    except Exception as e:
        logger.error(f"search_yt error: {e}")
        return None, None, None

async def ytdl(format: str, link: str):
    try:
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
        ]
        random_ua = random.choice(user_agents)

        args = ["yt-dlp", "--geo-bypass", "--user-agent", random_ua, "-g", "-f", format, link]
        if os.path.exists("cookies.txt"):
            args[1:1] = ["--cookies", "cookies.txt"]

        # Positional args for older Python versions
        proc = await asyncio.create_subprocess_exec(
            *args,
            asyncio.subprocess.PIPE,  # stdout
            asyncio.subprocess.PIPE   # stderr
        )
        stdout, stderr = await proc.communicate()
        
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        return 0, stderr.decode()
    except Exception as e:
        logger.error(f"ytdl error: {e}")
        return 0, str(e)

async def play_or_queue_media(chat_id: int, title: str, duration: str, link: str, media_type: str = None, message: Message = None, from_loop: bool = False):
    try:
        duration_seconds = sum(int(x) * 60 ** i for i, x in enumerate(reversed(duration.split(":"))))
    except ValueError:
        duration_seconds = 0

    format_type = "bestaudio" if media_type == "audio" else "best"
    resp, media_link = await ytdl(format_type, link)
    
    if resp != 1:
        if message:
            await message.reply("❌ Unable to fetch media link.")
        return

    media_stream = MediaStream(media_link, video_flags=MediaStream.Flags.IGNORE if media_type == "audio" else None)
    await real_pytgcalls.play(chat_id, media_stream)
    stream_running[chat_id] = {
        "title": title,
        "duration": duration_seconds,
        "link": link,
        "media_type": media_type,
        "start_time": time.time(),
    }
    if not from_loop:
        asyncio.create_task(poll_stream_status(chat_id))

async def poll_stream_status(chat_id: int):
    while chat_id in stream_running:
        await asyncio.sleep(5)
        stream_info = stream_running.get(chat_id)
        if not stream_info:
            break

        elapsed_time = time.time() - stream_info["start_time"]
        if elapsed_time >= stream_info["duration"]:
            if chat_id in looping and looping[chat_id] != 0:
                if looping[chat_id] > 0:
                    looping[chat_id] -= 1
                await play_or_queue_media(chat_id, **stream_info, from_loop=True)
            elif chat_id in queues and queues[chat_id]:
                await play_next_from_queue(chat_id)
            else:
                stream_running.pop(chat_id, None)
                looping.pop(chat_id, None)
                await real_pytgcalls.leave_call(chat_id)
                break

# Command Handlers
@real_app.on_message(filters.command(["play", "vplay"], PREFIX))
async def play_media(client: Client, message: Message):
    chat_id = message.chat.id
    command = message.command[0]
    media_type = "video" if command == "vplay" else "audio"

    if len(message.command) < 2:
        await message.reply("❌ Please provide a song name or link.")
        return

    query = message.text.split(" ", 1)[1]
    indicator = await message.reply(f"🔍 Searching for {media_type}...")

    title, duration, link = await search_yt(query)
    if not link:
        await indicator.edit("❌ No results found.")
        return

    if chat_id in stream_running:
        position = await add_to_queue(chat_id, title, duration, link, media_type)
        await indicator.edit(f"**Added to Queue (Position {position}):** `{title}`\n**Duration:** {duration}")
    else:
        await play_or_queue_media(chat_id, title, duration, link, media_type, message)
        await indicator.edit(f"**▶️ Now Playing:** `{title}`\n**Duration:** {duration}\n**Requested By:** {message.from_user.mention}")

@real_app.on_message(filters.command("skip", PREFIX))
async def enhanced_skip(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    args = message.command

    if not await is_admin(client, chat_id, user_id):
        await message.reply("❌ Only admins can skip tracks!")
        return

    if not stream_running.get(chat_id) and not queues.get(chat_id):
        await message.reply("❌ Nothing is playing or in queue.")
        return

    if len(args) == 1:
        await play_next_from_queue(chat_id)
        reply_text = "⏭️ Skipped current song."
        if queues.get(chat_id):
            next_track = queues[chat_id][0]
            reply_text += f"\n\n**Now Playing:** `{next_track['title']}`"
        await message.reply(reply_text)
    else:
        try:
            index = int(args[1]) - 1
            queue = queues.get(chat_id, [])
            if index < 0 or index >= len(queue):
                await message.reply(f"❌ Invalid index. Queue has {len(queue)} songs.")
                return
            queues[chat_id] = queue[index:]
            await play_next_from_queue(chat_id)
            await message.reply(f"⏩ Skipped to position {index + 1}\n**Now Playing:** `{queues[chat_id][0]['title']}`")
        except ValueError:
            await message.reply("❌ Invalid index format. Use /skip 2")

@real_app.on_message(filters.command("stop", PREFIX))
async def stop(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply("❌ Only admins can stop the stream!")
        return

    if chat_id in stream_running:
        stream_running.pop(chat_id, None)
        queues.pop(chat_id, None)
        looping.pop(chat_id, None)
        await real_pytgcalls.leave_call(chat_id)
        await message.reply("⏹️ Stream stopped and queue cleared.")
    else:
        await message.reply("❌ No song is playing.")

@real_app.on_message(filters.command("loop", PREFIX))
async def loop_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply("❌ Only admins can set loops!")
        return

    if chat_id not in stream_running:
        await message.reply("❌ No song is playing to loop.")
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
    elif arg.isdigit():
        count = int(arg)
        looping[chat_id] = count - 1 if count > 0 else 0
        await message.reply(f"🔂 Loop set to {count} plays.")
    else:
        await message.reply("❌ Invalid argument. Use a number or 'inf'.")

@real_app.on_message(filters.command("pause", PREFIX))
async def pause(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply("❌ Only admins can pause!")
        return
    if chat_id in stream_running:
        await real_pytgcalls.pause(chat_id)
        await message.reply("⏸️ Playback paused.")
    else:
        await message.reply("❌ Nothing is playing.")

@real_app.on_message(filters.command("resume", PREFIX))
async def resume(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply("❌ Only admins can resume!")
        return
    if chat_id in stream_running:
        await real_pytgcalls.resume(chat_id)
        await message.reply("▶️ Playback resumed.")
    else:
        await message.reply("❌ Nothing is paused.")

@real_app.on_message(filters.command("volume", PREFIX))
async def volume(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply("❌ Only admins can change volume!")
        return
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.reply("❌ Provide a volume level (0-200).")
        return
    vol = int(message.command[1])
    if 0 <= vol <= 200:
        await real_pytgcalls.change_volume(chat_id, vol)
        await message.reply(f"🔊 Volume set to {vol}%")
    else:
        await message.reply("❌ Volume must be between 0 and 200.")

@real_app.on_message(filters.command("nowplaying", PREFIX))
async def now_playing(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id not in stream_running:
        await message.reply("❌ Nothing is playing.")
        return
    info = stream_running[chat_id]
    elapsed = timedelta(seconds=int(time.time() - info["start_time"]))
    await message.reply(
        f"🎵 **Now Playing:** `{info['title']}`\n"
        f"⏱️ **Elapsed:** {elapsed} / {timedelta(seconds=info['duration'])}\n"
        f"🔗 **Link:** {info['link']}"
    )

@real_app.on_message(filters.command("queue", PREFIX))
async def image_queue(client: Client, message: Message):
    chat_id = message.chat.id
    queue = queues.get(chat_id, [])
    if not queue:
        await message.reply("📭 Queue is empty.")
        return

    chat = await client.get_chat(chat_id)
    chat_title = chat.title or "This Chat"
    img_path = await generate_queue_image(queue, chat_title, chat_id)
    caption = f"**Current Queue for {chat_title}**\nTotal {len(queue)} songs"
    await message.reply_photo(photo=img_path, caption=caption)
    os.remove(img_path)

@real_app.on_message(filters.command(PING_COMMAND, PREFIX))
async def ping(client: Client, message: Message):
    latency = time.time() - bot_start_time
    await message.reply(f"🏓 Pong! Latency: {latency:.2f} seconds.")

# Queue and Stream Logic
async def add_to_queue(chat_id: int, title: str, duration: str, link: str, media_type: str):
    if chat_id not in queues:
        queues[chat_id] = []
    track = {"title": title, "duration": duration, "link": link, "media_type": media_type}
    queues[chat_id].append(track)
    return len(queues[chat_id])

async def play_next_from_queue(chat_id: int):
    if chat_id in queues and queues[chat_id]:
        next_track = queues[chat_id].pop(0)
        await play_or_queue_media(chat_id, **next_track)
    else:
        stream_running.pop(chat_id, None)
        await real_pytgcalls.leave_call(chat_id)

async def generate_queue_image(queue, chat_title, chat_id):
    BG_COLOR = (18, 18, 18)
    CARD_COLOR = (30, 30, 30)
    ACCENT_COLOR = (0, 230, 118)
    TEXT_COLOR = (255, 255, 255)
    META_COLOR = (170, 170, 170)
    WIDTH, HEIGHT = 1080, 1920

    img = Image.new('RGB', (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, WIDTH, 220), fill=ACCENT_COLOR)
    draw.text((80, 80), f"✦ {chat_title[:27]}", fill=TEXT_COLOR, font=FONTS["title"])
    draw.rounded_rectangle((40, 270, WIDTH - 40, HEIGHT - 100), radius=25, fill=CARD_COLOR)

    y_position = 300
    for idx, track in enumerate(queue[:8], 1):
        track_card_top = y_position
        if idx % 2 == 0:
            draw.rounded_rectangle((60, track_card_top, WIDTH - 60, track_card_top + 150), radius=15, fill=BG_COLOR)
        draw.text((80, track_card_top + 45), f"{idx}.", fill=ACCENT_COLOR, font=FONTS["track"])
        title = textwrap.shorten(track['title'], width=32, placeholder="..")
        draw.text((180, track_card_top + 40), title, fill=TEXT_COLOR, font=FONTS["track"])
        draw.text((180, track_card_top + 85), f"⏱︎ {track['duration']} | {track['media_type'].upper()}", fill=META_COLOR, font=FONTS["meta"])

        if idx == 1 and chat_id in stream_running and stream_running[chat_id]["title"] == track["title"]:
            elapsed = time.time() - stream_running[chat_id]["start_time"]
            progress = min(elapsed / stream_running[chat_id]["duration"], 1) if stream_running[chat_id]["duration"] else 0
            draw.rectangle((700, track_card_top + 60, 700 + 300 * progress, track_card_top + 80), fill=ACCENT_COLOR)

        y_position += 170

    img_path = f"queue_{chat_title.replace(' ', '_')}.png"
    img.save(img_path, quality=95)
    return img_path

# Main Loop
async def main():
    await real_app.start()
    await real_pytgcalls.start()
    print("Bot started!")
    await idle()
    await real_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
