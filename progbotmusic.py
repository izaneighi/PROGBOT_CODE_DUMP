import asyncio

import discord
import koduck
import settings
from queue import Queue
import youtube_dl
import os
from dotenv import load_dotenv
import pandas as pd
import datetime

#TODO:
# - Can or can't steal the bot? DJ??
# - volume, playlist command to specifically add a playlist?
# - implement MAX_BOT_CLIENTS, MAX_SONG_QUEUE
# So much error checking. Not a URL, not a YT URL, strip hidden links...
# - help messages
# - hide the joke aliases
# - queue clear, queue undo (or remove tail) command

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist': 'True', 'quiet': True} #, 'ignoreerrors': True
FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
MAX_SONG_QUEUE = 20
MAX_QUEUE_DISPLAY = 5
MAX_BOT_CLIENTS = 5
MUSIC_TIMEOUT = datetime.timedelta(days=0, hours=0, minutes=1, seconds=0)
MUSIC_COLOR = 0x5058a8
FORMATTED_EMBED_LIMIT = 4096
MAX_EMBED_LIMIT = 6000

vc_queues = {}

async def _join_vc(context):
    channelGet = context["message"].author.voice.channel
    voiceGet = discord.utils.get(koduck.client.voice_clients, guild=context["message"].guild)
    if voiceGet and voiceGet.is_connected():
        # TODO: if moving channels while playing song, replay song?
        vc_queues[channelGet.id] = vc_queues.pop(voiceGet.channel.id)
        await voiceGet.move_to(channelGet)
    else:
        vc_queues[channelGet.id] = {"queue": Queue(maxsize=MAX_SONG_QUEUE),
                                    "last_modified": datetime.datetime.now().strftime(TIME_FORMAT),
                                    "loop": False}
        voiceGet = await channelGet.connect()
    return voiceGet

def _new_vc_entry():
    d = {"queue": Queue(maxsize=MAX_SONG_QUEUE),
         "now_playing": None,
         "last_modified": datetime.datetime.now().strftime(TIME_FORMAT),
         "loop": False}
    return d


async def _get_vc(ctx):
    channel = ctx["message"].author.voice.channel
    if channel.id not in vc_queues:
        await koduck.sendmessage(ctx["message"], sendembed=_display_message("Bot not in voice chat!"))
        return None
    return discord.utils.get(koduck.client.voice_clients, guild=ctx["message"].guild)


async def _validate_song(context, req_link):
    try:
        with youtube_dl.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(req_link, download=False)
            if info is None:
                return None
            return (req_link, info)
    except youtube_dl.utils.DownloadError as e:
        await koduck.sendmessage(context["message"], sendcontent="Couldn't add song! " + str(e))
        return None


async def _play_link(ctx, voiceClient, urlInfo):
    vc_queues[voiceClient.channel.id]["last_modified"] = datetime.datetime.now().strftime(TIME_FORMAT)
    try:
        voiceClient.play(discord.FFmpegPCMAudio(urlInfo["url"], **FFMPEG_OPTIONS),
                         after=lambda i: asyncio.run_coroutine_threadsafe(_after_play(ctx), koduck.client.loop))
        vc_queues[voiceClient.channel.id]["now_playing"] = urlInfo
        extraInf = {"loop": vc_queues[voiceClient.channel.id]["loop"]}
        await koduck.sendmessage(ctx["message"], sendembed=_display_song(urlInfo, extra_info=extraInf))
    except (TypeError, discord.ClientException) as e:
        await koduck.sendmessage(ctx["message"],
                                 sendembed=_display_message("Error while trying to play music!\n{}".format(str(e))))


async def _after_play(ctx):
    # this func also triggers if there's an HTTP error that cuts song off; still not sure how to catch
    voiceClient = discord.utils.get(koduck.client.voice_clients, guild=ctx["message"].guild)
    vc_queues[voiceClient.channel.id]["last_modified"] = datetime.datetime.now().strftime(TIME_FORMAT)
    if vc_queues[voiceClient.channel.id]["loop"]:
        return await _play_link(ctx, voiceClient, vc_queues[voiceClient.channel.id]["now_playing"])

    if vc_queues[voiceClient.channel.id]["queue"].empty():
        vc_queues[voiceClient.channel.id]["now_playing"] = None
        return await koduck.sendmessage(ctx["message"], sendembed=_display_message("No more songs in queue!"))

    sReq, sInfo = vc_queues[voiceClient.channel.id]["queue"].get()
    return await _play_link(ctx, voiceClient, sInfo)


