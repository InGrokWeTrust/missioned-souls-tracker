# Final 14 video-level human reviews — corrected verdicts

**Scope:** Draft PR #8 only. No production changes, live scans, Discord
messages or updates to historical metadata adjudications.

The user submitted a corrected final set of fourteen video-level labels:
**13 `reaction` and 1 `not_match`**. The latest explicit review supersedes
an earlier intermediate submission that marked all fourteen as reactions.
The canonical, exact user labels are stored in
`data/reaction_quality_remaining14_human_labels_20261022.csv`.

The `not_match` video is `qqhUL5a0rik` (free concert announcement).
`IjaXJDLaIEM` (million-views framing) is a human-confirmed **reaction**
although the earlier title-only metadata rule classified it as an
exclusion. This is a **potential missed reaction** for further rule
investigation, not permission to post or change the production detector.

All 37 existing *distinct selected videos* are now labelled. The
reproducible read-only report from
`python -m scripts.score_quality_reviewed37_final14` gives:

| Classifier tier | Human reactions | Human non-reactions |
| --- | ---: | ---: |
| Strong | 20 | 0 |
| Review | 6 | 1 |
| Excluded | 1 | 9 |
| **Total** | **27** | **10** |

There is **one** confirmed reaction excluded by the classifier
(`IjaXJDLaIEM`) and **zero** confirmed non-reactions marked strong
in this selected sample. A strong signal remains *shadow evidence only*,
not automatic Discord send permission.

**Sampling limitations:** These 37 titles were intentionally selected
through previous targeted review phases. They are not an independently
random or representative sample of all monitored reactor uploads.
These figures must not be presented as overall precision or recall.

The source artifacts remain separate and unchanged: original
`data/keyword_strong30_reactions_only_adjudication_20261022.json`,
previous `data/reaction_quality_next8_human_labels_20261022.csv`,
and the blank selection
`data/reaction_quality_remaining14_pending_review_20261022.json`.
The final report is independently generated as
`reaction-quality-reviewed37-with-final14.json`.

**Validation:** 118/118 offline Python tests and 20/20 keyword
fixtures passed in
https://github.com/InGrokWeTrust/missioned-souls-tracker/actions/runs/39753063946 .
PR #8 stays DRAFT and disconnected from production Discord delivery.
