"""Edit five existing recovery embeds in place; NEVER create a Discord message.

Supply exact Discord "Copy Message Link" URLs paired with video IDs.
A webhook GET must prove the original message belongs to this webhook,
channel, and video before any PATCH can occur. Zero replay or deletions.
"""
import json
import os
import re
from urllib.parse import urlparse
import requests

from recover_mrj_once import IDS

PAIRS_PATTERN = re.compile(
    r"^(?P<video>[A-Za-z0-9_-]{11})\s+"
    r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild>\d{15,22})/(?P<channel>\d{15,22})/(?P<message>\d{15,22})/?$"
)
RECOVERY_MARKER = "Recovered earlier reaction from a newly discovered channel."


def parse_message_links(raw):
    """Reject partial/ambiguous lists, duplicates, and links from other channels."""
    rows = [s.strip() for s in raw.splitlines() if s.strip()]
    if len(rows) != len(IDS):
        raise ValueError("Five exact video-ID and Discord-message-link pairs required")
    result = {}
    channel = None
    for row in rows:
        match = PAIRS_PATTERN.fullmatch(row)
        if match is None:
            raise ValueError("Expected: YOUTUBE_VIDEO_ID https://discord.com/channels/GUILD/CHANNEL/MESSAGE")
        video, current_channel, message = (
            match.group("video"), match.group("channel"), match.group("message")
        )
        if video not in IDS or video in result or message in result.values():
            raise ValueError("Unknown or duplicate video/message identifier")
        if channel is not None and channel != current_channel:
            raise ValueError("Message links must all belong to one Discord channel")
        channel = current_channel
        result[video] = message
    if set(result) != set(IDS):
        raise ValueError("Five known video IDs required, once each")
    return channel, result


def safe_webhook(url):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in
            ("discord.com", "discordapp.com")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Invalid Discord webhook URL")
    match = re.fullmatch(r"/api(?:/v\d+)?/webhooks/(\d{15,22})/([A-Za-z0-9._-]+)",
                         parsed.path)
    if match is None:
        raise ValueError("Webhook is not an unmodified Discord webhook endpoint")
    return url.rstrip("/"), match.group(1)


def read_youtube_metadata(session, api_key):
    response = session.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part":"snippet,statistics,status", "id":",".join(IDS),"key":api_key},
        timeout=30,
    )
    response.raise_for_status()
    items = {v["id"]:v for v in response.json().get("items", [])}
    if set(items) != set(IDS):
        raise RuntimeError("One or more recovery videos unavailable; no edits")
    result = {}
    for id in IDS:
        item = items[id]
        snippet = item.get("snippet") or {}
        if (snippet.get("channelId") != "UCTR_PSXLloLzLMBVxnt_kLQ"
                or (item.get("status") or {}).get("privacyStatus") != "public"):
            raise RuntimeError("Video no longer public or has unexpected uploader: "+id)
        thumbnails = snippet.get("thumbnails") or {}
        image = next((thumbnails[level]["url"] for level in
                      ("maxres","standard","high","medium","default")
                      if (thumbnails.get(level) or {}).get("url")), None)
        if not image or not image.startswith("https://"):
            raise RuntimeError("YouTube thumbnail is missing: "+id)
        result[id]={"image":image,"statistics":item.get("statistics") or {}}
    return result


def verify_existing_message(message, *, id, channel, webhook_id):
    if (str(message.get("webhook_id")) != webhook_id
            or str(message.get("channel_id")) != channel):
        raise RuntimeError("Discord post belongs to another webhook or channel: "+id)
    embeds = message.get("embeds")
    if not isinstance(embeds,list) or len(embeds)!=1:
        raise RuntimeError("Unexpected Discord embed count: "+id)
    embed=embeds[0]
    if (embed.get("url") != "https://youtu.be/"+id
            or embed.get("description") != RECOVERY_MARKER
            or not isinstance(embed.get("title"),str)
            or not embed.get("title")):
        raise RuntimeError("Discord embed is not the exact Mr J recovery: "+id)
    if not isinstance(embed.get("fields"),list):
        raise RuntimeError("Discord embed missing original fields: "+id)
    names={f.get("name") for f in embed["fields"]}
    if not {"Channel","Posted"}.issubset(names):
        raise RuntimeError("Discord embed is not the expected recovery format: "+id)
    return embed


def upgraded_embed(original, meta):
    original_fields={f["name"]:f["value"] for f in original["fields"]
                     if isinstance(f,dict) and "name" in f and "value" in f}
    stats=meta["statistics"]
    embed={
        "title":original["title"],
        "url":original["url"],
        "description":original["description"],
        "color":original.get("color",0x1e88e5),
        "timestamp":original.get("timestamp"),
        "image":{"url":meta["image"]},
        "fields":[
            {"name":"Channel","value":original_fields["Channel"],"inline":True},
            {"name":"Views","value":f"{int(stats.get('viewCount',0)):,}","inline":True},
            {"name":"Posted","value":original_fields["Posted"],"inline":True},
            {"name":"Likes","value":f"{int(stats.get('likeCount',0)):,}","inline":True},
            {"name":"Comment","value":f"{int(stats.get('commentCount',0)):,}","inline":True},
        ],
    }
    if embed["timestamp"] is None:
        embed.pop("timestamp")
    return embed


def repair(*, links, webhook_url, youtube_api_key, apply, session=None):
    """All-five preflight; no changes until every URL and embed has verified."""
    channel, ids = parse_message_links(links)
    base, webhook_id = safe_webhook(webhook_url)
    session = session or requests.Session()
    meta=read_youtube_metadata(session, youtube_api_key)
    messages = {}
    for id in IDS:
        url=f"{base}/messages/{ids[id]}"
        response=session.get(url, timeout=20)
        response.raise_for_status()
        original=verify_existing_message(response.json(),id=id,
                                         channel=channel,webhook_id=webhook_id)
        messages[id]=(url,original,upgraded_embed(original,meta[id]))

    print("✅ All five original Discord messages verified. No new posts will be made.")
    if not apply:
        print("🧪 DRY RUN: five thumbnail/metrics edits possible; no Discord writes.")
        return []
    edited=[]
    for id in IDS:
        url, original, embed = messages[id]
        if original.get("image",{}).get("url") and all(
            k in {f.get("name") for f in original["fields"]}
            for k in ("Views","Likes","Comment")
        ):
            print(f"⏭️ Already repaired: {id}")
            continue
        result=session.patch(url, json={"embeds":[embed]}, timeout=20)
        result.raise_for_status()
        updated=result.json()
        verified=verify_existing_message(updated,id=id,
                                         channel=channel,webhook_id=webhook_id)
        if verified.get("image",{}).get("url") != embed["image"]["url"]:
            raise RuntimeError("Discord did not confirm image edit for "+id)
        print(f"✅ Restored thumbnail and metrics on existing message: {id}")
        edited.append(id)
    return edited


if __name__=="__main__":
    mode=os.getenv("MRJ_EMBED_REPAIR_MODE","dry-run")
    if mode not in ("dry-run","apply"):
        raise ValueError("Expected MRJ_EMBED_REPAIR_MODE dry-run or apply")
    repair(links=os.getenv("MRJ_DISCORD_MESSAGE_LINKS",""),
           webhook_url=os.environ["DISCORD_WEBHOOK_URL"],
           youtube_api_key=os.environ["YOUTUBE_API_KEY"],
           apply=mode=="apply")