def _display_queue(chId, header_message="", suppress_empty=True):
    # zipped list
    lq = list(zip(*list(vc_queues[chId]["queue"].queue)))
    if not lq:
        if suppress_empty:
            return None
        else:
            embed = discord.Embed(description="Queue is empty!",
                                  color=MUSIC_COLOR)
            return embed
    lq = lq[1]
    queue_print = min(len(lq), MAX_QUEUE_DISPLAY)
    num_lq = list(zip(range(1, queue_print + 1), lq[0:queue_print]))
    songStrings = ["{}. [**{}**]({})".format(num, q["title"], q["url"]) for num, q in num_lq]
    msg = header_message
    if header_message:
        msg += "\n"
    descript_str = "{}{}".format(msg,"\n".join(songStrings))
    if len(descript_str)  > FORMATTED_EMBED_LIMIT:
        songStrings = ["{}. {}".format(num, q["title"]) for num, q in num_lq]
        msg = header_message
        if header_message:
            msg += "\n"
        descript_str = "{}{}".format(msg,"\n".join(songStrings))
        if len(descript_str) > MAX_EMBED_LIMIT:
            descript_str = msg
    embed = discord.Embed(description=descript_str,  color=MUSIC_COLOR)
    loop_word = "On" if vc_queues[chId]["loop"] else "Off"
    embed.set_footer(text="Songs in Queue: {}; Repeat: {}".format(len(lq), loop_word))
    return embed


def _display_song(sInfo, extra_info=None):
    embed = discord.Embed(description="*Now playing...*\n[**{}**]({})".format(sInfo["title"], sInfo["url"]),
                          color=MUSIC_COLOR)
    embed.add_field(name="Channel", value="[{}]({})".format(sInfo["channel"], sInfo["channel_url"], inline=False))
    embed.add_field(name="Duration", value=str(datetime.timedelta(seconds=sInfo["duration"])))
    if extra_info:
        if "loop" in extra_info:
            repeat_bool = "Yes" if extra_info["loop"] else "No"
            embed.add_field(name="Repeat", value=repeat_bool)
    embed.set_thumbnail(url=sInfo["thumbnails"][0]["url"])
    return embed


def _display_message(descript, color=MUSIC_COLOR):
    embed = discord.Embed(description=descript,
                          color=color)
    return embed


async def play_song(context, *args, **kwargs):
    if context["message"].author.voice is None:
        return await koduck.sendmessage(context["message"], sendembed=_display_message("Can't use music player when you're not in a voice chat!"))
    if context["message"].author.voice.channel.guild != context["message"].guild:
        return await koduck.sendmessage(context["message"], sendembed=_display_message("Can't summon to different server!"))

    good_args = []
    if context["params"]:
        split_params = [i for s in context["params"] for i in s.split()]
        good_args = [await _validate_song(context, arg) for arg in split_params]
        good_args = [i for i in good_args if i is not None]
        if not good_args:
            return # all parameters were bogus

    channelGet = context["message"].author.voice.channel
    voice = discord.utils.get(koduck.client.voice_clients, guild=context["message"].guild)
    if voice and voice.is_connected():
        # already connected
        if not context["params"]:
            if voice.is_paused():
                return await resume_song(context)
            else:
                return await koduck.sendmessage(context["message"], sendembed=_display_message(":musical_note: Music already playing!"))
        # TODO: if moving channels while playing song, replay song?
        vc_queues[channelGet.id] = vc_queues.pop(voice.channel.id)
        await voice.move_to(channelGet)
    else:
        if not context["params"]:
            return await koduck.sendmessage(context["message"], sendembed=_display_message("Help message TBD"))
        vc_queues[channelGet.id] = {"queue": Queue(maxsize=MAX_SONG_QUEUE),
                                    "last_modified": datetime.datetime.now().strftime(TIME_FORMAT),
                                    "loop": False}
        voice = await channelGet.connect()

    if "custom_repeat_flag" in context:
        vc_queues[voice.channel.id]["loop"] = context["custom_repeat_flag"]
    if "force_play" in context:
        voice.stop()
    vc_queues[voice.channel.id]["last_modified"] = datetime.datetime.now().strftime(TIME_FORMAT)
    [vc_queues[voice.channel.id]["queue"].put(arg) for arg in good_args]
    if not voice.is_playing():
        url_req, url_info = vc_queues[voice.channel.id]["queue"].get()

        await _play_link(context, voice, url_info)
    if not vc_queues[voice.channel.id]["queue"].empty():
        await koduck.sendmessage(context["message"],
                                 sendembed=_display_queue(voice.channel.id, header_message="*Adding song to queue...*"),
                                 ignorecd=True)
    return


