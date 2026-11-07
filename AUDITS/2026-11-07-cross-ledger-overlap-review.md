# Historical cross-ledger overlap audit — 2026-11-07

**Repository:** `InGrokWeTrust/missioned-souls-tracker`  
**Scope:** Four video IDs present in both durable `history/reactions.json` and `history/live.json`.  
**Method:** Read-only examination of GitHub history-branch commits, production source at the initial-history seed revision, and the original seed workflow's GitHub Actions log. **No YouTube or Discord requests**, no history edits, no notification replay.

## Finding

The four cross-ledger entries are **historical data imported from two existing state artifacts during initial durable-ledger seeding on 2026-10-22**. They were not created by four newly observed duplicate notifications in the current tracker.

The initialization imported **112 IDs** from the old reaction bookmark's `seen_video_ids` and **5 IDs** from the live bookmark's `sent_video_ids`. The four overlaps were already in both sets at the moment those ledgers were populated.

| Exact YouTube ID | In initial 112 reaction IDs | In initial 5 live IDs | In current ledgers |
| --- | --- | --- | --- |
| `sd2y0b2Oq80` | Yes | Yes | Both |
| `dFW09eKPitM` | Yes | Yes | Both |
| `G-Z-job7gGs` | Yes | Yes | Both |
| `IhyWd6XM5Ts` | Yes | Yes | Both |

**Important distinction:** The historical `seen_video_ids` is **not exclusively proof of a successful Discord post**. The old reaction code could mark videos as seen during bootstrap **without posting to Discord**. The old live code could likewise bootstrap current live stream IDs into `sent_video_ids` without posting. The initial migration copied both sets into ledgers labeled as confirmed histories. Consequently **the overlap alone cannot prove an actual double Discord delivery, or verify that all four were live-to-VOD transitions**.

## Primary evidence (all times UTC)

1. **2026-10-22 10:36 UTC — empty histories initialized.** Commit [`01c714d`](https://github.com/InGrokWeTrust/missioned-souls-tracker/commit/01c714d567bf99294d080afda8a2ade324407bed) created empty durable history.
2. **2026-10-22 10:42:58 UTC — [one-time seed workflow](https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39765174011) ran.** Its log records downloading `last-run-state` and `live-last-run-state` artifacts. It executes `notification_history.py hydrate reactions last_run.json` and `hydrate live live_last_run.json`; output reports **112 reaction IDs, 5 live IDs**, with no YouTube or Discord calls during seeding.
3. **2026-10-22 10:43:09 UTC — initial reaction history populated.** Commit [`f920f5f`](https://github.com/InGrokWeTrust/missioned-souls-tracker/commit/f920f5f00a3591dbb3c27b5031363257bac98f6e) adds 112 IDs, including the four listed above, in a *single batch*.
4. **2026-10-22 10:43:11 UTC — initial live history populated.** Commit [`0999633`](https://github.com/InGrokWeTrust/missioned-souls-tracker/commit/09996339f2b2dbbdeb807e6c0c793af2fe9b3d25) adds five IDs, including the four overlaps, in a *single batch*.
5. The archived source in [revision `e85ed57`](https://github.com/InGrokWeTrust/missioned-souls-tracker/commit/e85ed57f20751a7ead9e76f1c8d95692c90869c8) explicitly chooses `seen_video_ids` for reaction hydration and `sent_video_ids` for livestream hydration. That same archived reaction code has the explicit `Bootstrap complete: current uploads were marked seen; no posts sent.` path, and archived live code has the equivalent `Live bootstrap: current stream IDs seeded with no Discord posts.` path.
6. The archived `historical_deliveries.json` contains **one separately listed confirmed delivery ID**, none of these four. This does not prove they were never delivered; it simply does not provide independent confirmation for them.
7. **Current snapshot 2026-11-07:** `notification-history` contains **364 unique reaction IDs** and **16 unique live IDs**. Their intersection is **exactly the same four IDs** found in the initial seed. No **new cross-ledger overlap** is evidenced by the current contents.

## Assessment

| Question | Evidence-based answer |
| --- | --- |
| Were these four intersections created by current post-seed notification logic? | **No.** All four existed in both ledgers as soon as the initial artifact migration completed |
| Does the shared YouTube video ID indicate the same video identity, rather than merely the same title? | **Yes.** Both records use the identical 11-character video ID |
| Were they physically posted twice in Discord? | **Not determined.** Ledger membership alone is insufficient; archived bookmark bootstraps may have marked IDs seen without sending |
| Were they normal live-to-archived-VOD transitions? | **Possible but unverified for each ID.** Historical YouTube live status and Discord message records were not available as independent evidence in this audit |
| Have additional overlap identities appeared in the durable ledgers since the initial seed? | **No** as of the snapshot: the same four constitute the entire current intersection |
| Is a current deduplication-code change justified by these four IDs? | **No.** Both current notification paths already load reaction and live IDs before deciding what to send; altering histories would discard useful provenance without establishing actual deliveries |

## Decision and follow-up

**No code, ledger, or registry change is recommended for these four historical IDs.** Leave both ledgers unchanged. Do not delete or re-send any listed video. Keep this evidence available when assessing future duplicate reports.

To establish an *actual historical Discord duplicate*, compare Discord message links / webhook delivery records for the **same exact video ID**, including message timestamps and channels. A repeated title or shared history entry is not enough. If a **new** cross-ledger ID appears after this baseline or a new confirmed duplicate Discord post occurs, investigate it as a distinct regression with the corresponding workflow-run and history-commit timestamps.

**Unresolved risks outside this checkpoint:** Discord delivery-state persistence across catastrophic runner/artifact loss; title-based reaction accuracy; and production observation of previous checkpoint changes. These require their own scoped reviews.
