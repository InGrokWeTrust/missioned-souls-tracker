# Missioned Souls Reaction Tracker

A three-part automation system that tracks YouTube reactions to **Missioned Souls** and posts new ones to Discord.

The system runs entirely on GitHub Actions, triggered externally by cron-job.org.

---

## 🎯 What It Does

| Script | Frequency | Purpose |
|---|---|---|
| `autoreactions.py` | Every 30 min | Scans tracked uploads, queues reactions by video ID, retries deliveries |
| `discover_channels.py` | Every 2 hours | Paginates recent results, retains quiet reactors and updates the tracked list |
| `check_live_streams.py` | Every 2 hours | Confirms currently active Missioned Souls streams and saves sent IDs |

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
├── reaction_state.py                # Atomic reaction ID history + pending queue
├── webhook_delivery.py              # Shared Discord retry logic
├── report_failure.py                # Failure-only workflow alerts
├── tests/                          # Offline regression suite
├── discover_channels.py             # Channel discovery
├── check_live_streams.py            # Live stream detection
├── tracked_channels.json            # List of tracked channels (committed)
├── last_run.json                    # Versioned ID history + pending queue (artifact)
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
| `BOOTSTRAP_STATE_IF_MISSING` | `false` | Emergency one-run, seed-only recovery when bookmark cannot be restored |
| `DISCOVERY_PAGES` | `2` | Recent results pages checked every discovery run |
| `MAX_TRACKED_CHANNELS` | `155` | Conservative limit to preserve daily quota headroom |

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
- Marks channels absent from discovery for 60+ days as `dormant` but keeps them tracked; missing search results are not proof of inactivity
- Commits `tracked_channels.json` back to the repo

### 2. Tracking (`autoreactions.py`)

- Loads `tracked_channels.json`
- For each channel, fetches the 5 newest videos via `playlistItems.list`
- Filters titles to those containing **"Missioned Souls"**
- Filters out shorts (`#short`, `#shorts`, `"shorts"`, duration < 120s)
- Fetches stats + duration + `liveStreamingDetails` in one batch `videos.list` call
- Keeps live/upcoming/archived streams (via `liveStreamingDetails`)
- Migrates the previous timestamp bookmark to a versioned state file with a video-ID deduplication ledger
- Keeps undelivered reactions in a persistent pending queue; sends up to `MAX_TO_SEND` oldest pending first
- Saves state before sending and after each confirmed Discord delivery; failed sends remain queued
- Excludes live/upcoming alerts (handled by the live checker), and uses the live checker artifact to suppress already-announced archived streams
- Keeps a seven-day lookback for newly discovered old uploads; pending items are not subject to that cutoff

### 3. Live Streams (`check_live_streams.py`)

- Searches YouTube for **currently live** videos mentioning **Missioned Souls**
- Filters to titles containing "Missioned Souls"
- Confirms live status using `videos.list`, then uses ID-based deduplication (stored in `live_last_run.json`, capped at 2,000 IDs)
- Sends new live streams to Discord with `🔴 LIVE:` prefix; saves an ID immediately after each successful webhook delivery

---

## 📊 Quota Usage

YouTube Data API v3 gives **10,000 units/day**.

| Script | Runs/day | Units/run | Units/day |
|---|---|---|---|
| `autoreactions.py` | 48 | 125 playlist + 1–10 video batches | ~6,000–6,500 |
| `discover_channels.py` | 12 | Up to 2 search calls | 24 search calls/day |
| `check_live_streams.py` | 12 | 1 search + up to 1 video batch | 12 search calls/day + up to 12 standard units |
| **Total** | | | **~60–66% of standard units; 36/100 search calls** |

