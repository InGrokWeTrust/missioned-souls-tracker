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
