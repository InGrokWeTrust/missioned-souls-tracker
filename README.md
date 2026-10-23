# Missioned Souls Reaction Tracker

A three-part automation system that tracks YouTube reactions to **Missioned Souls** and posts new ones to Discord.

The system runs entirely on GitHub Actions, triggered externally by cron-job.org.

---

## 🎯 What It Does

| Script | Frequency | Purpose |
|---|---|---|
| `autoreactions.py` | Every 30 min | Scans tracked channels' uploads for new reactions |
| `discover_channels.py` | Every 2 hours | Finds new reactor channels and updates the tracked list |
| `check_live_streams.py` | Every 2 hours | Detects active Missioned Souls live streams |

**Discord output:**
- New reactions: posted with title, channel, views, likes, comments, and "Posted: HH:MM (Xm ago)"
- Live streams: posted with `🔴 LIVE:` prefix and red sidebar
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
| `MAX_VIDEOS_PER_CHANNEL` | `5` | Videos scanned per channel |
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
| YouTube Reaction Tracker | `https://api.github.com/repos/<user>/<repo>/actions/workflows/youtube-tracker.yml/dispatches` | POST | `{"ref":"main"}` | Every 30 min |
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
- Prunes channels inactive for 60+ days
- Commits `tracked_channels.json` back to the repo

### 2. Tracking (`autoreactions.py`)

- Loads `tracked_channels.json`
- For each channel, fetches the 5 newest videos via `playlistItems.list`
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

## 📊 Quota Usage

YouTube Data API v3 gives **10,000 units/day**.

| Script | Runs/day | Units/run | Units/day |
|---|---|---|---|
| `autoreactions.py` | 48 | ~66 (channels) | ~3,200 |
| `discover_channels.py` | 12 | ~100 | ~1,200 |
| `check_live_streams.py` | 12 | ~100 | ~1,200 |
| **Total** | | | **~5,600 (56%)** ✅ |

**API unit costs:**
- `search.list` = 100 units
- `playlistItems.list` = 1 unit
- `videos.list` = 1 unit (up to 50 IDs per call)

**Capacity:** At 30-min tracker frequency + 2-hr discovery + 2-hr live checks, the system supports up to ~158 tracked channels.

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
| **30-min tracker frequency** | Doubles channel capacity vs 15-min; negligible latency impact |
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
