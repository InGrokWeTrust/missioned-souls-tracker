"""Generate a local, no-server HTML review sheet with clickable thumbnail cards.

No image processing, AI calls or uploads: labels export locally to CSV.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_review_page(dataset: dict) -> str:
    rows = dataset.get("samples", [])
    for row in rows:
        if not isinstance(row.get("video_id"), str):
            raise ValueError("Invalid sample")
    # Prevent untrusted titles from terminating the JSON script tag.
    encoded = json.dumps(rows, ensure_ascii=False).replace("<", "\\u003c")
    markup = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Missioned Souls — Human Reaction Review</title>
<style>
:root{font:15px system-ui,-apple-system,sans-serif;color:#e8edf5;background:#101720}
body{max-width:1280px;margin:32px auto;padding:0 18px}
h1{font-size:24px;margin:0 0 6px}
p{color:#b0bfcc;line-height:1.5}
small{color:#aab8c7}
.toolbar{position:sticky;top:0;background:#101720eF;border-bottom:1px solid #3c4858;
padding:12px 0;display:flex;align-items:center;flex-wrap:wrap;gap:12px;z-index:2}
button{cursor:pointer;border:1px solid #62758b;border-radius:8px;color:#fff;background:#243345;padding:8px 12px}
button:hover{background:#31485f}
#cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;margin-top:20px}
.card{border:1px solid #364253;border-radius:10px;overflow:hidden;background:#1c2836}
.card img{width:100%;height:150px;object-fit:cover;background:#344354}
.inner{padding:12px;display:grid;gap:7px}
.title{font-weight:650;min-height:42px;overflow-wrap:anywhere}
a{color:#a5d4ff}
.actions{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px}
.actions button{flex:1;font-size:13px;white-space:nowrap}
.selected{outline:2px solid #74c9ab}
input{width:100%;box-sizing:border-box;background:#101720;border:1px solid #526073;
border-radius:6px;padding:7px;color:#fff}
.note{margin-bottom:14px}
</style></head>
<body><h1>Missioned Souls — Thumbnail Review</h1>
<p class="note"><strong>Human-review tool.</strong> Classify only after opening the video
to verify it is specifically a Missioned Souls reaction. A familiar reactor or the word
“Neisha” alone is not enough. Uncertain items remain unlabelled. Images load from
YouTube's public thumbnail CDN; no labels leave this page unless you export CSV.</p>
<div class="toolbar">
 <span id="progress"></span>
 <button id="export">Export my reviewed labels (CSV)</button>
 <button id="clear">Clear all local selections</button>
</div>
<div id="cards"></div>
<script type="application/json" id="dataset">__DATA__</script>
<script>
(function(){
"use strict";
var items=JSON.parse(document.getElementById("dataset").textContent);
var choices={};
var notes={};
var grid=document.getElementById("cards");
function update(){document.getElementById("progress").textContent=
 Object.keys(choices).length+" reviewed / "+items.length+" candidates";}
function make(tag,cls,txt){var el=document.createElement(tag);if(cls)el.className=cls;
if(txt!==undefined)el.textContent=txt;return el;}
items.forEach(function(row){
 var id=row.video_id;
 var card=make("div","card"),img=make("img");
 img.src="https://i.ytimg.com/vi/"+encodeURIComponent(id)+"/hqdefault.jpg";
 img.loading="lazy";img.alt="YouTube thumbnail of "+id;
 card.appendChild(img);
 var inner=make("div","inner");
 inner.appendChild(make("div","title",row.title||"Video "+id));
 inner.appendChild(make("small","",row.channel||"Reactor (from saved ID history)"));
 var link=make("a","", "View video and verify context");
 link.href="https://www.youtube.com/watch?v="+encodeURIComponent(id);
 link.target="_blank";link.rel="noopener noreferrer";inner.appendChild(link);
 var actions=make("div","actions");
 [["match","Missioned Souls reaction"],["not_match","Not a reaction"],
  ["","Uncertain / skip"]].forEach(function(pair){
   var b=make("button","",pair[1]);b.type="button";
   b.addEventListener("click",function(){
    if(pair[0])choices[id]=pair[0];else delete choices[id];
    var buttons=actions.querySelectorAll("button");
    buttons.forEach(function(x){x.classList.remove("selected")});
    b.classList.add("selected");update();
   });actions.appendChild(b);
 });
 inner.appendChild(actions);
 var note=make("input");note.placeholder="Optional evidence note for this video";
 note.addEventListener("input",function(){notes[id]=note.value});inner.appendChild(note);
 card.appendChild(inner);grid.appendChild(card);
});
function csvField(s){return '"'+String(s||"").replace(/"/g,'""')+'"'}
document.getElementById("export").addEventListener("click",function(){
 var lines=["video_id,ground_truth,reviewed_by,review_notes"];
 items.forEach(function(item){var id=item.video_id;if(!choices[id])return;
  lines.push([id,choices[id],"human",notes[id]||""].map(csvField).join(","));});
 var blob=new Blob([lines.join("\r\n")+"\r\n"],{type:"text/csv;charset=utf-8"});
 var url=URL.createObjectURL(blob),a=document.createElement("a");
 a.href=url;a.download="vision-labels-reviewed.csv";a.click();
 setTimeout(function(){URL.revokeObjectURL(url)},500);
});
document.getElementById("clear").addEventListener("click",function(){
 choices={};notes={};document.querySelectorAll(".selected").forEach(function(x){
 x.classList.remove("selected");});document.querySelectorAll("input").forEach(function(x){x.value=""});update();
});
update();
})();
</script></body></html>
"""
    return markup.replace("__DATA__", encoded)


def main():
    dataset = json.loads((ROOT / "vision-unlabelled-candidates.json").read_text(encoding="utf-8"))
    dest = ROOT / "vision-human-review.html"
    dest.write_text(make_review_page(dataset), encoding="utf-8")
    print(f"Wrote browser-based review page for {len(dataset.get('samples', []))} candidates")


if __name__ == "__main__":
    main()
