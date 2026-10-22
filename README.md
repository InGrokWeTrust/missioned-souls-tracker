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

## Experimental free member-keyword detection — Draft PR #4, NOT deployed

This is an optional, **report-only** experiment on `experiment/keyword-rules-v1`.
The current production YouTube-to-Discord logic is unchanged, and its existing
title-based delivery has not been altered by this draft PR.

### Matching tiers

- **Strong title evidence:** an unambiguous `Missioned Souls` name in the
  title, or the distinctive `Nitro Neisha` phrase on an already tracked
  reactor's uploads. In the experiment these are logged only; they do not
  create or send new keyword-based Discord notifications.
- **Review only:** `Neisha/Neesha + guitar/solo`,
  `Stacey/Stacy + vocals`, `Naces + bass/base/keyboard`,
  `Ice/Isaiah/Isiah + drum/barefoot`, `Sheena + vocals`,
  `Secan + bass/mixing`, or band references in descriptions/tags.
  Description boilerplate and unrelated people with common names can create
  false positives, so **NONE** of these clues are automatic-delivery evidence.
- **Ignore:** lone member names, song titles without band context, generic
  `young drummer` or `family band` labels, and instrument words alone.

`autoreactions.py` has these new **optional** environment controls:

| Variable | Default | Meaning |
|---|---|---|
| `KEYWORD_REPORT_ONLY` | `false` | Enable separate keyword discovery log of unmatched recent uploads; does not send keyword notifications |
| `MAX_KEYWORD_SCAN_VIDEOS` | `150` | Maximum video IDs fetched per scan, capped at 200 |

The original tracker still checks up to 5 latest uploads per tracked channel.
When report-only is on, it additionally batches up to 150 unmatched video IDs
in the existing YouTube Data API `videos.list` endpoint (up to 3 additional
metadata requests per run), checking video-specific descriptions and tags,
duration, and actual live/Short status. Normal filters and delivery are not
changed. An API failure in this experimental path logs a warning but does not
block normal title-based detection or notifications.

### Verified test results (22 October 2026)

- **68/68 offline GitHub regression tests pass**, including the existing
  delivery/bookmark coverage and 37 new keyword-specific tests.
- **20/20 reference fixtures produced their expected tier**, comprising
  seven publicly identified reaction titles and 13 *constructed* positive,
  ambiguous and negative examples. Detailed data:
  `data/keyword_trial_cases.json` and the uploaded
  `keyword-trial-results.json` artifact.
