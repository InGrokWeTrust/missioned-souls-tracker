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
.
├── .github/workflows/
│ ├── youtube-tracker.yml # Runs autoreactions.py
│ ├── discover_channels.yml # Runs discover_channels.py
│ └── check_live_streams.yml # Runs check_live_streams.py
├── autoreactions.py # Main tracker
├── discover_channels.py # Channel discovery
├── check_live_streams.py # Live stream detection
├── tracked_channels.json # List of tracked channels (committed)
├── last_run.json # Bookmark (artifact, not committed)
├── live_last_run.json # Live bookmark (artifact, not committed)
├── requirements.txt # Python dependencies
└── README.md # This file

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
Accept: application/vnd.github+json
Authorization: Bearer github_pat_...
Content-Type: application/json

---
