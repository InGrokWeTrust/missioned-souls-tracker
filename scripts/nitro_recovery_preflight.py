"""Inspect verified Nitro Neisha video against existing bookmark; NEVER post.

Usage:
    python -m scripts.nitro_recovery_preflight --bookmark last_run.json

No YouTube API, no Discord, no state writes. A confirmed historical video
outside the latest-five or seven-day window needs a separate, explicitly
authorised one-video recovery, not FORCE_SEND_ALL or bulk backfill.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from reaction_state import load_state, StateError
from verified_nitro_reactions import VERIFIED_NITRO_VIDEOS


def check_state(path, video_id="hf03Voz2QBM"):
    if video_id not in VERIFIED_NITRO_VIDEOS:
        raise ValueError("Requested video has not been individually verified")
    state=load_state(path,allow_bootstrap=False)
    if state.get("_migrate_legacy"):
        return {"video_id":video_id, "status":"legacy_state_requires_migration",
                "discord_posted":False,"state_changed":False}
    if video_id in state["seen_video_ids"]:
        status="already_seen_do_not_repost"
    elif any(v["video_id"]==video_id for v in state["pending"]):
        status="already_pending_do_not_duplicate"
    else:
        status="not_in_bookmark_controlled_recovery_required"
    return {"video_id":video_id,
            "uploader_id":VERIFIED_NITRO_VIDEOS[video_id],
            "status":status,"discord_posted":False,"state_changed":False,
            "limits":"Ordinary workflow scans only five latest uploads; seven-day "
                     "lookback applies to old unseen videos. This tool does not "
                     "establish publication time or whether Discord ever received it."}


def main():
    p=argparse.ArgumentParser(description="Read-only verified Nitro bookmark check")
    p.add_argument("--bookmark",required=True,help="Existing downloaded last_run.json")
    args=p.parse_args()
    result=check_state(args.bookmark)
    print(json.dumps(result,indent=2))
    if result["status"]=="not_in_bookmark_controlled_recovery_required":
        print("No automatic replay authorised. Do NOT enable FORCE_SEND_ALL.")


if __name__=="__main__":
    main()
