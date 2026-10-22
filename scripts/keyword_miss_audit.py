"""Independent, read-only audit of no-match uploads and ambiguous matches.

Uses the live shadow JSON already collected from the YouTube Data API;
there are NO additional API calls, Discord sends or state mutations.

Flags *possible* false negatives using clues intentionally NOT relied upon by
the keyword classifier. These are prompts for human review, never ground truth.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import random
import re

ROOT=Path(__file__).resolve().parents[1]
IN=ROOT/"keyword-live-shadow-report.json"
OUT=ROOT/"keyword-miss-audit.json"
HTML=ROOT/"keyword-human-review.html"
FLAG_RULES=[
    ("band_mentioned_anywhere",re.compile(r"missioned[\W_]*souls|mission[\W_]*souls",re.I)),
    ("nitro_or_neisha",re.compile(r"\b(?:nitro|neisha|neesha|neeisha)\b",re.I)),
    ("naces_or_stacey_or_ice",re.compile(r"\b(?:naces?|stacey|stacy|isaiah|isiah)\b",re.I)),
    ("soulmates_or_family_band",re.compile(r"\b(?:soulmates|filipino family band|sibling band)\b",re.I)),
    ("original_artist_reference",re.compile(r"youtube\.com/(?:@missionedsouls|channel/UCeTfwcnUVurw7Qrk_KF5Idw)|youtu\.be/",re.I)),
]


def independent_flags(row):
    title=row.get("title") or ""
    desc=row.get("description_excerpt") or ""
    tags=" ".join(x for x in (row.get("tags") or []) if isinstance(x,str))
    all_text=" ".join((title,desc,tags))
    flags=[name for name,p in FLAG_RULES if p.search(all_text)]
    if row.get("description_length",0)>len(desc):
        flags.append("description_truncated")
    return flags


def make_review_html(items):
    # JSON embedding with explicit escaping of closing tags from third-party titles.
    json_data=json.dumps(items,ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e")
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Missioned Souls: keyword shadow manual audit</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font:15px system-ui;background:#111923;color:#e5ecf5;margin:25px auto;
 max-width:1250px;padding:0 15px}
p,small{color:#c0cbd7} a{color:#94caff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:15px}
.card{background:#1e2a39;border:1px solid #49576b;border-radius:10px;padding:13px}
img{width:100%;aspect-ratio:16/9;object-fit:cover;background:#303d4c}
button{cursor:pointer;padding:8px;background:#344961;border:1px solid #7495b6;
 color:white;border-radius:7px}button.chosen{outline:2px solid #6ceab0}
textarea{box-sizing:border-box;width:100%;height:72px;background:#14202b;
 color:white;border:1px solid #62778c}
.description{font-size:13px;max-height:220px;overflow:auto;white-space:pre-wrap;
 overflow-wrap:anywhere;background:#182331;padding:7px;color:#b9c6d4}
.tools{position:sticky;top:0;background:#111923ee;padding:13px 0;z-index:2}
</style>
<h1>Missioned Souls — independent keyword audit</h1>
<p>Read-only human review of flagged and randomly selected videos. Open the
YouTube link and verify the video before judging. A thumbnail/title/description
alone may be misleading. Classify actual reactions as YES, confirmed unrelated
videos as NO, and unverifiable entries as UNSURE. No results transmit anywhere;
export CSV locally.</p>
<div class="tools"><span id="count"></span> <button id="export">Export reviewed CSV</button></div>
<div class="grid" id="cards"></div>
<script type="application/json" id="dataset">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById("dataset").textContent);
const answers={};const notes={};const cards=document.getElementById("cards");
function text(tag,value){let n=document.createElement(tag);n.textContent=String(value||"");return n}
function update(){document.getElementById("count").textContent=
Object.keys(answers).length+" reviewed of "+data.length;}
data.forEach(v=>{
 let card=text("article");card.className="card";let img=document.createElement("img");
 img.loading="lazy";img.src="https://i.ytimg.com/vi/"+encodeURIComponent(v.video_id)+"/hqdefault.jpg";
 img.alt="YouTube thumbnail";card.appendChild(img);
 card.appendChild(text("h3",v.title||v.video_id));
 card.appendChild(text("small",v.channel||"unknown channel"));
 let link=text("p");let a=text("a","Open and verify this video");
 a.href="https://www.youtube.com/watch?v="+encodeURIComponent(v.video_id);
 a.target="_blank";a.rel="noopener noreferrer";link.appendChild(a);card.appendChild(link);
 card.appendChild(text("small","Classifier: "+v.tier+" | "+v.sample_type+
   " | Flags: "+(v.audit_flags||[]).join(", ")));
 let detail=text("p",v.description_excerpt||"(No description returned)");
 detail.className="description";card.appendChild(detail);
 let buttons=text("div");
 [["match","YES reaction"],["not_match","NO unrelated"],["uncertain","UNSURE"]].forEach(([label,display])=>{
 let b=text("button",display);b.type="button";b.addEventListener("click",()=>{
 answers[v.video_id]=label;
 buttons.querySelectorAll("button").forEach(x=>x.classList.remove("chosen"));
 b.classList.add("chosen");update()});buttons.appendChild(b);
 });card.appendChild(buttons);
 let comment=document.createElement("textarea");
 comment.placeholder="Optional evidence notes / concerns";
 comment.addEventListener("input",()=>notes[v.video_id]=comment.value);
 card.appendChild(comment);cards.appendChild(card);
});
document.getElementById("export").addEventListener("click",()=>{
 const q=s=>'"'+String(s||"").replace(/"/g,'""')+'"';
 let lines=["video_id,review_label,reviewer,notes"];
 data.forEach(v=>{if(answers[v.video_id])
 lines.push([v.video_id,answers[v.video_id],"human",notes[v.video_id]||""].map(q).join(","))});
 const blob=new Blob([lines.join("\\r\\n")+"\\r\\n"],{type:"text/csv"});
 let u=URL.createObjectURL(blob);let a=document.createElement("a");
 a.href=u;a.download="keyword-manual-labels.csv";a.click();
 setTimeout(()=>URL.revokeObjectURL(u),500);
});update()
</script></html>""".replace("__DATA__",json_data)


