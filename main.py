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

api_id = int(os.getenv("REAL_API_ID"))
api_hash = os.getenv("REAL_API_HASH")
session_string = os.getenv("REAL_SESSION_STRING")

# Initialize Pyrogram client and PyTgCalls
app = Client("RealAccount", api_id=api_id, api_hash=api_hash, session_string=session_string)
real_pytgcalls = PyTgCalls(app)

OWNER_ID = 7305252437
MATRIX_ID = 6432648506
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
app.set_parse_mode(enums.ParseMode.MARKDOWN)
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
        title, duration_str, link, media_type = track["title"], track["duration"], track["link"], track["type"]
        duration = convert_duration(duration_str)
        resp, songlink = await ytdl("bestaudio" if media_type == 'audio' else "best", link)
        if resp != 1:
            await message.reply("Error playing the next track in the queue.")
            return
        media_stream = MediaStream(songlink, video_flags=MediaStream.Flags.IGNORE if media_type == 'audio' else None)
        await real_pytgcalls.play(chat_id, media_stream)
        user = message.from_user.first_name
        reply_message = (
            f"**Playing:** [{title}]({link})\n"
            f"**Duration:** {duration_str}\n"
            f"**Played By:** {user}"
        )
        if not from_loop:
            await message.reply(reply_message, disable_web_page_preview=True)
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
    global stream_running
    if len(message.command) < 2:
        await message.reply("Please provide a song name.")
        return

    chat_id = message.chat.id
    query = message.text.split(" ", 1)[1]
    indicator_message = await message.reply("Searching for the song...")

    try:
        await message.delete()
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        title, duration, link = await search_yt(query)
        if not link:
            await indicator_message.edit("Sorry, no results found for this query.")
            return

        resp, songlink = await ytdl("bestaudio", link)
        if resp == 0:
            await indicator_message.edit("Sorry, unable to retrieve audio link.")
            return

        if chat_id in stream_running:
            logger.info(f"Active stream found in chat {chat_id}, adding {title} to queue.")
            await add_to_queue(chat_id, title, duration, link, 'audio')
            await message.reply(f"**Added to queue:**\n [{title}]({link})\n**Duration: {duration}", disable_web_page_preview=True)
        else:
            logger.info(f"No active stream in chat {chat_id}, playing {title} directly.")
            await real_pytgcalls.play(chat_id, MediaStream(songlink, video_flags=MediaStream.Flags.IGNORE))
            user = message.from_user.first_name
            reply_message = (
                f"**Playing:** [{title}]({link})\n"
                f"**Duration:** {duration}\n"
                f"**Played By:** {user}"
            )
            await message.reply(reply_message, disable_web_page_preview=True)
            stream_running[chat_id] = {
                "start_time": time.time(),
                "duration": convert_duration(duration),
                "title": title,
                "duration_str": duration,
                "link": link,
                "type": 'audio'
            }
            asyncio.create_task(poll_stream_status(chat_id, message))
        await indicator_message.delete()
    except Exception as e:
        logger.error(f"Error in play command: {e}")
        await indicator_message.edit(f"Sorry, unable to retrieve. Error: {e}")

@app.on_message(filters.command("vplay", PREFIX))
async def vplay(client, message):
    global stream_running
    if len(message.command) < 2:
        await message.reply("Please provide a song name.")
        return

    chat_id = message.chat.id
    query = message.text.split(" ", 1)[1]
    indicator_message = await message.reply("Searching for the song...")

    try:
        await message.delete()
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        title, duration, link = await search_yt(query)
        if not link:
            await indicator_message.edit("Sorry, no results found for this query.")
            return

        resp, songlink = await ytdl("bestvideo", link)
        if resp == 0:
            await indicator_message.edit("Sorry, unable to retrieve video link.")
            return

        if chat_id in stream_running:
            logger.info(f"Active stream found in chat {chat_id}, adding {title} to queue.")
            await add_to_queue(chat_id, title, duration, link, 'video')
            await message.reply(f"**Added to queue:**\n **Title**: [{title}]({link})\n**Duration:** {duration}", disable_web_page_preview=True)
        else:
            logger.info(f"No active stream in chat {chat_id}, playing {title} directly.")
            await real_pytgcalls.play(chat_id, MediaStream(songlink))
            user = message.from_user.first_name
            reply_message = (
                f"**Playing:** [{title}]({link})\n"
                f"**Duration:** {duration}\n"
                f"**Played By:** {user}"
            )
            await message.reply(reply_message, disable_web_page_preview=True)
            stream_running[chat_id] = {
                "start_time": time.time(),
                "duration": convert_duration(duration),
                "title": title,
                "duration_str": duration,
                "link": link,
                "type": 'video'
            }
            asyncio.create_task(poll_stream_status(chat_id, message))
        await indicator_message.delete()
    except Exception as e:
        logger.error(f"Error in vplay command: {e}")
        await indicator_message.edit(f"Sorry, unable to retrieve. Error: {e}")

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
            looping[chat_id] = loop_count
            await message.reply(f"Looping current song {loop_count} times.")
        else:
            await message.reply("No active stream to loop.")
    else:
        await message.reply("Please provide the number of times to loop the current song.")

