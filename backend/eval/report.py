"""
Render the evaluation as an HTML report, and as JSON the dashboard can read.

    python -m backend.eval.report

Two outputs, for two audiences:

  backend/eval/results/report.html        a standalone page for the write-up
  frontend/public/eval/evaluation.json    what /dashboard/evaluation fetches

The HTML is self-contained — no CDN, no build step — so it can be opened from
a file path, emailed, or printed into a report appendix without anything else
being installed. Charts are inline SVG for the same reason.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
RESULTS = os.path.join(_HERE, "results")
PUBLIC = os.path.join(_ROOT, "frontend", "public", "eval")


def bar_chart(bands: list, width: int = 640, height: int = 220) -> str:
    """
    Accuracy per confidence band, as inline SVG.

    Bar HEIGHT is accuracy and bar WIDTH is proportional to how many
    observations fall in the band — so a band with three samples cannot look
    as authoritative as one with five hundred. A plain bar chart of accuracy
    hides exactly that, and it is the difference between a curve that means
    something and one that flatters a thin tail.
    """
    used = [b for b in bands if b["n"]]
    if not used:
        return "<p>No attributions to plot.</p>"

    total = sum(b["n"] for b in used)
    pad_l, pad_b, pad_t = 46, 34, 12
    plot_w = width - pad_l - 10
    plot_h = height - pad_b - pad_t

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'role="img" aria-label="Accuracy by confidence band">']

    # Gridlines at 0/25/50/75/100%.
    for pct in (0, 25, 50, 75, 100):
        y = pad_t + plot_h - (pct / 100) * plot_h
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - 10}" '
                     f'y2="{y:.1f}" stroke="currentColor" stroke-opacity="0.12"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'font-size="10" fill="currentColor" opacity="0.55">{pct}%</text>')

    x = pad_l + 2
    for b in used:
        w = max(14.0, (b["n"] / total) * (plot_w - 4 * len(used)))
        acc = b["correct"] / b["n"]
        h = acc * plot_h
        y = pad_t + plot_h - h
        # Red below the accept threshold, green above: the colour says which
        # side of the operating point the band sits on.
        colour = "#16a34a" if b["low"] >= 0.75 else (
            "#d97706" if b["low"] >= 0.5 else "#dc2626")
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{max(1, h):.1f}" '
            f'fill="{colour}" opacity="0.85"><title>{b["low"]:.1f}-{b["high"]:.1f}: '
            f'{b["correct"]}/{b["n"]} = {acc:.1%}</title></rect>')
        parts.append(
            f'<text x="{x + w / 2:.1f}" y="{height - pad_b + 14}" '
            f'text-anchor="middle" font-size="9" fill="currentColor" '
            f'opacity="0.7">{b["low"]:.1f}</text>')
        parts.append(
            f'<text x="{x + w / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
            f'font-size="9" fill="currentColor" opacity="0.8">n={b["n"]}</text>')
        x += w + 4

    parts.append(f'<text x="{pad_l}" y="{height - 4}" font-size="10" '
                 f'fill="currentColor" opacity="0.6">confidence band '
                 f'(bar width = share of observations)</text>')
    parts.append("</svg>")
    return "".join(parts)


def confusion_table(confusion: dict) -> str:
    if not confusion:
        return "<p>No attributions to tabulate.</p>"
    rows = sorted(confusion)
    cols = sorted({c for r in confusion.values() for c in r})
    names = [c for c in cols if c not in ("UNKNOWN", "(no output)")]
    tail = [c for c in cols if c in ("UNKNOWN", "(no output)")]
    cols = names + tail

    out = ['<table class="cm"><thead><tr><th></th>']
    for c in cols:
        out.append(f"<th>{c}</th>")
    out.append("</tr></thead><tbody>")
    for r in rows:
        out.append(f"<tr><th>{r}</th>")
        for c in cols:
            v = confusion.get(r, {}).get(c, 0)
            diag = (r == c)
            cls = "hit" if (diag and v) else ("miss" if v and c not in
                                              ("UNKNOWN", "(no output)") else "")
            out.append(f'<td class="{cls}">{v or ""}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def build_html(data: dict) -> str:
    o = data.get("overall", {})
    scenarios = data.get("scenarios", {})
    simulated = data.get("source") != "hand-labelled"
    when = datetime.now(timezone.utc).strftime("%d %b %Y")

    merged = {}
    for s in scenarios.values():
        for truth, preds in (s.get("confusion") or {}).items():
            row = merged.setdefault(truth, {})
            for p, n in preds.items():
                row[p] = row.get(p, 0) + n

    css = """
