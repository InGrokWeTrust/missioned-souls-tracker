"""One-time, allowlisted, claim-before-send recovery for Mr J missed reactions.

All five videos were seeded on first channel discovery on 23 Oct 2026,
not sent to Discord; no generic force-replay behavior is changed. Uses a
separate per-ID reservation file so reruns cannot send duplicates.
"""
import base64
import json
import os
import time
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from new_channel_reaction_evidence import is_strong_reaction_candidate
from video_release_policy import is_published_vod
from webhook_delivery import post_webhook

CHANNEL="UCTR_PSXLloLzLMBVxnt_kLQ"
IDS=(
    "12wK1tFGR6o",  # Black Velvet
    "MXwqmBcxx7A",  # I Hate Myself for Loving You
    "mppa-EI2Zz4",  # Hopelessly Devoted to You
    "TZ7z0pZvn8w",  # Total Eclipse of the Heart
    "RekjK8afksM",  # Bad Moon Rising
)
BRANCH="notification-history"


def metadata():
    key=os.environ["YOUTUBE_API_KEY"]
    query=urlencode({"part":"snippet,contentDetails,liveStreamingDetails,status",
                     "id":",".join(IDS),"key":key})
    with urlopen("https://www.googleapis.com/youtube/v3/videos?"+query,timeout=25) as response:
        values=json.load(response)
    results={item["id"]:item for item in values.get("items",[])}
    if set(results) != set(IDS):
        raise RuntimeError("All five approved Mr J VOD IDs must be available before recovery")
    now=datetime.now(timezone.utc)
    for id in IDS:
        item=results[id]
        snippet=item.get("snippet") or {}
        if snippet.get("channelId") != CHANNEL:
            raise RuntimeError("Wrong uploader for "+id)
        if (item.get("status") or {}).get("privacyStatus") != "public":
            raise RuntimeError("Video is not public: "+id)
        if not is_published_vod({
            "metadata_confirmed":True,
            "live_broadcast_content":snippet.get("liveBroadcastContent"),
            "live_streaming_details":item.get("liveStreamingDetails"),
        }):
            raise RuntimeError("Video not fully published as VOD: "+id)
        when=datetime.fromisoformat(snippet["publishedAt"].replace("Z","+00:00"))
        if not (timedelta(0)<=now-when<=timedelta(days=3)):
            raise RuntimeError("Video outside approved recovery age: "+id)
        desc=snippet.get("description","")
        if not is_strong_reaction_candidate({
            "title":snippet.get("title",""),
            "description":desc,
            "channel_id":CHANNEL,
        }):
            raise RuntimeError("Missing independent reaction evidence: "+id)
        import re
        d=(item.get("contentDetails") or {}).get("duration","")
        parts=re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",d)
        duration=(int(parts.group(1) or 0)*3600+
                  int(parts.group(2) or 0)*60+
                  int(parts.group(3) or 0)) if parts else 0
        if duration<120:
            raise RuntimeError("Short or unconfirmed duration: "+id)
    return results


def api(method,path,payload):
    token=os.environ["GITHUB_TOKEN"]
    repo=os.environ["GITHUB_REPOSITORY"]
    data=json.dumps(payload).encode("utf-8")
    request=Request(
        "https://api.github.com/repos/"+repo+"/contents/"+quote(path,safe="/"),
        method=method,data=data,
        headers={"Authorization":"Bearer "+token,
                 "Accept":"application/vnd.github+json",
                 "Content-Type":"application/json",
                 "X-GitHub-Api-Version":"2022-11-28"})
    with urlopen(request,timeout=25) as result:
        return json.load(result)


def claim_once(id):
    """GitHub create-only PUT is an atomic at-most-once reservation per ID."""
    path="history/mrj_recovery_20261023_"+id+".json"
    state={"version":1,"video_id":id,"channel_id":CHANNEL,
           "status":"claimed","claim_utc":datetime.now(timezone.utc).isoformat()}
    body={"message":"Reserve one-time Mr J recovery for "+id,"branch":BRANCH,
          "content":base64.b64encode((json.dumps(state)+"\n").encode()).decode()}
    try:
        result=api("PUT",path,body)
    except HTTPError as exc:
        if exc.code in (409,422):
            # Existing/ambiguous claim: do not resend. A previous attempt may
            # already have reached Discord before GitHub received its receipt.
            print("⏭️ Recovery already reserved or ambiguous, skipping "+id)
            return None
        raise
    sha=(result.get("content") or {}).get("sha")
    if not sha:
        raise RuntimeError("Claim created but receipt SHA unavailable; abort")
    return path,sha,state


def mark_delivered(id, claim):
    path,sha,state=claim
    state["status"]="delivered"
    state["delivered_utc"]=datetime.now(timezone.utc).isoformat()
    body={"message":"Record confirmed one-time Mr J recovery for "+id,
          "branch":BRANCH,"sha":sha,
          "content":base64.b64encode((json.dumps(state)+"\n").encode()).decode()}
    api("PUT",path,body)


def deliver(id,meta):
    s=meta["snippet"]
    dt=datetime.fromisoformat(s["publishedAt"].replace("Z","+00:00"))
    stamp=int(dt.timestamp())
    payload={"username":"Missioned Souls Reactions",
             "embeds":[{
                 "title":s["title"],"url":"https://youtu.be/"+id,
                 "description":"Recovered earlier reaction from a newly discovered channel.",
                 "color":0x1e88e5,
                 "fields":[
                     {"name":"Channel","value":s.get("channelTitle","The Mr. J Show"),"inline":True},
                     {"name":"Posted","value":f"<t:{stamp}:t> (<t:{stamp}:R>)","inline":True},
                 ],"timestamp":s["publishedAt"]
             }]}
    return post_webhook(os.environ["DISCORD_WEBHOOK_URL"],payload)


def recover():
    if os.getenv("MRJ_ONE_TIME_RECOVERY")!="approved":
        raise RuntimeError("Explicit one-time production recovery approval missing")
    if os.getenv("GITHUB_REF")!="refs/heads/main":
        raise RuntimeError("Recovery may run only from main")
    for required in ("GITHUB_TOKEN","GITHUB_REPOSITORY","YOUTUBE_API_KEY","DISCORD_WEBHOOK_URL"):
        if not os.getenv(required):
            raise RuntimeError("Missing required secret or GitHub configuration "+required)
    videos=metadata()  # Validate ALL five before creating ANY claims or notifications.
    success=0
    for id in IDS:
        claim=claim_once(id)
        if claim is None:
            continue
        # Claim is durable before the webhook, so ambiguous failures never
        # cause an automatic second Discord message.
        if not deliver(id,videos[id]):
            raise RuntimeError("Webhook failed after recovery claim for "+id+
                               "; manual review required, no automatic retry")
        success+=1
        print("✅ Delivered one-time recovered reaction: "+id)
        mark_delivered(id,claim)
        time.sleep(1.3)
    print(f"✅ Recovery completed: {success} newly delivered, {len(IDS)-success} preclaimed; no replay.")


if __name__=="__main__":
    recover()