**API unit costs:**
- `search.list` = 1 request from a **separate 100 search-calls/day bucket** (Google's updated quota scheme)
- `playlistItems.list` = 1 unit
- `videos.list` = 1 unit (up to 50 IDs per call)

**Capacity:** The discovery script defaults to a conservative limit of 155 channels; review actual Cloud Console usage before changing it. Assumes 48 tracker runs/day and no large recovery scans.

---

## 🚦 Key Design Decisions

| Decision | Rationale |
|---|---|
| **Playlist-based tracking** (not search-based) | 100% reliable for tracked channels; catches VODs of streams |
| **No backfill for new channels** | First scan of a new channel seeds its existing visible uploads by ID, preventing Discord spam |
| **Cherman Vlogs exception** | Only vlog channel to track; all other vlog channels excluded |
| **Live stream detection via `liveStreamingDetails`** | Distinguishes currently active streams from scheduled/completed ones |
| **ID-based dedup for live streams** | Handles scheduled streams with old publication timestamps and archived VOD overlap when live history is available |
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
- Check the pending queue and `seen_video_ids` in the saved reaction-state artifact; historical timestamps alone no longer decide delivery
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
| YouTube `search.list` returns at most 50 per page | Discovery checks two pages by default, but deeper results may still be missed |
| RSS feeds blocked from cloud IPs | Cannot use RSS for zero-quota tracking |
| Live stream detection latency | Up to 2 hours (based on cron schedule) |
| Cross-workflow dedup | Uses the latest retained live sent-IDs artifact; if it expires, archived-stream dedup may be incomplete |
| Exactly-once delivery | Unachievable across webhook and Actions artifact storage if GitHub stops after Discord accepts a message but before artifact upload |
| Past videos from newly added channels | Not backfilled (by design) |
| Vlog channels | Excluded except Cherman (hardcoded) |
| Discovery pruning | Search-dormant channels remain tracked rather than being deleted automatically |

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

*Updated for the reliability branch: 2026-10-22. Merge only after production bookmark preflight passes.*

---

## 🩺 Operational safeguards

- The tracker fails closed if either primary bookmark is missing or damaged. Logs explain how to restore it from an older workflow artifact.
- Artifacts are retained for seven days, including after a failed tracker run when a valid state file exists. All tracker runs in `main` share a concurrency group for safer notification ordering.
- Two separate state files remain necessary: `last_run.json` for reactions and `live_last_run.json` for live announcements. The reaction workflow downloads the latest available live state for archived stream deduplication.
- Production tracker/live-check failures send a Discord health alert **only when the actual error category is recognised** in the captured failing step output. Alerts use safe, actionable text (e.g. **YouTube API quota exceeded**) without workflow-run URLs, raw stack traces, full API errors, secrets, or debugging links. Unknown/setup-only failures stay in GitHub Actions logs and **do not** generate generic Discord warnings. `ALERT_WEBHOOK_URL` is used if configured, otherwise the existing Discord webhook. The run step uses `set -o pipefail` and `tee` to retain the Python exit status while capturing diagnostics.
- Tests run on pull requests with synthetic data and no YouTube or Discord production credentials. Use **Offline delivery tests** and **Production bookmark preflight** to verify a proposed deployment.

### One-time migration from a legacy bookmark

On the first deployment, the tracker reads the current `last_run.json` artifact containing `last_published_at`, seeds already-covered IDs and writes the versioned state. Keep an independent backup of the artifact before merging.

If the artifact has expired, **do not simply create an empty JSON file**. Restore an existing artifact if possible. The explicit emergency option `BOOTSTRAP_STATE_IF_MISSING=true` seeds currently visible uploads without posting; use it for one run only, then return it to `false`.

If the saved live state has expired, the same emergency option seeds *currently active* live stream IDs without posting them; it will not recover sent IDs for older archived streams.

### Safe deployment checklist

1. Confirm the PR's offline tests and bookmark preflight are both green.
2. Confirm the `main` branch is still healthy, including both scheduled tracker jobs and the discovery job.
3. Take copies of `last_run.json` and `live_last_run.json` artifacts before changing `main`.
4. Merge only after the preflight passes; do not trigger `FORCE_SEND_ALL` for migration.
5. Confirm that the next normal tracker and live-check runs succeed and that bookmark artifacts are regenerated.
6. Verify Discord received only intended alerts and that no redundant archived live-stream VOD was announced.

### Quota documentation

See [Google's current YouTube quota calculator](https://developers.google.com/youtube/data/determine_quota_cost). A search request consumes one of the separate daily search calls; the standard playlist and video fetches consume the general 10,000-unit pool.