:root{--bg:#ffffff;--ink:#0b0a0c;--muted:#57545c;--line:#e7e5e9;--soft:#fafafa;
      --accent:#c7362f;--good:#16a34a;--warn:#d97706}
@media(prefers-color-scheme:dark){:root{--bg:#0f0e11;--ink:#f4f3f5;
      --muted:#a5a1ab;--line:#2a272e;--soft:#171519;--accent:#e05a52}}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 64px;background:var(--bg);color:var(--ink);
     font:15px/1.6 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:28px;letter-spacing:-.02em;margin:0 0 4px}
h2{font-size:18px;letter-spacing:-.01em;margin:36px 0 10px;
   padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:22px 0 6px}
p{color:var(--muted);margin:8px 0}
.sub{color:var(--muted);font-size:13px;margin-bottom:22px}
.headline{background:var(--soft);border:1px solid var(--line);border-radius:12px;
          padding:18px 20px;margin:18px 0}
.headline .big{font-size:21px;font-weight:800;color:var(--ink);letter-spacing:-.01em}
.note{border-left:3px solid var(--accent);background:var(--soft);
      padding:12px 16px;margin:16px 0;font-size:13.5px;border-radius:0 8px 8px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:14px 0}
.card{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--soft)}
.card .k{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}
.card .v{font-size:22px;font-weight:800;margin-top:3px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left}
th{background:var(--soft);font-weight:700}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
table.cm td{text-align:center;font-variant-numeric:tabular-nums}
table.cm td.hit{background:rgba(22,163,74,.16);font-weight:700}
table.cm td.miss{background:rgba(220,38,38,.16);font-weight:700}
.chart{border:1px solid var(--line);border-radius:10px;padding:12px;background:var(--soft);margin:12px 0}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);
       font-size:12px;color:var(--muted)}
