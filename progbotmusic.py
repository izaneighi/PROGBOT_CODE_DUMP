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
# - Can't steal the bot
# - playskip, np, volume, playlist?? loop settings
# - queue
# - fuckoff
# - DJ??? or all the bot stealing
# - On leave, remove all items from queue
# - add limits

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist': 'True', 'quiet': True} #, 'ignoreerrors': True
FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
MAX_SONG_QUEUE = 20
MAX_BOT_CLIENTS = 5
MUSIC_TIMEOUT = datetime.timedelta(days=0, hours=0, minutes=1, seconds=0)
MUSIC_COLOR = 0x5058a8

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


async def _get_vc(context):
    channel = context["message"].author.voice.channel
    voice = discord.utils.get(koduck.client.voice_clients, guild=context["message"].guild)
    if voice and voice.is_connected():
        await voice.move_to(channel)
    else:
        voice = await channel.connect()
    return voice


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
                         after=lambda e: asyncio.run_coroutine_threadsafe(_after_play(ctx, urlInfo), koduck.client.loop))
        #msg = "Now playing %s" % urlInfo["title"]
        extraInf = {"loop": vc_queues[voiceClient.channel.id]["loop"]}
        await koduck.sendmessage(ctx["message"], sendembed=_display_song(urlInfo, extra_info=extraInf))
    except TypeError as e:
        msg = "Error! " + str(e)
    except discord.ClientException as e:
        msg = "Error! " + str(e)

    #await koduck.sendmessage(ctx["message"], sendcontent=msg)

async def _after_play(ctx, lastPlayed):
    voiceClient = discord.utils.get(koduck.client.voice_clients, guild=ctx["message"].guild)
    vc_queues[voiceClient.channel.id]["last_modified"] = datetime.datetime.now().strftime(TIME_FORMAT)
    print(vc_queues[voiceClient.channel.id]["loop"])
    if vc_queues[voiceClient.channel.id]["loop"]:
        await _play_link(ctx, voiceClient, lastPlayed)
        return
    if vc_queues[voiceClient.channel.id]["queue"].empty():
        return await koduck.sendmessage(ctx["message"], sendcontent="No more songs in queue!")
    sReq, sInfo = vc_queues[voiceClient.channel.id]["queue"].get()
    await _play_link(ctx, voiceClient, sInfo)
    return

def _display_queue(chId, header_message=""):
    # zipped list
    lq = list(zip(*list(vc_queues[chId]["queue"].queue)))
    if not lq:
        return None
    lq = lq[1]
    queue_print = min(len(lq), 10)
    num_lq = list(zip(range(1, queue_print + 1), lq[0:queue_print]))
    songStrings = ["{}. [**{}**]({})".format(num, q["title"], q["url"]) for num, q in num_lq]
    msg = header_message
    if header_message:
        msg += "\n"
    embed = discord.Embed(description="{}{}".format(msg,"\n".join(songStrings)),
                          color=MUSIC_COLOR)
    embed.set_footer(text="Songs in queue: {}".format(len(lq)))
    return embed

def _display_song(sInfo, extra_info=None):
    embed = discord.Embed(description="*Now playing...*\n[**{}**]({})".format(sInfo["title"], sInfo["url"]),
                          color=MUSIC_COLOR)
    embed.add_field(name="Channel", value="[{}]({})".format(sInfo["channel"], sInfo["channel_url"], inline=False))
    embed.add_field(name="Duration", value=str(datetime.timedelta(seconds=sInfo["duration"])))
    if extra_info:
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
    #voice = _get_vc(context)
    good_args = []
    if context["params"]:
        split_params = [i for s in context["params"] for i in s.split()]
        good_args = [await _validate_song(context, arg) for arg in split_params]
        good_args = [i for i in good_args if i is not None]
        if not good_args:
            return # all parameters were bogus

    voice = await _join_vc(context) # steals the bot
    if "custom_repeat_flag" in context:
        vc_queues[voice.channel.id]["loop"] = context["custom_repeat_flag"]

    if not context["params"]: # no arguments
        if voice.is_paused():
            voice.resume()
            return await koduck.sendmessage(context["message"], sendcontent="Resuming!")
        else:
            return await koduck.sendmessage(context["message"], sendcontent="Info statement here")

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

#TODO
async def np(context, *args, **kwargs):
    return

#TODO
async def pause(context, *args, **kwargs):
    return

#TODO
async def resume(context, *args, **kwargs):
    return


async def skip_song(context, *args, **kwargs):
    channel = context["message"].author.voice.channel
    if channel.id not in vc_queues:
        return await koduck.sendmessage(context["message"], sendcontent="Bot not in vc!")

    voice = discord.utils.get(koduck.client.voice_clients, guild=context["message"].guild)
    if voice.is_playing():
        voice.stop()

    q = vc_queues[channel.id]["queue"]
    if q.empty():
        return await koduck.sendmessage(context["message"], sendembed=_display_message("Queue is empty!"))
    url_req, url_info = q.get()

    await _play_link(context, voice, url_info)

    return


async def loop_toggle(context, *args, **kwargs):
    channel = context["message"].author.voice.channel
    if not context["params"]:
        if channel.id not in vc_queues:
            return await koduck.sendmessage(context["message"], sendcontent="Bot not in vc!")
        new_loop = not vc_queues[channel.id]["loop"]
        vc_queues[channel.id]["loop"] = new_loop
        loop_msg = "Repeat (single song) now **enabled**!" if new_loop else "Repeat now **disabled**."
        return await koduck.sendmessage(context["message"], sendembed=_display_message(loop_msg))

    #vc_queues[channel.id]["loop"] = True
    context["custom_repeat_flag"] = True
    await play_song(context, None, None)
    return


async def leave_music(context, *args, **kwargs):
    channel = context["message"].author.voice.channel
    if channel.id not in vc_queues:
        return await koduck.sendmessage(context["message"], sendcontent="Bot not in vc!")

    voice = discord.utils.get(koduck.client.voice_clients, guild=context["message"].guild)
    await voice.disconnect()
    del vc_queues[channel.id]
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

