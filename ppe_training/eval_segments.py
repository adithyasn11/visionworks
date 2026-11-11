"""
The core experiment (design §14.A and §14.G): does the temporal evidence layer accuse fewer people than the obvious
alternatives, and at what cost in abstentions?

A "segment" is one labelled person seen for up to 45 frames. Real own-footage segments (a worker filmed for ~10 s) have
not been recorded yet, so the frames here are made from the held-out labelled images of Construction-PPE: frame 0 is the
image as it is, and every later frame is the same scene through a slightly different camera moment (a small shift and
zoom, a change of light and contrast, sometimes a little blur, a JPEG compression level). The people do not move. This
is honest about one thing and not the other: it measures how the system copes with the frame-to-frame jitter of a real
detector on a real camera, NOT with people turning, walking or being hidden. Own footage is still needed for that.

Every frame goes through the PRODUCTION code: the PPE detector (production weights), the person-to-PPE association and
the clear-frame gate (ppe_evidence.associate_frame), and for "ours" the evidence tracker (ppe_evidence.PpeEvidenceTracker).

THE THREE SYSTEMS (per person, per item; the rule needs that item)

  B0  per-frame       one frame (frame 0, the clean image): no box at or above the detection threshold -> violation
  B1  majority vote   over the window: no box in more than half the frames -> violation; no clear-frame gate, never abstains
  ours                clear-frame gate + presence 60 % / absence 90 % over the window + UNVERIFIABLE

  "A box" for the baselines is a detection at or above PRESENT_CONF (0.45), the threshold ours uses for "seen".

OUTCOMES against the label (helmet / no_helmet, vest / no_vest)

  caught            label ABSENT,  verdict violation          (correct)
  cleared           label PRESENT, verdict compliant          (correct)
  FALSE ACCUSATION  label PRESENT, verdict violation          (the error that matters most)
  FALSE COMPLIANCE  label ABSENT,  verdict compliant
  abstained         verdict UNVERIFIABLE                      (ours only)

A zero is reported with its 95 % upper bound by the rule of three (3 / N), never as a bare 0 %.

THE SWEEP: presence {0.4, 0.5, 0.6, 0.7} x absence {0.7, 0.8, 0.9, 0.95} x window {10, 20, 30, 45} frames, plotted as
error rate (wrong among committed verdicts) against coverage (share of segments with a verdict).

Data: Construction-PPE (Ultralytics, CC BY 4.0, partly generated imagery). Main result: valB, the 100 validation images
that were never used in training or in choosing the model (prepare_finetune_data.py). A larger, slightly less clean set
(all 200 validation images; valA was used to pick the best training epoch) is reported beside it.

Run:    venv/Scripts/python.exe ppe_training/eval_segments.py              (detects and caches the frame readings)
        venv/Scripts/python.exe ppe_training/eval_segments.py --cached     (re-scores from the cache)
Writes: ppe_training/reports/SEGMENT_EVAL.json, SEGMENT_EVAL.md, segment_sweep.svg / .png
"""

import argparse
import glob
import json
import math
import os
import random
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backend")))

from app.cv.ppe_evidence import (                                  # noqa: E402
    ABSENCE_FRACTION, COMPLIANT, MIN_CLEAR_FRAMES, NON_COMPLIANT, PRESENCE_FRACTION, PRESENT_CONF, WINDOW_FRAMES,
    PpeEvidenceTracker, associate_frame,
)
from eval_construction_ppe import CLS, MAX_W, gt_state, load_labels  # noqa: E402

ROOT = os.path.join(HERE, "datasets", "construction_ppe")
REPORTS = os.path.join(HERE, "reports")
CACHE = os.path.join(REPORTS, "segment_readings.json")
FRAMES = 45
ITEMS = (("helmet", "helmet"), ("vest", "safety_vest"))
PRESENCES = (0.4, 0.5, 0.6, 0.7)
ABSENCES = (0.7, 0.8, 0.9, 0.95)
WINDOWS = (10, 20, 30, 45)