- [Taylor John Hardin's title-only missing-band example](https://www.youtube.com/watch?v=hf03Voz2QBM)
  classifies as **strong** based on `Nitro Neisha`.
- [HCJB Music's hidden-name multi-member example](https://www.youtube.com/watch?v=4J6dullEftA)
  classifies as **review**, not automatic: the title includes multiple members
  but excludes the band's name; its description provides their instrument
  roles.
- Known unrelated and generic-title examples did not trigger a strong match.
  `Stacey Solomon + vocal` is deliberately review-only, illustrating why
  first names and roles must never be auto-posted.

**These are functionality tests, not measured precision or recall** on a
representative, human-labelled corpus. No live API-backed trial against all
125 tracked reactors has been run with this branch. The GitHub Actions
workflow uses mocks, never accesses YouTube/Discord in its offline tests,
and does not prove the keyword report function will find every real reaction.

### Recommended deployment gate

1. Review Draft PR #4 and confirm no unintended changes to production paths.
2. Deploy disabled and run a time-bounded `KEYWORD_REPORT_ONLY=true` trial
   with the existing YouTube key, keeping Discord rules unchanged.
3. Review both candidate video titles and description context; independently
   verify all proposed matches and a random sample of nonmatches.
4. Only after precision/recall look acceptable on genuinely different real
   samples, consider a *separate* PR allowing **strong, verified** keyword
   matches to be enqueued through the existing video-ID deduplication state.

No paid AI services or new secrets are needed.


### Controlled missed-reaction audit — 22 October 2026

A second **read-only live scan** of all 125 tracked channels found the same
622 public uploads, 197 excluded, 425 eligible, 91 strong keyword signals,
two review-tier matches and 332 unmatched. No Discord delivery or state writes.

The separate scripts/keyword_miss_audit.py checked 332 unmatched uploads
using independent *audit flags* that do not count as ground truth: artist
references anywhere in description/tags, role names, video links and truncated
descriptions. **112 videos had at least one flag; 220 had none.**
Many of the 112 flags were generic YouTube links (not Missioned Souls links),
or simply long descriptions (not reaction evidence).

A channel-balanced review sample of **82** videos is saved as
keyword-human-review.html and keyword-miss-audit.json in the workflow artifact:
40 flagged possible misses, 40 random unmatched controls and 2 earlier review
candidates. Open the HTML locally after downloading the artifact; open linked
videos, mark actual reactions versus unrelated, then export CSV. These labels
are not sent to a server.

Exactly four unmatched videos contained a band-name reference in the selected
description excerpt or tags. Three were Agnes is my Dog police/crime videos
with an irrelevant copied Missioned Souls tag. The fourth was a Nickelback
reaction with an incidental band reference farther into its description.
None is a confirmed new Missioned Souls reaction on this evidence.

The two review-tier videos:
- E2tRH0B6Ow8 (Reactions with Vinny, Total Ecplipse): the actual video
  description explicitly identifies Missioned Souls' Total Eclipse of the
  Heart and refers to the upload as a reaction. Supported as genuine by its
  published video-specific description.
- jxoHvBlRABA (HCJB Music Reaction, Soulful Strings With Neisha Part 2):
  the description discusses Neisha's guitar analysis and references
  Missioned Souls, but requires independent video verification.

**Do not claim precision, recall or zero false negatives based on this sample.**
The 80 new review entries have not been manually labelled. The repeat snapshot
had no new uploads versus the earlier scan; review and sample at a later date
before considering auto-delivery. The user does not approve paid AI API billing.

Last audited workflow:
https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39724013451

Tests: https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39724013470



### Refined live keyword audit — 22 October 2026

The read-only live scan again checked all 125 tracked channels and 622 public
video uploads (425 eligible, 197 excluded, 0 API failures). The stronger
keyword tier still reports 91 strong, 2 manual review, and 332 no-match;
3 hidden-title candidates remain (Nitro Neisha, Vinny, HCJB).

We corrected a second **audit-only** source of false alarms: a generic
YouTube link and a long description are not artist evidence. Consequently,
the unmatched **suspect pool shrank from 112 to just 4** video records
with actual Missioned Souls references. All four are unrelated on the
published metadata (3 police/crime videos with copied tags and one
Nickelback reaction with a stray hashtag). Long descriptions still appear
as a completeness diagnostic, not as suspected reactions.

For balanced auditing, the review sheet now includes **46 videos**:
2 title-hidden description-supported review candidates, 4 incidental
band-reference records, and 40 randomly sampled no-match controls
(capped at 3 per channel). There are 88 passing offline tests.

Independent metadata review found:
- Vinny video E2tRH0B6Ow8: video-specific description explicitly confirms
  a Missioned Souls reaction with the original performance URL.
- HCJB video jxoHvBlRABA: video-specific description strongly identifies
  Neisha guitar-performance reaction to Tunnel of Love and Rosanna with
  Missioned Souls. Video playback has not been verified.
- Ambiguous Taylor John Hardin no-match w-Lu3DfSMFc: actual description
  identifies Ren x Chris Webby, **not** Missioned Souls.
- Ambiguous Vinny no-match P6jrwuhFHlo: actual description identifies
  Erik Gronwall, **not** Missioned Souls.
- A Filipino viral-singers compilation paU0F6MOIHo remains genuinely
  unclear from text and should not be labelled as unrelated without
  watching the video.

Provenance-preserving assessments are saved as
data/keyword_metadata_review_findings.json; **they are not independently
video-watched ground-truth labels**, and no measured recall/precision is
claimed.

Final read-only artifact includes keyword-live-shadow-report.json,
keyword-miss-audit.json and keyword-human-review.html at:
https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39724894612

All 88 offline tests and 20 curated fixture checks passed:
https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39724894617

All this remains on the unmerged draft experiment branch; production
notification code, Discord and bookmarks are unchanged. No paid API calls.


### User-completed 46-video human review (22 October 2026)

All 46 human classifications exported from the standalone HTML review page
have been preserved unchanged in data/keyword_manual_labels_20261022.csv.
They were matched one-to-one against the original live-shadow artifact
(https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39724894612)
and its classifier-tier snapshot in
data/keyword_audit_snapshot_20261022.json.

- **2 confirmed actual reactions:** jxoHvBlRABA (HCJB) and E2tRH0B6Ow8
  (Reactions with Vinny). Both had been placed in the **review** tier.
- **44 confirmed non-reactions:** all 4 keyword-mention suspect cases
  and 40 channel-balanced unmatched controls. All were in the **none** tier.
- The resulting stratified review dataset has **2/2 review positives**
  and **44/44 correct no-match controls**. No positive was missed
  in these 46 user-reviewed entries.
- **ZERO of the 91 strong-tier matches** from the entire live snapshot were
  independently reviewed as part of this 46-video set. No population-wide
  precision/recall or automatic-posting safety can be inferred from it.

To reproduce scoring, run:

    python -m scripts.score_keyword_human_review

The script refuses duplicate IDs, unverified AI labels, missing video IDs
or a mismatch between the frozen classifier snapshot and human review.
It writes keyword-human-label-results.json and deliberately sets
population_precision/population_recall to null.

**Next experimental gate:** Select a fresh, independently verified
sample of strong-tier and recent new-upload matches, including challenging
member-name collisions and true misses, before proposing automatic
Discord delivery. The free metadata + official-video-link strategy is
still a candidate for a separate, explicitly report-only test.

No paid AI/API billing, and this branch remains unmerged.


### New 30 strong-tier user reviews — 22 October 2026

The user reviewed 30 previously selected strong-keyword results from 30
different tracked channels. Original classifications and exact review notes
are preserved in data/keyword_strong30_human_labels_20261022.csv, with the
frozen selection in data/keyword_strong30_pending_review_20261022.json.

- **Raw submitted labels:** 29 match, 1 not_match.
- **One internal contradiction:** fqTvzUxJT1k (HCJB tribute to Naces) was
  labelled match while the attached human note says: *Not an actual reaction*.
  This entry is **DISPUTED, not silently corrected**.
- **Uncontested human labels:** 28 match and 1 not_match (Paper Dove),
  or 28/29 matches in the consistent portion of this risk-enriched set.
  These are human-marked **valid** videos, not established genuine reactions
  in every case, and not a population-wide accuracy estimate.
- **Confirmed separate-project exception:** r5TVQYSmZcY is a Paper Dove
  cover featuring some Missioned Souls members plus other musicians; user
  marked not_match. The detector now downgrades any Paper Dove + band-name
  title from strong to **review only**, even when the title says reacts.
- **Tribute warning:** titles referring to a tribute/dedication are also
  downgraded to **review only**, pending a clarified content policy.
- **Definition unresolved:** several other videos marked match have titles
  announcing a concert, livestream, YouTube view milestone, album cover,
  or reaction archive. If the tracker should report only fresh third-party
  reactions, these classifications need a separate definition check.

The scorer scripts/score_keyword_strong30.py refuses duplicate, missing or
nonhuman label data, preserves contradictory raw notes, and leaves
reaction_only_precision and population_precision as null. It publishes
keyword-strong30-human-results.json via the offline test workflow.

Current caution: the original 30-video selection is risk-enriched and
includes a known positive. Its 28/29 consistent-portion result must **not**
be treated as an unbiased precision estimate. The live 125-channel metadata
scan started to fail on a YouTube API metadata request during later retries;
a quota-related problem is possible but not verified because errors are
deliberately redacted to avoid exposing keys. Therefore do not claim a new
successful live trial for this stage.

To conserve the shared YouTube API quota, the experimental live workflow
is now gated behind the explicit text **[shadow-run]** in Draft PR #4's title,
instead of running on every PR update. No existing production workflow was
changed, and nothing posts to Discord.


### Scope confirmed: reactions ONLY (22 October 2026)

The user explicitly settled the content policy:

**VALID:** A new, independent third-party YouTube video genuinely reacting to,
critiquing or analysing a musical performance by Missioned Souls.

**NOT VALID:** Mere band-name mentions; concert or livestream announcements;
views/subscriber milestones; website updates/reviews; musical tributes;
covers or collabs by separate groups including Paper Dove; original
performances without a third-party reactor; reaction archives or copied
video posts without new commentary. Generic reaction hashtags or a reactor
channel's standard description do not transform these into reactions.

The 30 human strong-match labels remain unchanged in
data/keyword_strong30_human_labels_20261022.csv. A *separate* provisional
reactions-only reassessment is frozen in
data/keyword_strong30_reactions_only_adjudication_20261022.json and scored by
scripts/score_keyword_reactions_only.py:

| Provisional decision | Number | Meaning |
| --- | ---: | --- |
| Supported reaction candidates | 19 | Previously user-marked valid, with video-specific published description or reaction-oriented title supporting a performance reaction |
| Exclude for reactions-only policy | 6 | Naces musical tribute, Paper Dove cover, concert announcements (2), one-million-views milestone, website/livestream announcement |
| Requires video-content confirmation | 5 | Reaction archive, band comparison/commentary, minimal Crazy Train listing, ambiguous Cherman studio cover, ambiguous Global Take studio cover |

These 30 were intentionally risk-enriched and channel-balanced, and this
new reassessment uses previously available titles, descriptions and user
notes, **not** new independent video viewing. The 19 candidates are
*metadata-supported*, not 19 newly verified reactions. The five unresolved
videos must not become automatic alerts based on hashtags alone.

In the experimental keyword classifier:
- A title containing the band name alone now yields **review**, not strong.
- A title explicitly identifying both the band and a performance reaction
  may yield **strong evidence** (still report-only).
- The previously verified Nitro Neisha reactor example remains strong.
- Clearly irrelevant Paper Dove, musical tribute, concert promotion,
  website review, milestone or reaction-archive titles/channels yield
  **none** (excluded by conservative policy).
- A reaction in generic tags, boilerplate description, or generic reactor
  channel branding is not sufficient for a strong match.

The new scoring script validates all original 30 IDs and preserves the raw
user review unchanged, produces keyword-reactions-only-results.json, and
refuses any strong keyword classification for the six exclusions or five
uncertain videos.

**No keyword-triggered Discord delivery was activated.**
The experimental live scan remains opt-in only (PR title must include
[shadow-run]) to protect the existing YouTube quota. The production
autoreactions.py remains unchanged, including its old title-based behavior.
**Therefore these stricter keyword rules are NOT YET enforced on existing
production Discord alerts.** Implementation of production reaction-only
filters requires a separate, explicitly approved deployment and safety test.


## Final reaction-only follow-up: five user-reviewed videos (2026-10-22)

The user classified the remaining five videos:

| Video ID | Classification | Tracker decision |
| --- | --- | --- |
| VhwqMoL-t8A | repost | exclude |
| cx1s0V_8Bu4 | discussion | exclude |
| JjFzKrfhNgg | reaction | reaction candidate |
| iVeWY7mTkrE | reaction | reaction candidate |
| gXWQQNUpKcA | reaction | reaction candidate |

The labels are stored separately in
data/keyword_reactions_only_five_human_labels_20261022.csv.

The final scorer scripts/score_keyword_reactions_only_final.py
produces keyword-reactions-only-final-results.json. It keeps earlier
human labels and tentative metadata assessments unchanged.
The completed 30-video breakdown is 19 earlier metadata-supported
reactions plus 3 newly user-confirmed reactions, against 6 earlier
metadata-supported exclusions plus 2 newly user-confirmed exclusions:
**22 reaction candidates, 8 exclusions, zero unresolved**.

This sample was selected to include difficult cases and is not suitable
for estimating real-world precision or recall. The 19 earlier candidates
were not all separately rewatched under the new definition.
The three newly confirmed reactions still fall into the detector's
review tier based on title alone; they are not rejected merely because
the titles resemble studio covers.

No production changes or automatic experimental alerts were enabled.


### New evidence signal: independently uploaded reaction titles (2026-10-22)

The user verified three genuine reactions whose exact YouTube titles contain
the band name and musical-cover wording but do NOT literally say "reaction":

- gXWQQNUpKcA — **ROSANNA by Toto | Missioned Souls | Familly band studio cover**
  — uploader **Global take** (channel UC8ReQVU4PPfbxRy4B18jauw).
- iVeWY7mTkrE — **MISSIONED SOULS (MS4) "I HATE MYSELF FOR LOVING YOU"
  STUDIO COVER | THIS IS HOW YOU DO IT!!!** — uploader **Cherman Vlogs**
  (channel UCvEgHm5bWB8Rvla_Bxl83WA).
- JjFzKrfhNgg — **Missioned Souls Crazy Train** — uploader **S.E.A.T.**
  (channel UC27_PpVaZq4kYquM5v0m3lg).

The key distinguishing clue is not the presence of the word "reaction" in
a title, but that the video was uploaded by an independently tracked reactor,
**rather than** the band's official channel. This raises the priority of a
band-titled video for HUMAN REVIEW, but DOES NOT independently prove it is
a reaction. Independent reactor channels can also publish concert news,
tributes, reposts, unrelated band discussions, and separate projects.

In the unmerged experimental keyword classifier:

- The independent tracked-reactor signal must be verified using the uploads
  playlist and channel ID, not just a non-Missioned Souls display name.
- The exact official Missioned Souls YouTube channel ID is explicitly
  excluded.
- A verified tracked reactor publishing a video with the band name in the
  title but no reaction word receives **tier=review,
  review_priority=high**. It does not receive **tier=strong** and cannot
  generate an automatic Discord notification.
- Other ambiguous title-only band matches stay at **review_priority=normal**;
  historical controls and arbitrary external uploader identities are not
  treated as independently verified tracked reactors.
- Separate Paper Dove performances, tributes, event announcements and
  archived performances retain their exclusions even when uploaded by a
  tracked reactor. Generic band discussion can also be review-priority high
  and must still be manually judged.
- The read-only shadow runner and experimental title-unmatched report pass
  channel ID/source context through to the classifier; the rest of production
  delivery, bookmarks, and Discord remain unchanged.

The three human-confirmed examples are now permanent offline regression
cases. The signal improves **which videos are reviewed first**, not measured
precision or verified automatic-posting safety.


### Verified channel-history evaluation — 22 October 2026

The experimental evaluator scripts/evaluate_reactor_history.py now checks
**all 125 tracked reactor IDs** against 32 video-to-channel assignments
grounded in two previously downloaded, public YouTube Data API snapshots.
The exact video IDs and authenticated channel IDs are frozen in
data/reactor_history_video_channels_20261022.json, and the output is
reactor-history-report.json. It requires no live YouTube API requests,
no paid AI, no Discord access and no changes to bookmark state.

Evidence is kept in separate levels to avoid falsely promoting channels:

- **6 directly user-confirmed performance reaction videos** from 6 unique
  reactors: Taylor John Hardin Reactions, HCJB Music Reaction, Reactions with
  Vinny, S.E.A.T., Cherman Vlogs, and Global take.
- **4 explicitly human-confirmed band-related nonreactions**, including
  Naces tribute, Paper Dove, the reaction-archive repost and an unrelated
  general band discussion.
- **18 further metadata-supported reaction candidates**, not individually
  reconfirmed under the stricter reactions-only definition.
- **4 further metadata-assessed nonreactions**, such as concert promotions
  and a popularity milestone, not human-reverified.
- The 44 videos independently marked unrelated in the earlier review
  were **not** counted against reactors' Missioned Souls history: creators
  are allowed to react to other artists.

Only 6 of 125 channels have at least one directly confirmed true Missioned
Souls reaction in this limited review sample, and **none has two**.
That is a coverage limitation, NOT evidence that the remaining 119 channels
never publish reactions. HCJB has mixed confirmed content (one genuine
reaction and a separate nonreaction tribute), proving that a source can
publish both.

**No channel is classified as eligible for automatic notification based
on its history.** A sensible future evidence gate is at least three
distinct, manually confirmed performance reactions from the channel,
spread across more than one date, plus examination of band-related
false positives. Even meeting such a gate should only increase manual
review priority until a separate production integration is specifically
approved.

Offline regression test report:
https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39736270940

The draft branch remains unmerged. No paid API or new Discord delivery.


### Next independent reactor-history review cohort (seven videos)

The channel-history audit found only one verified reaction per identified
reactor and cannot justify channel-level automatic approvals. A frozen
follow-up review set is now recorded in
data/reactor_history_next7_pending_review_20261022.json.

These **seven genuinely unlabelled** band-related uploads were recovered
from the already saved 22 October YouTube metadata snapshot (without
performing another YouTube API request):

- HCJB Music Reaction: bRpiOxl1sdE, pbgQdCCaPo8, 7iYFOFiu1KI
- Reactions with Vinny: EN0kaZai6T4
- Global take: ayffX_R86kw, XBYttS5x4i4, b6eDBjF6sko

Several are explicitly labelled reactions by title, and one appears to be
a song dedicated to Neisha. **No verdicts were assigned by the evaluator.**
The case mix is deliberately chosen to test a trusted channel's
potential positive *and* negative band-related content.

The other three previously verified reactors (Taylor John Hardin,
S.E.A.T., Cherman Vlogs) did not have further unreviewed band-related
uploads in this particular last-five-per-channel snapshot. This is a
sampling limitation, not a statement about their entire upload history.

An offline HTML review sheet with YouTube links, thumbnails, saved public
descriptions and CSV export was generated for user classification. The
seven labels must be independently returned and verified before any
history counters are incremented.

Next test results: 
https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39736735115

The report-only experiment remains draft and unmerged, with no new
Discord posting or paid AI API use.


### Seven-video review completed; HCJB experimentally excluded (22 October 2026)

The user classified all seven additional videos from the frozen previous
YouTube shadow snapshot. Original labels are preserved unchanged as
data/reactor_history_next7_human_labels_20261022.csv:

- HCJB Music Reaction: bRpiOxl1sdE, pbgQdCCaPo8 and 7iYFOFiu1KI
  are **not** genuine performance reactions.
- Reactions with Vinny: EN0kaZai6T4 is a genuine reaction.
- Global take: ayffX_R86kw, XBYttS5x4i4 and b6eDBjF6sko
  are genuine reactions.

Cumulative band-specific human evidence in the tracked sample:

| Channel | Directly confirmed reactions | Band-specific confirmed nonreactions |
| --- | ---: | ---: |
| HCJB Music Reaction | 1 | 4 |
| Reactions with Vinny | 2 | 0 |
| Global take | 4 | 0 |

The user recommends excluding HCJB. Consequently its exact YouTube
channel ID **UCtEs-cB4K1XFnXgLrIWW1Zg** is excluded from new
*experimental keyword discovery* by keyword_detector.py. Its genuine
previous reaction (jxoHvBlRABA) is kept in historical evidence. The
tracked_channels.json registry and production autoreactions.py have
**not** been changed. Existing production title-based Discord alerts
can therefore still include HCJB until a separately approved deployment.

The new script scripts/evaluate_reactor_history_followup.py preserves the
original 32 cases, adds 7 reviewed videos with exact ID matches, and
reproduces a **39 band-related-video** audit across 125 tracked channels:
10 human-confirmed genuine reactions, 7 human-confirmed band-related
nonreactions, 18 additional unverified positive candidates, and
4 metadata-only nonreaction assessments. Of the six channels with
at least one confirmed reaction, two now have multiple confirmations.

Historical observed fractions in this hand-picked sample MUST NOT be
reported as unbiased channel reliability percentages. No channel is
auto-approved; the user-requested HCJB exclusion is keyed by exact
channel ID, not a display-name similarity.

Passing offline verification:
https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39738846687
171 tests passed plus 20/20 keyword fixtures. The updated machine-readable
report is reactor-history-followup-report.json in the CI artifact.

Live quota-consuming shadow scan was skipped. No paid APIs and no
experimental Discord posts. Draft PR remains unmerged.