def audit(data, *, random_limit=40, flagged_limit=40, seed=20261022):
    all_rows=data.get("all_scanned") or []
    review=[x for x in all_rows if x.get("tier")=="review"]
    nonmatches=[x for x in all_rows if x.get("tier")=="none"]
    flagged=[]
    ordinary=[]
    for row in nonmatches:
        new={**row,"audit_flags":independent_flags(row)}
        if new["audit_flags"]:
            flagged.append(new)
        else:
            ordinary.append(new)
    # Prefer flagged videos with actual artist mentions, but keep channel diversity.
    rng=random.Random(seed)
    rng.shuffle(flagged);rng.shuffle(ordinary)
    def balanced(rows,limit,cap=3):
        chosen=[];seen=Counter()
        for row in rows:
            cid=row.get("channel_id","")
            if seen[cid]>=cap:continue
            seen[cid]+=1;chosen.append(row)
            if len(chosen)>=limit:break
        return chosen
    flags=balanced(flagged,flagged_limit)
    random_controls=balanced(ordinary,random_limit)
    seen_ids=set()
    selected=[]
    for sample_type,items in (("needs_verification",review),
                              ("possible_miss",flags),("random_unmatched",random_controls)):
        for item in items:
            if item["video_id"] not in seen_ids:
                seen_ids.add(item["video_id"])
                selected.append({**item,"sample_type":sample_type,
                                 "audit_flags":item.get("audit_flags") or independent_flags(item),
                                 "human_ground_truth":None})
    return {
        "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "live_source_created_at":data.get("created_at"),
        "no_match_count":len(nonmatches),
        "review_count":len(review),
        "flagged_no_match_total":len(flagged),
        "unflagged_no_match_total":len(ordinary),
        "selected_review":len(review),
        "selected_possible_miss":len(flags),
        "selected_random_unmatched":len(random_controls),
        "total_for_human_review":len(selected),
        "flag_counts_all_unmatched":dict(Counter(
            flag for r in flagged for flag in r["audit_flags"])),
        "ground_truth":"UNKNOWN: do not infer precision/recall from these flags",
        "note":"Only public YouTube metadata from latest five uploads. "
               "A description mention can be unrelated boilerplate.",
        "samples":selected,
    }


def main():
    data=json.loads(IN.read_text(encoding="utf-8"))
    result=audit(data)
    OUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    HTML.write_text(make_review_html(result["samples"]),encoding="utf-8")
    for field in ("no_match_count","review_count","flagged_no_match_total",
                  "unflagged_no_match_total","selected_review",
                  "selected_possible_miss","selected_random_unmatched",
                  "total_for_human_review"):
        print(f" {field}: {result[field]}")
    print("Audit flags across unmatched uploads:",result["flag_counts_all_unmatched"])
    print("REVIEW ENTRIES")
    for row in result["samples"]:
        if row["sample_type"]!="random_unmatched":
            print(f" {row['sample_type']:18} | {row['video_id']} | "
                  f"{row['title'][:90]} | flags={','.join(row['audit_flags'])}")
    print("Human verification is required; output is NOT an accuracy metric.")


if __name__=="__main__":
    main()
