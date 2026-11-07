# Missioned Souls Reaction Tracker

A three-part automation system that tracks YouTube reactions to **Missioned Souls** and posts new ones to Discord.

The system runs entirely on GitHub Actions, triggered externally by cron-job.org.

---

## 🎯 What It Does

| Script | Frequency | Purpose |
|---|---|---|
| `autoreactions.py` | Hourly at :15 (user-configured) | Scans tracked channels' uploads for new reactions |
| `discover_channels.py` | Every 2 hours | Finds new reactor channels and updates the tracked list |
| `check_live_streams.py` | Every 2 hours | Detects active Missioned Souls live streams |

**Discord output:**
- New reactions: posted with title, channel, views, likes, comments, and "Posted: HH:MM (Xm ago)"
- Live streams: posted with `🔴 LIVE:` prefix and red sidebar
- Livestream **Started** times: shown in Pacific/Auckland local time (NZDT/NZST), while the Discord embed timestamp preserves the original UTC instant
- Shorts: always filtered out

---

## 📁 Repository Structure
```
.
├── .github/workflows/
│   ├── youtube-tracker.yml          # Runs autoreactions.py
│   ├── discover_channels.yml        # Runs discover_channels.py
│   └── check_live_streams.yml       # Runs check_live_streams.py
├── autoreactions.py                 # Main tracker
├── discover_channels.py             # Channel discovery
├── tracked_channel_registry.py      # Fail-closed channel registry validation and atomic writes
├── check_live_streams.py            # Live stream detection
├── tracked_channels.json            # List of tracked channels (committed)
├── last_run.json                    # Bookmark (artifact, not committed)
├── live_last_run.json               # Live bookmark (artifact, not committed)
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```
---

## ⚙️ Configuration

### GitHub Secrets

| Secret | Purpose |
|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 key |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL |

### GitHub Variables

| Variable | Default | Purpose |
|---|---|---|
| `CHANNEL_NAME` | `Missioned Souls` | Search keyword / channel to track |
| `MAX_TO_SEND` | `6` | Max reactions sent to Discord per run |
| `MAX_VIDEOS_PER_CHANNEL` | Deprecated | Replaced by fixed 50-upload API pages (1 quota unit per page) |
| `FORCE_SEND_ALL` | `false` | When `true`, sends latest N regardless of bookmark |
| `LAST_RUN_FILE` | `last_run.json` | Bookmark filename |

### GitHub Personal Access Token (PAT)

- **Type:** Fine-grained
- **Permissions:** `Actions: Read and write` (scoped to this repo)
- **Expiry:** Set to 2027-01-01 (reminder: renew by mid-December 2026)
- **Used by:** cron-job.org to trigger `workflow_dispatch`

### cron-job.org Entries

| Entry | URL | Method | Body | Schedule |
|---|---|---|---|---|
| YouTube Reaction Tracker | `https://api.github.com/repos/<user>/<repo>/actions/workflows/youtube-tracker.yml/dispatches` | POST | `{"ref":"main"}` | Hourly at :15 (user-configured) |
| Discover Reactor Channels | `.../discover_channels.yml/dispatches` | POST | `{"ref":"main"}` | Every 2 hours |
| Check Live Streams | `.../check_live_streams.yml/dispatches` | POST | `{"ref":"main"}` | Every 2 hours |

**Headers (all three):**
```
Accept: application/vnd.github+json
Authorization: Bearer github_pat_...
Content-Type: application/json
```
---

## 🧠 How It Works

### 1. Discovery (`discover_channels.py`)

- Searches YouTube for recent videos mentioning **Missioned Souls**
- Extracts unique channel IDs from results
- Adds new channels to `tracked_channels.json`
- Excludes vlog channels (except **Cherman Vlogs**)
- Marks channels absent from recent searches as dormant; retains them for upload scanning
- Saves a validated nonempty registry atomically, then commits `tracked_channels.json` back to the repo

**Registry recovery:** Missing, malformed, empty or structurally invalid `tracked_channels.json` is a workflow failure, not an empty initial setup. Scheduled discovery never recreates the registry automatically. Restore a validated file from Git history and review its contents before rerunning; new installations require an explicit, separately reviewed bootstrap.

### 2. Tracking (`autoreactions.py`)

- Loads a validated existing `tracked_channels.json`; missing/corrupt data halts scanning before any Discord posts
- For each channel, fetches up to 50 newest videos per `playlistItems.list` call (1 quota unit), with up to one extra page for a burst since the last complete scan
- Filters titles to those containing **"Missioned Souls"**
- Filters out shorts (`#short`, `#shorts`, `"shorts"`, duration < 120s)
- Fetches stats + duration + `liveStreamingDetails` in one batch `videos.list` call
- Keeps live/upcoming/archived streams (via `liveStreamingDetails`)
- Compares against bookmark (`last_run.json`), sends new ones to Discord
- Updates bookmark

### 3. Live Streams (`check_live_streams.py`)

- Searches YouTube for **currently live** videos mentioning **Missioned Souls**
- Filters to titles containing "Missioned Souls"
- Uses ID-based deduplication (stored in `live_last_run.json`, capped at 200 IDs)
- Sends new live streams to Discord with `🔴 LIVE:` prefix

---

## 🔒 Durable Discord delivery attempts

Both notification scripts write an optional `unconfirmed_discord_video_id` into their existing **version-1 GitHub history JSON** on the `notification-history` branch **before** sending a Discord webhook. The reservation is verified remotely first. After Discord returns HTTP 204, the ID is appended to confirmed history **in the same GitHub update that clears the reservation**. An explicit non-204 rejection clears the reservation without recording delivery. Timeout, runner termination and GitHub history failure leave the remote reservation unresolved and **block further automatic Discord notifications** in both workflows; neither a local JSON artifact nor a GitHub Actions job success is a substitute for delivery evidence.

