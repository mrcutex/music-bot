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
import subprocess
import requests
import random
import aiohttp
from PIL import Image, ImageDraw, ImageFont
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
app = Client("RealAccount", api_id=api_id, api_hash=api_hash, session_string=session_string)
real_pytgcalls = PyTgCalls(app)


# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stream_running = {}
queues = {}
looping = {}
_boot_ = time.time()

PREFIX = ["/", "#", "!", "."]
app.set_parse_mode(enums.ParseMode.MARKDOWN)
bot_start_time = time.time()
MAX_TITLE_LENGTH = 20
MAX_TITLE_LENGTHH = 30
CLINK = "https://t.me/mrcutex"

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

            # Thumbnail URLs in preferred order
            thumbnail_urls = [
                f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
            ]

            link = f"https://www.youtube.com/watch?v={video_id}"
            return title, duration, link, thumbnail_urls
        else:
            return None, None, None, []  # Return an empty list if no result is found
    except Exception as e:
        logger.error(f"search_yt error: {e}")
        return None, None, None, []





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



async def download_thumbnail(thumbnail_urls, retries=3):
    """Attempt to download a thumbnail from a list of URLs with retry logic."""
    try:
        async with aiohttp.ClientSession() as session:
            for url in thumbnail_urls:
                for attempt in range(retries):
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                img_data = await resp.read()
                                img_name = f"thumb_{random.randint(1, 10000)}.jpg"
                                with open(img_name, 'wb') as f:
                                    f.write(img_data)
                                return img_name  # Return image name if successful
                            else:
                                logger.warning(f"Thumbnail download failed at {url} (Attempt {attempt+1}/{retries})")
                    except aiohttp.ClientError as e:
                        logger.error(f"Download error at {url} (Attempt {attempt+1}/{retries}): {e}")
                    await asyncio.sleep(2)  # Delay between retries

    except Exception as e:
        logger.error(f"Unexpected error downloading thumbnail: {e}")
    return None  # Return None if all attempts fail



def convert_duration(duration_str):
    parts = duration_str.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    else:
        return int(parts[0])


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
                # End stream if no more loops or queue
                stream_running.pop(chat_id, None)
                looping.pop(chat_id, None)  # Clear looping status
                await real_pytgcalls.leave_call(chat_id)
                await message.reply("Stream has ended.")
                break

#Imports and setup remain unchanged

async def add_to_queue(chat_id, title, duration, link, media_type):
    if chat_id not in queues:
        queues[chat_id] = []
    queues[chat_id].append({"title": title, "duration": duration, "link": link, "type": media_type})
    logger.info(f"Added to queue: {title} (Duration: {duration}) in chat {chat_id}")



# /ping command handler
@app.on_message(filters.command("ping", PREFIX))
async def ping_command(client, message):
    # Bot ping calculation
    start_time = time.time()
    sent_message = await message.reply_text("Pinging...")
    bot_ping = round((time.time() - start_time) * 1000)  # Bot ping in ms

    # Server ping calculation
    try:
        # Replace "8.8.8.8" with another IP if needed
        output = subprocess.check_output(["ping", "-c", "1", "8.8.8.8"])
        server_ping = int(output.split(b"time=")[1].split(b" ")[0])
    except Exception as e:
        server_ping = None  # If there's an error, set as None

    # Overall Ping calculation (average if server_ping is available)
    if server_ping is not None:
        overall_ping = (bot_ping + server_ping) / 2
        ping_message = (
            f"🤖 Bot Ping: `{bot_ping} ms`\n"
            f"🌐 Server Ping: `{server_ping} ms`\n"
            f"📊 Overall Ping: `{overall_ping} ms`"
        )
    else:
        ping_message = (
            f"🤖 Bot Ping: `{bot_ping} ms`\n"
            f"🌐 Server Ping: `Unavailable`\n"
            f"📊 Overall Ping: `{bot_ping} ms`"
        )

    # Update message with ping information
    await sent_message.edit_text(ping_message)






