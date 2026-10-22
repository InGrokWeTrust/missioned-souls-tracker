# Reaction-quality shadow evaluation — staged, no automatic Discord

**Status:** planning-only document. No quota-consuming shadow runs are
scheduled or activated by this file. The detector stays disconnected
from the production `autoreactions.py` sender.

## Purpose

Evaluate whether the reactions-only classifier appropriately separates
independent third-party performance reactions from reposts, archives,
band discussions, tributes, promotions and unrelated other artists.

## Baseline and provenance

- Current staged offline check: 70/70 regression tests and 20/20
  curated keyword fixtures passed in GitHub Actions run 39745032243.
- Seven newly user-reviewed band-related videos remain frozen in
  `data/reactor_history_next7_human_labels_20261022.csv`.
- Those seven cases are **deliberately sampled**, so 4 valid and 3 invalid
  is not a population estimate. Do not report sample precision/recall as
  if videos were drawn randomly.
- Production's HCJB exclusion, 124-channel tracker, bookmark queue, and
  error-only Discord messages stay active and unchanged.

## Proposed evaluation period

**14 days of review** once a quota-conscious source of observations has
been explicitly approved. Target a diverse spread of uploader channels and
reaction/nonreaction content. Do not exhaust the shared YouTube quota:
live shadow scanning must be explicitly approved and bounded beforehand.
For each observation save ID, exact channel ID, title, upload timestamp,
source/provenance, detector tier, review priority, and user judgment.
Maintain both title-positive and title-negative controls. A user must
actually inspect content before calling a case human-verified.

A practical review sheet has three verdicts: `reaction`, `not_match`,
and `uncertain`. Keep uncertainty separate; do not turn it into a
negative or positive. Preserve old labels immutable when policies evolve.
Seek repeat cases from channels with mixed publishing histories, including
a sample of genuinely related discussion versus musical reaction.
Do not interpret one prior confirmed reaction as approval of all new
uploads on the channel.

## Explicit deployment gates

1. All offline tests and curated fixtures remain green on a branch
   rebased/reconciled against latest production.
2. Every previously human-confirmed nonreaction that can be located is
   either excluded from notification or retained for manual review.
3. Confirmed performance reactions lacking the word "reaction" should
   remain visible in manual review (avoid dropping them silently).
4. The band official upload source, HCJB exact channel ID, Paper Dove,
   reaction archives, tributes and dedicated songs must not get promoted
   solely by uploader reputation.
5. Prove safely that read-only staging does not mutate `last_run.json`,
   `live_last_run.json`, or invoke `post_webhook`.
6. Review false positives and false negatives **by source cohort**;
   document how samples were selected. Do not estimate population accuracy
   from the curated 7-case and 30-case audits.
7. A separate explicit production approval is mandatory before
   converting `auto_candidate` metadata into Discord delivery.
   `auto_candidate` is *not* a verified content label or send permission.

## Next execution step

Apply/validate the verified Nitro PR #7 changes on the quality staging
branch, then collect independently reviewed new evidence during the
evaluation window. Preserve the Nitro exact-video exception separately:
PR #8 itself does not activate new content posting.


## Current offline evidence checkpoint — 22 October 2026

Reconciled with production commit `e5fcfeab3bd9fad717f6a120e9c71c5f2903ed7e`.
Exact Nitro VOD matching remains in production and is not broadened by
these shadow quality tiers. The staged detector remains unwired to
Discord, so its `auto_candidate` does not trigger a message.

A reproducible 37-video selected-case report is produced with:

```bash
python -m scripts.score_quality_reviewed37
```

Human-confirmed reaction videos: 8, of which 5 are strong title evidence
and 3 need manual review; none was missed. Human-confirmed
nonreaction videos: 7, of which none is strong, 1 needs manual review,
and 6 are excluded. Another **18 metadata-supported reactions** and
**4 metadata-excluded videos** remain **unverified under strict content
review**. These are risk-enriched and selective, *not unbiased sample
accuracy or representative channel quality rates*. Source data are
frozen; the script never fetches YouTube or sends to Discord.

Next review cohort: `data/reaction_quality_next8_pending_review_20261022.json`.
It contains **six provisional positives and two provisional negatives**
from eight distinct channels, none newly human-labelled. The user can
open the embedded official YouTube video URLs and assign
`reaction`, `not_match` or `uncertain` after inspection. Selection
was deliberately enriched for ambiguity; do not extrapolate any rate.
This review queue does **not** run or notify automatically.

Continue observing new cases over the recommended 14-day window only
after explicitly agreeing on a quota-safe metadata source and review
schedule. Do not start new live YouTube scans or schedule future checks
as a side effect of merging this draft.
