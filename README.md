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
- Failed GitHub Actions tracker runs send a warning to `ALERT_WEBHOOK_URL` if configured, otherwise to the main Discord webhook. Alerts include a link to the failed workflow run.
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

---

## Experimental AI vision vs metadata evaluation (NOT deployed)

The experiment branch `experiment/vision-metadata-eval` tests a second approach after traditional thumbnail OCR proved unreliable across different YouTube thumbnails. It does **not** alter the production reaction tracker, notification bookmarks or Discord channel.

### Why this approach

- Read video-specific titles, first portion of descriptions, and exact artist tags as supporting signals, not unquestioned proof.
- Have a vision-capable model read and interpret a public thumbnail, returning one of: `match`, `not_match`, or `uncertain`, plus visible and reaction evidence. Ambiguous images must stay `uncertain`.
- Prevent overconfident matches based only on Neisha, a generic song name, or a reactor's channel history.
- Use an **independently human-labelled** evaluation set for precision, recall, false positives, and abstention/coverage. Unknown labels must not contribute to performance claims.

### Free candidate collection

GitHub Actions workflow **Vision candidate sample - read only** collects *up to 150 UNLABELLED candidates* from public YouTube RSS feeds of up to 65 tracked reactor channels, plus five previously inspected test examples. It may collect fewer if RSS feeds are unavailable. No YouTube API key or Discord webhook is involved, and no OpenAI API calls occur. Download `vision-unlabelled-candidates` artifact to review links and supply independent ground-truth labels. Missing verification must be represented as null, not inferred from titles.

### Opt-in paid vision comparison

The separate **PAID manual vision evaluation - never production** workflow runs **only** on explicit manual dispatch on the experimental branch when you enter `YES` to approve billing. A GitHub Actions repository secret named `OPENAI_API_KEY` is required, configured in GitHub Settings → Secrets and variables → Actions; never share the key in chat or a PR. This secret is **not available from ChatGPT Plus by default**; API usage is separately billed.

Defaults: `gpt-6-luna`, a maximum of 10 calls, and a roughly USD $0.50 estimated API usage budget (plus any taxes/fees). The runner tracks token usage and enforces a call cap. The budget can be exceeded by the final request and depends on actual image processing; do not treat it as a hard billing limit. Review current [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) before execution. Optionally compare `gpt-6.1-sol` after first evaluating the lower-cost model. Every sample yields an inspectable JSON record; uncertain predictions go to manual review.

The report produces numerical precision and recall **only for cases with `ground_truth` = `match` or `not_match` and `reviewed_by` = `human`, `user`, or `manual`**. With no human-confirmed labels the metric is deliberately `null`; a large collection is *not* an accuracy test by itself.

### Safety and rollout

No automatic Discord notifications, no YouTube write access, and no production workflow edits. The API is called only from the manually approved workflow and processes public thumbnail images, titles and metadata. Avoid putting private information in the test sample. Retain results for 14 days. Start with a varied manually-labelled set (ideally 100–200 real uploads with positive and negative cases), then compare model predictions to labels before considering any separate deployment proposal. Image models can still make mistakes; threshold-free model labels are not calibrated probabilities.

### Results of the first free collection trial (22 October 2026)

The first RSS-only CI attempt could not load any of 65 YouTube channel feeds. The sampler was updated to read the existing **production last-run bookmark artifact** strictly as a **read-only** source of recent video IDs. On the later trial this provided 103 IDs plus five previously examined examples and eight videos from two accessible RSS feeds: **116 unique, unlabelled candidates** in total. The number varies between runs and does **not** represent 116 ground-truth confirmed reactions or balanced positives and negatives. RSS access remains patchy from GitHub-hosted runners, so future collection should use an authorised YouTube Data API read-only sampler if greater coverage is needed.

The free artifact includes three forms of the same dataset:

- `vision-unlabelled-candidates.json` for machine comparison.
- `vision-review-sheet.csv` for a spreadsheet-style human review.
- `vision-human-review.html` for local browser review, showing thumbnail links and exporting a small CSV of **explicit human selections**. Open this HTML file locally after downloading the artifact ZIP. It never submits labels to a server. Thumbnail images load from public YouTube hosts while viewing.

Only the user's screenshot example `hf03Voz2QBM` is prelabelled as a verified match in `data/vision-labels.csv`. The remaining cases need independent human review; do not infer ground truth from older tracker notifications. To add further verified rows, edit `data/vision-labels.csv` on the experiment branch, using columns `video_id,ground_truth,reviewed_by,review_notes`. Allowed ground-truth values: `match` or `not_match`; reviewed_by: `user`, `human`, or `manual`. Then explicitly initiate the opt-in paid vision workflow if you want model evaluation. That workflow joins human labels to samples before scoring. It cannot claim meaningful precision/recall from one label.

**Experimental branch only:** Draft PR #3 is separate from production and separate from draft OCR PR #2. All new detector components remain outside the live notification path. Even if AI vision performs well, a future production rollout would require additional validation and an explicit decision to integrate it.

### Manual-only evaluation workflow availability

GitHub requires new `workflow_dispatch` workflow files to exist on the repository's **default branch** before they can be triggered through the Actions tab. While this experiment remains only in draft PR #3, the two manual, credentialed workflows are **prepared but not runnable from the Actions UI**. The free pull-request workflow has run and verified the sample collector, and the automated suite has tested the AI request construction using mocks. No billable AI requests have occurred.

For a future live API evaluation, first review the PR and decide whether to merge **only the isolated experimental utilities and manually gated workflows** (which do not modify any existing production notification scripts). Alternatively run the Python scripts locally with a separately supplied API key. Never post API keys in a pull request, issue, or chat. The workflows require explicit `YES` opt-in after they are available on the default branch. <https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow>

A second manual YouTube-only sampler was added to obtain up to roughly 200 more varied candidate records with up to 65 read-only playlist requests (from the existing YouTube API key); it is also **not yet executed**. This reduces the positive-selection bias caused by relying on the notification bookmark alone. Human review remains essential, because an upload lacking the band's title may still be a genuine reaction.