async def create_thumbnail(title, duration):
    # Open the template image
    template_path = 'banner.png'  # Replace with your template image path
    template = Image.open(template_path).convert("RGBA")
    
    # Define font for title and duration
    title_font_path = "DejaVuSans-Bold.ttf"  # Replace with your bold font path
    title_font = ImageFont.truetype(title_font_path, 33)  # Large font for title
    duration_font = ImageFont.truetype("DejaVuSans.ttf", 22)  # Smaller font for duration

    # Truncate the title if it exceeds 22 characters
    max_title_length = 22
    truncated_title = title if len(title) <= max_title_length else title[:max_title_length] + '...'

    # Draw text on the image
    draw = ImageDraw.Draw(template)

    # Calculate width for horizontal centering of title and duration texts
    title_text_bbox = draw.textbbox((0, 0), truncated_title, font=title_font)
    title_text_width = title_text_bbox[2] - title_text_bbox[0]
    duration_text_bbox = draw.textbbox((0, 0), f"Duration: {duration}", font=duration_font)
    duration_text_width = duration_text_bbox[2] - duration_text_bbox[0]

    # Define vertical positions for title and duration with margin adjustments
    bottom_margin = 150  # Adjusted bottom margin
    title_position = ((template.width - title_text_width) // 2, template.height - title_text_bbox[3] - bottom_margin)
    duration_position = ((template.width - duration_text_width) // 2, title_position[1] + title_text_bbox[3] + 10)

    # Draw the title text with a pseudo-bold effect
    offsets = [(0, 0), (1, 0), (0, 1), (1, 1)]
    for offset in offsets:
        draw.text((title_position[0] + offset[0], title_position[1] + offset[1]), truncated_title, font=title_font, fill="white")

    # Draw the duration text centered below the title
    draw.text(duration_position, f"Duration: {duration}", font=duration_font, fill="white")

    # Save the updated thumbnail
    output_path = 'output_thumbnail.png'
    template.save(output_path, "PNG")
    return output_path

async def play_media(chat_id, track, message, from_loop=False, seek_time=0):
    try:
        title, duration_str, link, media_type = track["title"], track["duration"], track["link"], track["type"]
        duration = convert_duration(duration_str)
        requester_name = message.from_user.first_name
        
        # Create thumbnail with aligned title and other info
        thumbnail_path = await create_thumbnail(title, duration_str)

        # Stream preparation code
        resp, songlink = await ytdl("bestaudio" if media_type == 'audio' else "best", link)
        if resp != 1:
            await message.reply("Error playing the next track in the queue.")
            return
        
        # Use the generated thumbnail
        media_stream = MediaStream(songlink, video_flags=MediaStream.Flags.IGNORE if media_type == 'audio' else None)
        await real_pytgcalls.play(chat_id, media_stream)
        truncated_title = title if len(title) <= MAX_TITLE_LENGTH else title[:MAX_TITLE_LENGTH] + '...'
       
        reply_message = (
            f"**[ ➲ Sᴛʀᴇᴀᴍɪɴɢ Sᴛᴀʀᴛᴇᴅ |](https://t.me/mrcutex)**\n\n"
            f"➤ **Tɪᴛʟᴇ :** [{truncated_title}]({link})\n"
            f"➤ **Dᴜʀᴀᴛɪᴏɴ:** {duration_str}\n"
            f"➤ **Rᴇǫᴜᴇsᴛᴇᴅ ʙʏ:** {requester_name}"
        )

        # Send the message with the thumbnail
        if not from_loop:
            await message.reply_photo(photo=thumbnail_path, caption=reply_message)
        
        # Store stream info
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


async def fetch_thumbnail_with_retries(thumbnail_urls, retries=3):
    """Attempt to download the thumbnail with retries if it fails."""
    for url in thumbnail_urls:
        for attempt in range(retries):
            try:
                thumbnail_file = await download_thumbnail([url])
                if thumbnail_file:
                    return thumbnail_file
            except Exception as e:
                logger.warning(f"Thumbnail download failed on attempt {attempt + 1} for URL {url}: {e}")
            await asyncio.sleep(1)
    logger.error(f"Failed to download thumbnail after {retries} attempts")
    return None

# Additional functions such as poll_stream_status remain the same
@app.on_message(filters.command("play", PREFIX))
async def play(client, message):
    global stream_running
    if len(message.command) < 2:
        await message.reply("**Lᴏʟ🥲! Pʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴍᴇ ᴀ sᴏɴɢ ɴᴀᴍᴇ.**")
        return

    chat_id = message.chat.id
    query = message.text.split(" ", 1)[1]
    indicator_message = await message.reply("♫︎♫︎ **Sᴇᴀʀᴄʜɪɴɢ ғᴏʀ ᴛʜᴇ sᴏɴɢ...** ♫︎♫︎")

    try:
        await message.delete()
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        
        title, duration, link, thumbnail_url = await search_yt(query)
        await indicator_message.edit("Pʟᴇᴀsᴇ ᴡᴀɪᴛ...")
        if not link:
            await indicator_message.edit("❌ No results found for this query.")
            return

        resp, songlink = await ytdl("bestaudio", link)
        if resp == 0:
            await indicator_message.edit("⚠️ Unable to retrieve audio link.")
            return

        requester_name = message.from_user.first_name
          # Define the maximum length for the title

        # Check if there is an active stream
        if chat_id in stream_running:
            logger.info(f"Active stream found in chat {chat_id}, adding {title} to queue.")
            await add_to_queue(chat_id, title, duration, link, 'audio')

            # Fetch the thumbnail
            thumbnail_file = await fetch_thumbnail_with_retries(thumbnail_url) if thumbnail_url else None
            
            # Truncate the title if it exceeds the maximum length
            truncated_title = title if len(title) <= MAX_TITLE_LENGTH else title[:MAX_TITLE_LENGTH] + '...'

            queue_caption = (
                f"➜ **Aᴅᴅᴇᴅ ᴛᴏ ǫᴜᴇᴜᴇ:**\n\n"
                f"➤ **Tɪᴛʟᴇ:** [{truncated_title}]({link})\n"
                f"➤ **Dᴜʀᴀᴛɪᴏɴ:** {duration}\n"
                f"➤ **Rᴇǫᴜᴇsᴛᴇᴅ ʙʏ:** {requester_name}"
            )
            
            # Send the thumbnail photo along with the message
        
            await message.reply(queue_caption, disable_web_page_preview=True)

        else:
            logger.info(f"No active stream in chat {chat_id}, playing {title} directly.")
            await real_pytgcalls.play(chat_id, MediaStream(songlink, video_flags=MediaStream.Flags.IGNORE))
            
            # Only download and send the thumbnail if it exists
            thumbnail_file = await fetch_thumbnail_with_retries(thumbnail_url) if thumbnail_url else None
            
            # Truncate the title if it exceeds the maximum length
            truncated_title = title if len(title) <= MAX_TITLE_LENGTH else title[:MAX_TITLE_LENGTH] + '...'

            play_caption = (
                f"**[ ➲ Sᴛʀᴇᴀᴍɪɴɢ Sᴛᴀʀᴛᴇᴅ |](https://t.me/mrcutex)**\n\n"
                f"➤ **Tɪᴛʟᴇ :** [{truncated_title}]({link})\n"
                f"➤ **Dᴜʀᴀᴛɪᴏɴ:** {duration}\n"
                f"➤ **Rᴇǫᴜᴇsᴛᴇᴅ ʙʏ:** {requester_name}"
            )

            if thumbnail_file:
                try:
                    await message.reply_photo(thumbnail_file, caption=play_caption)
                except Exception as e:
                    logger.error(f"Error sending thumbnail: {e}")
                    await message.reply(f"{play_caption}\n⚠️ (Thumbnail failed to load)")
                finally:
                    os.remove(thumbnail_file)  # Ensure file cleanup
            else:
                await message.reply(play_caption)

            stream_running[chat_id] = {
                "start_time": time.time(),
                "duration": convert_duration(duration),
                "title": title,
                "duration_str": duration,
                "link": link,
                "type": 'audio'
            }
            asyncio.create_task(poll_stream_status(chat_id, message))
        
        await indicator_message.delete()  # Move this outside of the try-except block

    except Exception as e:
        logger.error(f"Error in play command: {e}")
        await indicator_message.edit(f"⚠️ **An error occurred:** {e}")


@app.on_message(filters.command("vplay", PREFIX))
async def vplay(client, message):
    global stream_running
    if len(message.command) < 2:
        await message.reply("Lᴏʟ🥲! Pʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴍᴇ ᴀ sᴏɴɢ ɴᴀᴍᴇ.")
        return

    chat_id = message.chat.id
    query = message.text.split(" ", 1)[1]
    indicator_message = await message.reply("♫︎♫︎ **Sᴇᴀʀᴄʜɪɴɢ ғᴏʀ ᴛʜᴇ sᴏɴɢ...** ♫︎♫︎")

    try:
        await message.delete()
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        
        title, duration, link, thumbnail_url = await search_yt(query)
        await indicator_message.edit("Pʟᴇᴀsᴇ ᴡᴀɪᴛ...")
        if not link:
            await indicator_message.edit("❌ **No results found for this query.**")
            return

        resp, songlink = await ytdl("bestvideo", link)
        if resp == 0:
            await indicator_message.edit("⚠️ **Unable to retrieve audio link.**")
            return

        requester_name = message.from_user.first_name
          # Define the maximum length for the title

        # Check if there is an active stream
        if chat_id in stream_running:
            logger.info(f"Active stream found in chat {chat_id}, adding {title} to queue.")
            await add_to_queue(chat_id, title, duration, link, 'video')

            # Fetch the thumbnail
            thumbnail_file = await fetch_thumbnail_with_retries(thumbnail_url) if thumbnail_url else None
            
            # Truncate the title if it exceeds the maximum length
            truncated_title = title if len(title) <= MAX_TITLE_LENGTH else title[:MAX_TITLE_LENGTH] + '...'

            queue_caption = (
                f"➜ **Aᴅᴅᴇᴅ ᴛᴏ ǫᴜᴇᴜᴇ:**\n\n"
                f"➤ **Tɪᴛʟᴇ:** [{truncated_title}]({link})\n"
                f"➤ **Dᴜʀᴀᴛɪᴏɴ:** {duration}\n"
                f"➤ **Rᴇǫᴜᴇsᴛᴇᴅ ʙʏ:** {requester_name}"
            )
            
            # Send the thumbnail photo along with the message
        
            await message.reply(queue_caption, disable_web_page_preview=True)

        else:
            logger.info(f"No active stream in chat {chat_id}, playing {title} directly.")
            await real_pytgcalls.play(chat_id, MediaStream(songlink))
            
            # Only download and send the thumbnail if it exists
            thumbnail_file = await fetch_thumbnail_with_retries(thumbnail_url) if thumbnail_url else None
            
            # Truncate the title if it exceeds the maximum length
            truncated_title = title if len(title) <= MAX_TITLE_LENGTH else title[:MAX_TITLE_LENGTH] + '...'

            play_caption = (
                f"**➲ Sᴛʀᴇᴀᴍɪɴɢ Sᴛᴀʀᴛᴇᴅ  |**\n\n"
                f"➤ **Tɪᴛʟᴇ :** [{truncated_title}]({link})\n"
                f"➤ **Dᴜʀᴀᴛɪᴏɴ:** {duration}\n"
                f"➤ **Rᴇǫᴜᴇsᴛᴇᴅ ʙʏ:** {requester_name}"
            )

            if thumbnail_file:
                try:
                    await message.reply_photo(thumbnail_file, caption=play_caption)
                except Exception as e:
                    logger.error(f"Error sending thumbnail: {e}")
                    await message.reply(f"{play_caption}\n⚠️ (Thumbnail failed to load)")
                finally:
                    os.remove(thumbnail_file)  # Ensure file cleanup
            else:
                await message.reply(play_caption)

            stream_running[chat_id] = {
                "start_time": time.time(),
                "duration": convert_duration(duration),
                "title": title,
                "duration_str": duration,
                "link": link,
                "type": 'video'
            }
            asyncio.create_task(poll_stream_status(chat_id, message))
        
        await indicator_message.delete()  # Move this outside of the try-except block

    except Exception as e:
        logger.error(f"Error in play command: {e}")
        await indicator_message.edit(f"⚠️ **An error occurred:** {e}")

@app.on_message(filters.command("skip", PREFIX))
async def skip(client, message):
    chat_id = message.chat.id
    if len(message.command) == 2 and message.command[1].isdigit():
        index = int(message.command[1])
        if chat_id in queues and len(queues[chat_id]) >= index:
            logger.info(f"Skipping to track {index} in chat {chat_id}.")
            for _ in range(index - 1):
                queues[chat_id].pop(0)
            next_track = queues[chat_id].pop(0)
            await play_media(chat_id, next_track, message)
        else:
            await message.reply("Invalid track number.")
    elif chat_id in stream_running:
        logger.info(f"Skipping current track in chat {chat_id}.")
        await real_pytgcalls.leave_call(chat_id)
        if chat_id in queues and queues[chat_id]:
            next_track = queues[chat_id].pop(0)
            await play_media(chat_id, next_track, message)
        else:
            stream_running.pop(chat_id, None)
            await message.reply("No more tracks in the queue.")
    else:
        await message.reply("No active stream to skip.")

@app.on_message(filters.command("queue", PREFIX))
async def queue(client, message):
    chat_id = message.chat.id
    if chat_id in queues and queues[chat_id]:
        queue_message = "Current Queue:\n"
        for idx, track in enumerate(queues[chat_id]):
            queue_message += f"{idx + 1}. {track['title']} - {track['duration_str']}\n"
        await message.reply(queue_message)
    else:
        await message.reply("The queue is empty.")

@app.on_message(filters.command("clearqueue", PREFIX))
async def clearqueue(client, message):
    chat_id = message.chat.id
    if chat_id in queues:
        queues[chat_id] = []
        await message.reply("Queue cleared successfully.")
    else:
        await message.reply("No active queue to clear.")

@app.on_message(filters.command("pause", PREFIX))
async def pause(client, message):
    chat_id = message.chat.id
    if chat_id in stream_running:
        logger.info(f"Pausing stream in chat {chat_id}.")
        await real_pytgcalls.pause_stream(chat_id)
        await message.reply("Stream paused.")
    else:
        await message.reply("No active stream to pause.")

@app.on_message(filters.command("resume", PREFIX))
async def resume(client, message):
    chat_id = message.chat.id
    if chat_id in stream_running:
        logger.info(f"Resuming stream in chat {chat_id}.")
        await real_pytgcalls.resume_stream(chat_id)
        await message.reply("Stream resumed.")
    else:
        await message.reply("No active stream to resume.")

@app.on_message(filters.command("stop", PREFIX))
async def stop(client, message):
    global stream_running
    chat_id = message.chat.id
    if chat_id in stream_running:
        logger.info(f"Stopping stream in chat {chat_id}.")
        await real_pytgcalls.leave_call(chat_id)
        del stream_running[chat_id]
        await message.reply("Stream stopped.")
    if chat_id in queues:
        queues[chat_id] = []
    else:
        await message.reply("No active stream to stop.")

@app.on_message(filters.command("loop", PREFIX))
async def loop(client, message):
    chat_id = message.chat.id
    if len(message.command) == 2 and message.command[1].isdigit():
        loop_count = int(message.command[1])
        if chat_id in stream_running:
            if loop_count > 0:
                looping[chat_id] = loop_count
                await message.reply(f"Looping current song {loop_count} times.")
            else:
                looping.pop(chat_id, None)  # Remove loop settings if set to 0
                await message.reply("Looping disabled.")
        else:
            await message.reply("No active stream to loop.")
    else:
        await message.reply("Please provide a valid number of times to loop the current song.")


@app.on_message(filters.group & filters.video_chat_ended)
async def handle_voice_chat_closed(client, message):
    chat_id = message.chat.id

    if chat_id in stream_running:
        try:
            # Leave the voice chat and stop the stream
            await real_pytgcalls.leave_call(chat_id)

            # Clear the queue and stream info
            stream_running.pop(chat_id, None)
            if chat_id in queue:
                queue[chat_id] = []

            await message.reply("🔴 Voice chat ended. Stream stopped and queue cleared.")
        except Exception as e:
            logger.error(f"Error while handling voice chat closure: {e}")
            await message.reply("⚠️ Error occurred while stopping the stream.")
    else:
        await message.reply("No active stream found to stop.")

@app.on_message(filters.command("restart", PREFIX) & filters.group)
async def restart_group(client, message):
    chat_id = message.chat.id

    try:
        # Always stop the stream and clear the queue
        if chat_id in stream_running:
            # Stop the stream and leave the group call if any
            await real_pytgcalls.leave_group_call(chat_id)
            stream_running.pop(chat_id, None)

        if chat_id in queue:
            queue[chat_id] = []  # Clear the queue for this group

        # Inform the group that the bot is restarting
        await message.reply("🔄 Restarting bot for this group...")

        # Restart the bot
        await client.restart()

        # Send a confirmation message after the bot has restarted
        await message.reply("✅ The bot has been successfully restarted for this group!")

    except Exception as e:
        logger.error(f"Error during restart: {e}")
        await message.reply(f"⚠️ Error occurred while restarting: {e}")

async def main():
    await app.start()
    await real_pytgcalls.start()
    print("Real app and PyTgCalls started")
   # asyncio.create_task(send_auto_messages())
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