code{background:var(--soft);padding:1px 5px;border-radius:4px;font-size:12.5px}
"""

    H = []
    a = H.append
    a("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    a("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    a("<title>VisionWorks — Evaluation</title>")
    a(f"<style>{css}</style></head><body><div class='wrap'>")

    a("<h1>Identity tracking — evaluation</h1>")
    a(f"<p class='sub'>Step 17 of the build plan &middot; generated {when}</p>")

    a("<div class='headline'>")
    a(f"<div class='big'>{o.get('headline', 'n/a')}</div>")
    a("<p style='margin-top:8px'>Accuracy is measured over the cases the "
      "system <em>answered</em>; coverage says how many it answered. The two "
      "are reported separately and never multiplied, because a single number "
      "cannot describe a system that is allowed to say “I don’t know”.</p>")
    a("</div>")

    if simulated:
        a("<div class='note'><strong>These figures come from a simulated "
          "office.</strong> The tracker, the stitching, the seat binding, the "
          "confidence floor and the aggregation are the real production code — "
          "only the appearance vectors are generated, to distributions "
          "measured from real OSNet output (same person 0.877, different "
          "people 0.31). Run <code>python -m backend.eval.label --video "
          "&lt;file&gt; --extract</code> to regenerate every number here from "
          "real footage.</div>")

    a("<div class='grid'>")
    for k, v in (("Accuracy", f"{o.get('accuracy', 0) * 100:.1f}%"),
                 ("Coverage", f"{o.get('coverage', 0) * 100:.1f}%"),
                 ("Wrong names", str(o.get("wrong", 0))),
                 ("Abstention", f"{o.get('abstention_rate', 0) * 100:.1f}%"),
                 ("Correct rejections",
                  f"{o.get('correct_rejections', 0)}/{o.get('strangers', 0)}")):
        a(f"<div class='card'><div class='k'>{k}</div><div class='v'>{v}</div></div>")
    a("</div>")

    a("<h2>Accuracy vs confidence</h2>")
    a("<p>The curve that matters: when the system speaks at a given "
      "confidence, how often is it right? Bar width is the share of "
      "observations in that band, so a thin tail cannot masquerade as a "
      "result.</p>")
    a(f"<div class='chart'>{bar_chart(o.get('bands', []))}</div>")

    a("<h2>Per scenario</h2>")
    a("<table><thead><tr><th>Scenario</th><th class='num'>Correct</th>"
      "<th class='num'>Wrong</th><th class='num'>Abstained</th>"
      "<th class='num'>Accuracy</th><th class='num'>Coverage</th></tr></thead><tbody>")
    for name, s in scenarios.items():
        a(f"<tr><td>{name.replace('_', ' ')}</td>"
          f"<td class='num'>{s.get('correct', 0)}</td>"
          f"<td class='num'>{s.get('wrong', 0)}</td>"
          f"<td class='num'>{s.get('abstained', 0)}</td>"
          f"<td class='num'>{s.get('accuracy', 0) * 100:.1f}%</td>"
          f"<td class='num'>{s.get('coverage', 0) * 100:.1f}%</td></tr>")
    a("</tbody></table>")

    # Stitching and handoff, where measured.
    st = [(n, s["stitch"]) for n, s in scenarios.items()
          if s.get("stitch", {}).get("raw_ids")]
    if st:
        a("<h2>Fragment stitching</h2>")
        a("<p>ByteTrack produces fragments, not people. This is how many "
          "fragments were correctly rejoined — and, more importantly, how "
          "many were joined <em>wrongly</em>, which welds two people's days "
          "together.</p>")
        a("<table><thead><tr><th>Scenario</th><th class='num'>Raw ids</th>"
          "<th class='num'>Identities</th><th class='num'>People</th>"
          "<th class='num'>Stitch rate</th><th class='num'>Wrong joins</th>"
          "</tr></thead><tbody>")
        for n, s in st:
            a(f"<tr><td>{n.replace('_', ' ')}</td>"
              f"<td class='num'>{s['raw_ids']}</td>"
              f"<td class='num'>{s['identities']}</td>"
              f"<td class='num'>{s['people']}</td>"
              f"<td class='num'>{s['rate'] * 100:.0f}%</td>"
              f"<td class='num'>{s['wrong_joins']}</td></tr>")
        a("</tbody></table>")

    ho = [(n, s["handoff"]) for n, s in scenarios.items()
          if s.get("handoff", {}).get("attempts")]
    if ho:
        a("<h2>Cross-camera handoff</h2>")
        a("<p>The plan expects 6&ndash;8 out of 10 and calls that a normal "
          "result.</p>")
        a("<table><thead><tr><th>Scenario</th><th class='num'>Crossings</th>"
          "<th class='num'>Correct</th><th class='num'>Wrong</th>"
          "<th class='num'>Unknown</th></tr></thead><tbody>")
        for n, h in ho:
            a(f"<tr><td>{n.replace('_', ' ')}</td>"
              f"<td class='num'>{h['attempts']}</td>"
              f"<td class='num'>{h['correct']}</td>"
              f"<td class='num'>{h['wrong']}</td>"
              f"<td class='num'>{h['unknown']}</td></tr>")
        a("</tbody></table>")

    ce = [(n, s["chair_exits"]) for n, s in scenarios.items()
          if s.get("chair_exits", {}).get("tp") or
          s.get("chair_exits", {}).get("fp") or
          s.get("chair_exits", {}).get("fn")]
    if ce:
        a("<h2>Chair exits</h2>")
        a("<p>The plan calls this &ldquo;the single most important "
          "verification in the whole plan&rdquo;.</p>")
        a("<table><thead><tr><th>Scenario</th><th class='num'>Detected</th>"
          "<th class='num'>False alarms</th><th class='num'>Missed</th>"
          "<th class='num'>Precision</th><th class='num'>Recall</th>"
          "</tr></thead><tbody>")
        for n, c in ce:
            a(f"<tr><td>{n.replace('_', ' ')}</td>"
              f"<td class='num'>{c['tp']}</td><td class='num'>{c['fp']}</td>"
              f"<td class='num'>{c['fn']}</td>"
              f"<td class='num'>{c['precision'] * 100:.0f}%</td>"
              f"<td class='num'>{c['recall'] * 100:.0f}%</td></tr>")
        a("</tbody></table>")

    a("<h2>Confusion matrix</h2>")
    a("<p>Rows are the truth; columns are what the system said. The diagonal "
      "is correct; the <code>UNKNOWN</code> column is where it declined to "
      "guess.</p>")
    a(confusion_table(merged))

    fails = data.get("failures", [])
    serious = [f for f in fails if f.get("kind") in
               ("misidentification", "false_name", "hallucination")]
    a("<h2>Failures</h2>")
    a(f"<p>{len(fails)} recorded, of which <strong>{len(serious)}</strong> are "
      f"serious (a wrong name rather than an abstention). The full catalogue, "
      f"with the reasoning for each, is in <code>FAILURE_CASES.md</code>.</p>")
    if serious:
        from collections import Counter
        per = Counter(f.get("scenario", "?") for f in serious)
        a("<table><thead><tr><th>Scenario</th><th class='num'>Serious failures</th>"
          "</tr></thead><tbody>")
        for scen, n in per.most_common():
            a(f"<tr><td>{scen.replace('_', ' ')}</td><td class='num'>{n}</td></tr>")
        a("</tbody></table>")

    a("<footer>Generated by <code>python -m backend.eval.report</code> from "
      "<code>backend/eval/results/evaluation.json</code>. "
      "Every figure is reproducible: the simulator is seeded, so the same "
      "command produces the same numbers.</footer>")
    a("</div></body></html>")
    return "\n".join(H)


def main() -> int:
    src = os.path.join(RESULTS, "evaluation.json")
    if not os.path.exists(src):
        print("No results. Run:  python -m backend.eval.evaluate")
        return 1
    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    html_path = os.path.join(RESULTS, "report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_html(data))
    print(f"  report -> {html_path}")

    # The dashboard reads a trimmed copy: the individual failure list can run
    # to hundreds of entries and the page only needs the counts.
    os.makedirs(PUBLIC, exist_ok=True)
    slim = {k: v for k, v in data.items() if k != "failures"}
    slim["failure_summary"] = {}
    for f in data.get("failures", []):
        k = f.get("kind", "unknown")
        slim["failure_summary"][k] = slim["failure_summary"].get(k, 0) + 1
    slim["generated_at"] = datetime.now(timezone.utc).isoformat()
    pub = os.path.join(PUBLIC, "evaluation.json")
    with open(pub, "w", encoding="utf-8") as f:
        json.dump(slim, f, indent=1)
    print(f"  dashboard data -> {pub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