async def now_playing(context, *args, **kwargs):
    voice = await _get_vc(context)
    if voice is None:
        return
    vc_queues[voice.channel.id]["last_modified"] = datetime.datetime.now().strftime(TIME_FORMAT)
    extra_func_info = {"loop": vc_queues[voice.channel.id]["loop"]}
    return await koduck.sendmessage(context["message"],
                                    sendembed=_display_song(vc_queues[voice.channel.id]["now_playing"],
                                                            extra_info=extra_func_info))


async def pause_song(context, *args, **kwargs):
    voice = await _get_vc(context)
    if voice is None:
        return
    vc_queues[voice.channel.id]["last_modified"] = datetime.datetime.now().strftime(TIME_FORMAT)
    voice.pause()
    return await koduck.sendmessage(context["message"], sendembed=_display_message(":pause_button: Pausing song!"))


async def resume_song(context, *args, **kwargs):
    voice = await _get_vc(context)
    if voice is None:
        return
    vc_queues[voice.channel.id]["last_modified"] = datetime.datetime.now().strftime(TIME_FORMAT)
    voice.resume()
    return await koduck.sendmessage(context["message"], sendembed=_display_message(":arrow_forward: Resuming song!"))


async def skip_song(context, *args, **kwargs):
    voice = await _get_vc(context)
    if voice is None:
        return
    if voice.is_playing():
        voice.stop()

    q = vc_queues[voice.channel.id]["queue"]
    if q.empty():
        try:
            voice.play(None) # flushes out the _after_play call; if queue is not empty, not needed
        except TypeError:
            pass
        return
    url_req, url_info = q.get()

    await _play_link(context, voice, url_info)

    return


async def playskip(context, *args, **kwargs):
    voice = await _get_vc(context)
    if not context["params"]:
        return await koduck.sendmessage(context["message"], sendembed=_display_message("Need song URL!"))
    if voice is not None:
        q = vc_queues[voice.channel.id]["queue"]
        if not q.empty():
            await koduck.sendmessage(context["message"], sendembed=_display_message("Can't playskip when songs are already in the queue!"))
            return await koduck.sendmessage(context["message"], sendembed=_display_queue(voice.channel.id, suppress_empty=False))
    context["force_play"] = True
    await play_song(context, None, None)

    return


async def queue_show(context, *args, **kwargs):
    voice = await _get_vc(context)
    if voice is None:
        return
    return await koduck.sendmessage(context["message"], sendembed=_display_queue(voice.channel.id, suppress_empty=False))


async def loop_toggle(context, *args, **kwargs):
    channel = context["message"].author.voice.channel
    if not context["params"]:
        voice = await _get_vc(context)
        if voice is None:
            return
        new_loop = not vc_queues[channel.id]["loop"]
        vc_queues[channel.id]["loop"] = new_loop
        loop_msg = ":repeat_one: Repeat (single song) now enabled!" if new_loop else ":arrow_right: Repeat now disabled."
        return await koduck.sendmessage(context["message"], sendembed=_display_message(loop_msg))

    context["custom_repeat_flag"] = True
    await play_song(context, None, None)
    return


async def leave_music(context, *args, **kwargs):
    voice = await _get_vc(context)
    if voice is None:
        return
    await voice.disconnect()
    del vc_queues[voice.channel.id]
    return


async def clean_music():
    del_keys = []
    for chID, chDict in vc_queues.items():
        voiceCh = koduck.client.get_channel(chID)
        if voiceCh is None:
            del_keys.append(chID)
            continue
        voiceClient = discord.utils.get(koduck.client.voice_clients, channel=voiceCh)
        if len(voiceCh.voice_states)>1: # dc if no one else in vc
            if not chDict["queue"].empty():
                continue
            if (datetime.datetime.now() - datetime.datetime.strptime(chDict["last_modified"], TIME_FORMAT)) < MUSIC_TIMEOUT:
                continue
            if voiceClient.is_playing():
                continue
        # don't know where to send message, so...
        # await koduck.sendmessage(context["message"], sendembed=_display_queue(voice.channel.id), ignorecd=True)
        await voiceClient.disconnect()
        del_keys.append(chID)
    for key in del_keys: del vc_queues[key]
    return