# ── the frames ───────────────────────────────────────────────────────────────

def camera_moment(img, boxes, rng):
    """The same scene through a slightly different camera moment. Returns (frame, boxes moved the same way)."""
    h, w = img.shape[:2]
    s = rng.uniform(0.94, 1.06)
    tx, ty = rng.uniform(-0.03, 0.03) * w, rng.uniform(-0.03, 0.03) * h
    m = np.array([[s, 0, (1 - s) * w / 2 + tx], [0, s, (1 - s) * h / 2 + ty]], dtype=np.float32)
    out = cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REFLECT)
    alpha, beta = rng.uniform(0.85, 1.15), rng.uniform(-18, 18)
    out = cv2.convertScaleAbs(out, alpha=alpha, beta=beta)
    if rng.random() < 0.5:
        k = rng.choice([3, 5])
        out = cv2.GaussianBlur(out, (k, k), rng.uniform(0.3, 1.2))
    q = int(rng.uniform(55, 95))
    ok, enc = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), q])
    out = cv2.imdecode(enc, cv2.IMREAD_COLOR) if ok else out
    moved = []
    for x1, y1, x2, y2 in boxes:
        a = [s * x1 + m[0, 2], s * y1 + m[1, 2], s * x2 + m[0, 2], s * y2 + m[1, 2]]
        moved.append([max(0.0, min(w, a[0])), max(0.0, min(h, a[1])), max(0.0, min(w, a[2])), max(0.0, min(h, a[3]))])
    return out, moved