An unresolved durable marker is **not** proof that Discord accepted the alert. If the workflow halts with an unresolved attempt, inspect the exact YouTube video ID, the affected Discord channel messages, and the GitHub history commit before choosing a reviewed, manual reconciliation. Never delete a marker or add a confirmed video ID just to make a job pass. Do not rerun production jobs until the uncertainty is resolved.

**Limit:** GitHub remote history protects against repeated webhooks after lost artifacts, but the pending reaction queue still lives in workflow artifacts. Losing an artifact may lose unsent queue state; this checkpoint does not claim complete recovery of every unpublished or queued VOD.

## 📊 Quota Usage

YouTube Data API v3 gives **10,000 units/day**.

| Script | Runs/day | Units/run | Units/day |
|---|---|---|---|
| `autoreactions.py` | 24 | 186 channel pages + variable metadata/catch-up | ~4,464 base playlist units/day |
| `discover_channels.py` | 12 | Up to 3 search calls/run | Up to 36 search calls/day |
| `check_live_streams.py` | 12 | 1 search + detail verification/run | 12 search calls/day + details |
| **Total** | | | **Variable: separate search allowance plus shared playlist/detail units** |

**API unit costs:**
- `search.list` = 1 call against the separate 100-search-calls/day quota
- `playlistItems.list` = 1 unit
- `videos.list` = 1 unit (up to 50 IDs per call)

**Capacity:** At the user-configured hourly :15 schedule, 186 channels need ~4,464 base playlist requests/day. Extra pages are used only when needed; video details also count against the 10,000 shared-unit budget. cron-job.org is configured separately from GitHub, so verify its actual dispatch schedule there.

**Catch-up safety:** A successful scan records `last_complete_scan_at` separately from the last delivered video. Each 50-upload page costs 1 unit, and a second page is fetched only when the prior scan timestamp is not reached. If 100 uploads are insufficient, the scan stops without advancing the cutoff. Legacy bookmarks seed the new timestamp after their first complete scan; they do not trigger unverified historical replay.

---

## 🚦 Key Design Decisions

| Decision | Rationale |
|---|---|
| **Playlist-based tracking** (not search-based) | 100% reliable for tracked channels; catches VODs of streams |
| **No backfill for new channels** | Prevents Discord spam when many channels are added at once |
| **Cherman Vlogs exception** | Only vlog channel to track; all other vlog channels excluded |
| **Live streams via `liveStreamingDetails`** | Only way to reliably detect archived stream VODs |
| **ID-based dedup for live streams** | Handles scheduled streams with old `published_at` timestamps |
| **No RSS feed** | YouTube blocks RSS from datacenter IPs (GitHub Actions) |
| **Hourly tracker frequency** | Reduces base channel scan requests by half versus every 30 minutes |
| **No CSV/HTML output** | Unused; removed to reduce runtime and complexity |

---

## 🛠️ Maintenance

### Monthly
- Check GitHub Actions logs for failures
- Check cron-job.org history for non-204 status codes
- Verify `tracked_channels.json` is growing sensibly

### Every 3 Months
- Check YouTube API quota usage in Google Cloud Console
- Review `tracked_channels.json` for inactive channels to prune manually

### Before 2027-01-01
- **Renew the fine-grained PAT** (it expires!)
- Update the `Authorization` header in all 3 cron-job.org entries

### If Quota Errors Appear (403 `quotaExceeded`)
- Wait until midnight PT for reset
- Reduce tracker frequency to 45 min or 1 hour
- Or increase discovery interval to 4-6 hours

### If a Channel Is Missed
- Manually add its channel ID to `tracked_channels.json`:

```json
"UCxxxxxxxxxxxxxxxxxxxxxx": {
  "title": "Channel Name",
  "added_at": "2026-10-08T00:00:00+00:00",
  "last_seen_at": "2026-10-08T00:00:00+00:00"
}
```
---
- Commit and push

### If Discord Posts Are Missing
- Check that the video's `published_at` is newer than the bookmark
- Use `FORCE_SEND_ALL=true` for one run to resend the latest N

---

## 🧪 Manual Testing


### Test discovery

```
Actions tab → Discover Reactor Channels → Run workflow
```

### Test tracker

```
Actions tab → YouTube Reaction Tracker → Run workflow
```

### Test live check

```
Actions tab → Check Live Streams → Run workflow
```

### Force send latest reactions

1. Set `FORCE_SEND_ALL` = `true` in GitHub Variables
2. Trigger `youtube-tracker.yml` manually
3. Set `FORCE_SEND_ALL` = `false` back

---

## 📌 Known Limitations

| Limitation | Impact |
|---|---|
| YouTube `search.list` returns only top 50 newest | Discovery may miss channels not in the top 50 |
| RSS feeds blocked from cloud IPs | Cannot use RSS for zero-quota tracking |
| Live stream detection latency | Up to 2 hours (based on cron schedule) |
| Past videos from newly added channels | Not backfilled (by design) |
| Vlog channels | Excluded except Cherman (hardcoded) |

---

## 🚀 Deployment Checklist

- [ ] Fork or clone this repo
- [ ] Set `YOUTUBE_API_KEY` secret
- [ ] Set `DISCORD_WEBHOOK_URL` secret
- [ ] Set all GitHub Variables listed above
- [ ] Create fine-grained PAT with `Actions: Read and write`
- [ ] Create 3 cron-job.org entries with the correct URLs, headers, and schedules
- [ ] Run each workflow once manually to verify
- [ ] Check Discord for test posts
- [ ] Set calendar reminder for PAT renewal (mid-Dec 2026)

---

## 📜 License

Personal project. Not licensed for redistribution.

---

*Last updated: 2026-10-08*
