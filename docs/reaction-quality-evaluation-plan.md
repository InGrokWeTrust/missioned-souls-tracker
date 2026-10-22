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