@app.on_message(filters.command("seek", PREFIX))
async def seek(client, message):
    chat_id = message.chat.id
    if len(message.command) == 2:
        time_str = message.command[1]
        try:
            # Convert the seek time into seconds
            seek_time = int(time_str.replace('sec', '').replace('min', '').strip())
            if 'min' in time_str:
                seek_time *= 60
            
            if chat_id in stream_running:
                stream_info = stream_running[chat_id]
                current_time = time.time()
                elapsed_time = current_time - stream_info["start_time"]
                new_elapsed_time = elapsed_time + seek_time

                # Ensure the new playback position does not exceed the total duration
                if new_elapsed_time < stream_info["duration"]:
                    stream_info["start_time"] = current_time - new_elapsed_time
                    logger.info(f"Seeked forward by {seek_time} seconds. New start time: {stream_info['start_time']}")
                else:
                    await message.reply("Seek time exceeds song duration.")
            else:
                await message.reply("No active stream to seek.")
        except ValueError:
            await message.reply("Invalid seek time format. Use '10sec' or '1min'.")
    else:
        await message.reply("Please provide the time to seek, e.g., .seek 30sec or .seek 1min.")



GROUPS_FILE = "groups.json"


# Function to fetch and save group IDs
async def fetch_and_save_groups():
    try:
        async for dialog in app.get_dialogs():
            if dialog.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                save_group_id(dialog.chat.id)

        print("Groups fetched and saved successfully.")  # Debugging line
    except Exception as e:
        print(f"Error fetching groups: {e}")  # Debugging line

# Helper function to save group IDs
def save_group_id(chat_id):
    try:
        with open(GROUPS_FILE, "r") as f:
            groups = json.load(f)
    except FileNotFoundError:
        groups = []

    if chat_id not in groups:
        groups.append(chat_id)
        with open(GROUPS_FILE, "w") as f:
            json.dump(groups, f)
        print(f"Saved new group ID: {chat_id}")  # Debugging line

# Function to fetch trivia questions
async def fetch_trivia():
    try:
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        response = requests.get(url)
        data = response.json()

        if data['response_code'] == 0:
            question = data['results'][0]['question']
            correct_answer = data['results'][0]['correct_answer']
            incorrect_answers = data['results'][0]['incorrect_answers']
            options = incorrect_answers + [correct_answer]
            random.shuffle(options)

            # Create a formatted message for the trivia question
            trivia_message = f"**🎉 Trivia Challenge! 🎉**\n\n**Question:** {question}\n"
            trivia_message += "\n".join([f"{i + 1}. {option}" for i, option in enumerate(options)])
            trivia_message += f"\n\n**🕵️‍♂️ Find the correct answer in the poll below!**"
            
            return trivia_message, options, correct_answer
        else:
            print("Failed to fetch trivia. Response code:", data['response_code'])  # Debugging line
            return "Couldn't fetch trivia at the moment.", [], ""
    except Exception as e:
        print(f"Error fetching trivia: {e}")
        return "Couldn't fetch trivia due to an error.", [], ""

# Function to fetch fun facts
async def fetch_fun_fact():
    try:
        number = random.randint(1, 100)
        url = f"http://numbersapi.com/{number}/trivia"
        response = requests.get(url)

        if response.status_code == 200:
            return f"**📚 Fun Fact:** {response.text}"
        else:
            print("Failed to fetch fun fact. Status code:", response.status_code)  # Debugging line
            return "Couldn't fetch a fun fact at the moment."
    except Exception as e:
        print(f"Error fetching fun fact: {e}")
        return "Couldn't fetch a fun fact due to an error."

# Function to send auto messages to all saved groups
async def send_auto_messages():
    # Load group IDs from the file
    await fetch_and_save_groups()
    try:
        with open(GROUPS_FILE, "r") as f:
            group_ids = json.load(f)
    except FileNotFoundError:
        group_ids = []
        print("No groups found, please fetch groups first.")  # Debugging line

    while True:
        print("Sending auto messages...")  # Debugging line

        if random.choice([True, False]):
            message, options, correct_answer = await fetch_trivia()
            # Send the trivia question as a poll
            for group_id in group_ids:
                try:
                    # Create a poll with the options
                    await app.send_poll(
                        group_id,
                        question=message,
                        options=options,
                        is_anonymous=False,
                        correct_option_id=options.index(correct_answer) if correct_answer in options else 0,
                        explanation="Select the correct answer!"
                    )
                    print(f"Poll sent to group ID: {group_id}")  # Debugging line
                except Exception as e:
                    print(f"Error sending poll to group {group_id}: {e}")
        else:
            message = await fetch_fun_fact()
            for group_id in group_ids:
                try:
                    await app.send_message(group_id, message)
                    print(f"Fun fact sent to group ID: {group_id}")  # Debugging line
                except Exception as e:
                    print(f"Error sending fun fact to group {group_id}: {e}")

        await asyncio.sleep(1000)  # Wait for 5 minutes before sending the next message





async def main():
    await app.start()
    await real_pytgcalls.start()
    print("Real app and PyTgCalls started")
    asyncio.create_task(send_auto_messages())
    
    await idle()
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