def collect(files, detector):
    """Per person and item: the label and FRAMES readings of (conf, clear)."""
    segments = []
    t0 = time.time()
    for n, path in enumerate(files, 1):
        img0 = cv2.imread(path)
        if img0 is None:
            continue
        h0, w0 = img0.shape[:2]
        scale = MAX_W / float(w0) if w0 > MAX_W else 1.0
        img = cv2.resize(img0, (int(w0 * scale), int(h0 * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else img0
        h, w = img.shape[:2]
        label_path = path.replace(os.sep + "images" + os.sep, os.sep + "labels" + os.sep)[:-4] + ".txt"
        if not os.path.isfile(label_path):
            continue
        boxes = load_labels(label_path, w0, h0, scale)
        person_boxes = boxes.get(CLS["person"], [])
        if not person_boxes:
            continue
        persons = [{"track_id": i, "bbox": b} for i, b in enumerate(person_boxes)]
        truth = {
            "helmet": gt_state(persons, boxes.get(CLS["helmet"], []), boxes.get(CLS["no_helmet"], []), "helmet"),
            "vest": gt_state(persons, boxes.get(CLS["vest"], []), boxes.get(CLS["no_vest"], []), "safety_vest"),
        }
        rng = random.Random(f"{os.path.basename(path)}")
        readings = {p["track_id"]: {"helmet": [], "vest": []} for p in persons}
        imgsz = int(math.ceil(max(h, w) / 32.0) * 32)
        for f in range(FRAMES):
            if f == 0:
                frame, moved = img, person_boxes
            else:
                frame, moved = camera_moment(img, person_boxes, rng)
            frame_persons = [{"track_id": i, "bbox": b} for i, b in enumerate(moved)]
            dets = detector.detect(frame, imgsz=imgsz)
            seen = associate_frame(frame_persons, dets, frame.shape)
            for p in frame_persons:
                o = seen[p["track_id"]]
                readings[p["track_id"]]["helmet"].append([round(float(o["helmet"]), 3), bool(o["clear"])])
                readings[p["track_id"]]["vest"].append([round(float(o["safety_vest"]), 3), bool(o["clear"])])
        for p in persons:
            height = p["bbox"][3] - p["bbox"][1]
            for item, _ in ITEMS:
                gt = truth[item][p["track_id"]]
                if gt is None:
                    continue
                segments.append({"image": os.path.basename(path), "person": p["track_id"], "item": item, "truth": gt,
                                 "height_px": round(height, 1), "frames": readings[p["track_id"]][item]})
        if n % 10 == 0:
            print(f"  {n}/{len(files)} images, {len(segments)} segments, {time.time() - t0:.0f} s")
    return segments


# ── the three systems ────────────────────────────────────────────────────────

def b0(frames):
    return "violation" if frames[0][0] < PRESENT_CONF else "compliant"


def b1(frames, window):
    w = frames[:window]
    missing = sum(1 for c, _ in w if c < PRESENT_CONF)
    return "violation" if missing > len(w) / 2 else "compliant"


def ours(frames, window, presence=PRESENCE_FRACTION, absence=ABSENCE_FRACTION):
    key = "helmet"                                     # the tracker's own item name; the reading is what matters
    t = PpeEvidenceTracker(required=(key,), window=window, min_clear=MIN_CLEAR_FRAMES, presence=presence, absence=absence, stale_seconds=1e9)
    for i, (c, clear) in enumerate(frames[:window]):
        t.update("p", {key: c, "safety_vest": 0.0, "clear": clear}, float(i))
    st = t.status("p", float(window))["status"]
    return {COMPLIANT: "compliant", NON_COMPLIANT: "violation"}.get(st, "abstain")


def outcome(truth, verdict):
    if verdict == "abstain":
        return "abstained"
    if truth == "PRESENT":
        return "cleared" if verdict == "compliant" else "false_accusation"
    return "caught" if verdict == "violation" else "false_compliance"


def score(segments, decide):
    counts = {"caught": 0, "cleared": 0, "false_accusation": 0, "false_compliance": 0, "abstained": 0}
    for s in segments:
        counts[outcome(s["truth"], decide(s["frames"]))] += 1
    n = len(segments)
    worn = sum(1 for s in segments if s["truth"] == "PRESENT")
    missing = n - worn
    committed = n - counts["abstained"]
    wrong = counts["false_accusation"] + counts["false_compliance"]
    return {
        "n": n, "worn": worn, "missing": missing, **counts,
        "committed": committed, "coverage": round(committed / n, 4) if n else None,
        "error_rate": round(wrong / committed, 4) if committed else None,
        "false_accusation_rate": round(counts["false_accusation"] / worn, 4) if worn else None,
        "false_accusation_upper95": round((3 / worn) if counts["false_accusation"] == 0 and worn else _wilson_upper(counts["false_accusation"], worn), 4) if worn else None,
        "false_compliance_rate": round(counts["false_compliance"] / missing, 4) if missing else None,
        "false_compliance_upper95": round((3 / missing) if counts["false_compliance"] == 0 and missing else _wilson_upper(counts["false_compliance"], missing), 4) if missing else None,
    }


def _wilson_upper(k, n, z=1.96):
    """Upper end of the 95 % Wilson interval (used when the count is not zero)."""
    p = k / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + margin) / (1 + z * z / n))


def evaluate(segments):
    out = {}
    for item in ("helmet", "vest", "both items"):
        segs = segments if item == "both items" else [s for s in segments if s["item"] == item]
        out[item] = {
            "B0 per-frame": score(segs, b0),
            "B1 majority vote": score(segs, lambda f: b1(f, WINDOW_FRAMES)),
            "ours": score(segs, lambda f: ours(f, WINDOW_FRAMES)),
        }
    sweep = []
    for p in PRESENCES:
        for a in ABSENCES:
            for w in WINDOWS:
                r = score(segments, lambda f, p=p, a=a, w=w: ours(f, w, p, a))
                sweep.append({"presence": p, "absence": a, "window": w, "coverage": r["coverage"], "error_rate": r["error_rate"],
                              "false_accusation": r["false_accusation"], "false_compliance": r["false_compliance"], "abstained": r["abstained"]})
    by_size = {}
    for label, lo, hi in (("under 90 px", 0, 90), ("90-200 px", 90, 200), ("200 px and over", 200, 1e9)):
        segs = [s for s in segments if lo <= s["height_px"] < hi]
        by_size[label] = {"B0 per-frame": score(segs, b0), "B1 majority vote": score(segs, lambda f: b1(f, WINDOW_FRAMES)),
                          "ours": score(segs, lambda f: ours(f, WINDOW_FRAMES))}
    return out, sweep, by_size


# ── the plot ─────────────────────────────────────────────────────────────────

def plot(sweep, baselines, path_svg, path_png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=130)
    for w, marker in zip(WINDOWS, ("o", "s", "^", "D")):
        pts = [s for s in sweep if s["window"] == w and s["error_rate"] is not None]
        ax.scatter([s["coverage"] * 100 for s in pts], [s["error_rate"] * 100 for s in pts], marker=marker, s=26,
                   facecolors="none", edgecolors="#555555", linewidths=1.0, label=f"ours, window {w}")
    chosen = next(s for s in sweep if s["presence"] == PRESENCE_FRACTION and s["absence"] == ABSENCE_FRACTION and s["window"] == WINDOW_FRAMES)
    ax.scatter([chosen["coverage"] * 100], [chosen["error_rate"] * 100], s=120, color="#DC2626", zorder=5,
               label=f"ours as shipped (0.6 / 0.9 / {WINDOW_FRAMES})")
    for name, r, marker in (("B0 per-frame", baselines["B0 per-frame"], "x"), ("B1 majority vote", baselines["B1 majority vote"], "+")):
        ax.scatter([r["coverage"] * 100], [r["error_rate"] * 100], marker=marker, s=110, color="#111111", linewidths=2, zorder=5, label=name)
    ax.set_xlabel("Coverage: segments with a verdict (%)")
    ax.set_ylabel("Error rate among verdicts (%)")
    ax.set_title("Error against coverage (helmet and vest segments)")
    ax.grid(True, color="#DDDDDD", linewidth=0.6)
    ax.legend(fontsize=7.5, loc="upper left", frameon=False)
    fig.tight_layout()
    fig.savefig(path_svg)
    fig.savefig(path_png)
    plt.close(fig)


def pc(v):
    return "n/a" if v is None else f"{v * 100:.1f}%"


def fmt(r, key):
    v = r.get(key)
    return "n/a" if v is None else f"{v * 100:.1f}%"


def table_md(results):
    lines = ["| system | N | caught | cleared | **false accusations** | false compliance | abstained | coverage | error rate |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, r in results.items():
        fa = f"{r['false_accusation']} of {r['worn']} worn ({fmt(r, 'false_accusation_rate')}; 95% upper {fmt(r, 'false_accusation_upper95')})"
        fc = f"{r['false_compliance']} of {r['missing']} missing ({fmt(r, 'false_compliance_rate')}; 95% upper {fmt(r, 'false_compliance_upper95')})"
        lines.append(f"| {name} | {r['n']} | {r['caught']} | {r['cleared']} | {fa} | {fc} | {r['abstained']} | {fmt(r, 'coverage')} | {fmt(r, 'error_rate')} |")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cached", action="store_true", help="re-score from the cached frame readings")
    ap.add_argument("--set", default="valB", choices=["valB", "val"], help="valB: never seen in training or selection (main); val: all 200")
    args = ap.parse_args()
    os.makedirs(REPORTS, exist_ok=True)
    val = sorted(glob.glob(os.path.join(ROOT, "images", "val", "*.jpg")))
    sets = {"valB": val[1::2], "val": val}
    if args.cached and os.path.isfile(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
    else:
        from app.cv.ppe_detector import PpeDetector
        detector = PpeDetector()
        print(f"detector: {detector.weights} on {detector.device}")
        segs = collect(val, detector)                  # read all 200 once; valB is a subset of it
        cache = {"frames_per_segment": FRAMES, "weights": os.path.relpath(detector.weights, HERE), "segments": segs}
        json.dump(cache, open(CACHE, "w", encoding="utf-8"))
    report = {"data": "Construction-PPE validation images (held out); people still, frames vary by camera moment",
              "frames_per_segment": cache["frames_per_segment"], "weights": cache.get("weights"),
              "defaults": {"presence": PRESENCE_FRACTION, "absence": ABSENCE_FRACTION, "window": WINDOW_FRAMES, "min_clear_frames": MIN_CLEAR_FRAMES,
                           "seen_threshold": PRESENT_CONF},
              "sets": {}}
    md = ["# PPE segment evaluation: our evidence layer against two baselines", "",
          "Generated by `ppe_training/eval_segments.py`. Each person and item is one segment of up to 45 frames: the labelled, held-out "
          "image as it is (frame 0) and then the same scene through slightly different camera moments (shift, zoom, light, blur, JPEG). "
          "People do not move or turn here, so this measures the system against detector jitter on a camera, not against motion or occlusion. "
          "Every frame goes through the production detector, association and clear-frame gate.", "",
          f"Defaults: presence {PRESENCE_FRACTION}, absence {ABSENCE_FRACTION}, window {WINDOW_FRAMES} frames, at least {MIN_CLEAR_FRAMES} clear frames; "
          f"the baselines count an item as seen at a detection of {PRESENT_CONF} or more.", ""]
    names = set(os.path.basename(p) for p in sets[args.set])
    for set_name in ("valB", "val"):
        wanted = set(os.path.basename(p) for p in sets[set_name])
        segs = [s for s in cache["segments"] if s["image"] in wanted]
        results, sweep, by_size = evaluate(segs)
        report["sets"][set_name] = {"images": len(wanted), "segments": len(segs), "results": results, "sweep": sweep, "by_size": by_size}
        title = ("valB: never used in training or in choosing the model (main result)" if set_name == "valB"
                 else "All 200 validation images (valA was used to choose the training epoch, so this is slightly less clean)")
        md += [f"## {title}", "", f"{len(wanted)} images, {len(segs)} segments.", ""]
        for item in ("both items", "helmet", "vest"):
            md += [f"### {item.capitalize()}", ""] + table_md(results[item]) + [""]
        md += ["### By person height (both items)", ""]
        for label, res in by_size.items():
            md += [f"**{label}**", ""] + table_md(res) + [""]
        if set_name == "valB":
            plot(sweep, results["both items"], os.path.join(REPORTS, "segment_sweep.svg"), os.path.join(REPORTS, "segment_sweep.png"))
            md += ["### Threshold sweep (both items)", "", "![error against coverage](segment_sweep.png)", "",
                   "| presence | absence | window | coverage | error rate | false accusations | false compliance | abstained |",
                   "|---:|---:|---:|---:|---:|---:|---:|---:|"]
            for s in sweep:
                md.append(f"| {s['presence']} | {s['absence']} | {s['window']} | {pc(s['coverage'])} | {pc(s['error_rate'])} | "
                          f"{s['false_accusation']} | {s['false_compliance']} | {s['abstained']} |")
            md.append("")
    json.dump(report, open(os.path.join(REPORTS, "SEGMENT_EVAL.json"), "w", encoding="utf-8"), indent=1)
    open(os.path.join(REPORTS, "SEGMENT_EVAL.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    main_r = report["sets"]["valB"]["results"]["both items"]
    for k, r in main_r.items():
        print(f"{k:18s} N={r['n']} FA={r['false_accusation']} FC={r['false_compliance']} abst={r['abstained']} cov={r['coverage']} err={r['error_rate']}")
    _ = names


if __name__ == "__main__":
    main()
