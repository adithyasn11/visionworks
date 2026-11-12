"""
Near-duplicate check between the images the PPE model learned from and the images it was judged on (§14.A of the
master doc: "Check the public test split for leakage").

Roboflow and Ultralytics exports can put the same scene (or a flipped / re-lit copy of it) in both a training and a
test split. If they do, the test numbers are optimistic. This compares every evaluation image against every training
image with a 64-bit perceptual hash (DCT of the 32x32 grey picture, the usual pHash), the picture itself and its mirror
image (Roboflow's augmentation flips half the training copies).

Hamming distance between two hashes: 0 = the same picture after resizing / re-encoding; up to about 6 = the same
scene with light, crop or compression changes; above about 10 = different pictures. The closest pairs are written to a
contact sheet so the borderline ones can be judged by eye, which this script does not pretend to do.

    venv/Scripts/python.exe ppe_training/check_leakage.py

Writes ppe_training/reports/LEAKAGE_CHECK.json, LEAKAGE_CHECK.md and leakage_pairs.jpg. Changes no dataset.
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(HERE, "datasets")
OUT = os.path.join(HERE, "reports")
NEAR = 6             # at or below: treated as the same scene
CLOSE = 10           # at or below: listed for a look
SHEET_PAIRS = 24

IMG = (".jpg", ".jpeg", ".png", ".bmp")


def images(folder):
    return sorted(p for p in glob.glob(os.path.join(folder, "*")) if p.lower().endswith(IMG))


def phash(grey):
    small = cv2.resize(grey, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].flatten()
    bits = low > np.median(low[1:])                  # skip the DC term when choosing the cut
    return np.packbits(bits).view(">u8")[0]


def hashes(paths):
    plain, mirror, keep = [], [], []
    for p in paths:
        g = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        if g is None:
            continue
        plain.append(phash(g))
        mirror.append(phash(cv2.flip(g, 1)))
        keep.append(p)
    return np.array(plain, dtype=np.uint64), np.array(mirror, dtype=np.uint64), keep


POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def distances(q, t):
    """Hamming distance matrix between two uint64 hash arrays."""
    x = (q[:, None] ^ t[None, :]).view(np.uint8).reshape(len(q), len(t), 8)
    return POP[x].sum(axis=2)


def compare(name, query_paths, train_sets):
    qp, qm, qkeep = hashes(query_paths)
    best_d = np.full(len(qkeep), 99, dtype=np.int32)
    best_p = [None] * len(qkeep)
    best_flip = [False] * len(qkeep)
    for tname, tpaths in train_sets:
        tp, _, tkeep = hashes(tpaths)
        if not len(tkeep):
            continue
        for flip, qh in ((False, qp), (True, qm)):
            d = distances(qh, tp)
            j = d.argmin(axis=1)
            dj = d[np.arange(len(qkeep)), j]
            for i in np.where(dj < best_d)[0]:
                best_d[i] = dj[i]
                best_p[i] = (tname, tkeep[j[i]])
                best_flip[i] = flip
    rows = [{"image": os.path.relpath(qkeep[i], HERE), "closest": os.path.relpath(best_p[i][1], HERE) if best_p[i] else None,
             "closest_set": best_p[i][0] if best_p[i] else None, "distance": int(best_d[i]), "mirrored": bool(best_flip[i])}
            for i in range(len(qkeep))]
    rows.sort(key=lambda r: r["distance"])
    n = len(rows)
    summary = {
        "name": name, "images": n, "compared_against": {t: len(p) for t, p in train_sets},
        "identical (0)": sum(r["distance"] == 0 for r in rows),
        f"near-duplicate (<= {NEAR})": sum(r["distance"] <= NEAR for r in rows),
        f"close, worth a look (<= {CLOSE})": sum(r["distance"] <= CLOSE for r in rows),
        "median distance to the closest training image": float(np.median([r["distance"] for r in rows])) if rows else None,
    }
    return summary, rows


def sheet(pairs, path):
    tiles = []
    for r in pairs[:SHEET_PAIRS]:
        a = cv2.imread(os.path.join(HERE, r["image"]))
        b = cv2.imread(os.path.join(HERE, r["closest"]))
        if a is None or b is None:
            continue
        a, b = cv2.resize(a, (200, 200)), cv2.resize(b, (200, 200))
        if r["mirrored"]:
            b = cv2.flip(b, 1)
        tile = np.hstack([a, np.full((200, 4, 3), 255, np.uint8), b])
        bar = np.zeros((22, tile.shape[1], 3), np.uint8)
        cv2.putText(bar, f"{r['set']}  d={r['distance']}{' (mirror)' if r['mirrored'] else ''}", (4, 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        tiles.append(np.vstack([bar, tile]))
    if not tiles:
        return False
    cols = 4
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    cv2.imwrite(path, grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return True


def remeasured():
    """The public test numbers again without the leaked images, if eval_ppe_model.py has been run with --only."""
    sets = (("all", "all 213"), ("dedup", f"without near-duplicates (≤ {NEAR})"), ("strict", f"without close pairs (≤ {CLOSE})"))
    models = (("v1", "base model `ppe_yolov8m_v1` (the §8.3 numbers)"), ("ft1", "production model `ppe_yolov8m_ft1`"))
    rows = []
    for m, mname in models:
        for s, sname in sets:
            p = os.path.join(OUT, f"ppe6_test_{m}_{s}.json")
            if not os.path.exists(p):
                return []
            d = json.load(open(p, encoding="utf-8"))
            t = d["thresholds"]["0.35"]
            cells = [f"{t[c]['precision']:.2f} / {t[c]['recall']:.2f} ({t[c]['tp'] + t[c]['fn']})" for c in ("helmet", "safety_vest", "person")]
            rows.append(f"| {mname} | {sname} | {d['images']} | " + " | ".join(cells) + " |")
    return ["## PPE-6 public test split, measured again", "",
            "Per-class precision / recall at confidence 0.35 (`eval_ppe_model.py --only reports/ppe6_test_deduplicated*.txt`); "
            "the number in brackets is the labelled boxes of that class.", "",
            "| Model | Test set | Images | Helmet P / R | Vest P / R | Person P / R |", "|---|---|---:|---|---|---|", *rows, "",
            "**Reading it.** About two in five public test images are frames from the same videos as training images, so the "
            "published per-class numbers are optimistic, helmet precision most of all. The de-duplicated sets are small "
            "(25 to 34 helmets), so these numbers are rough. The held-out Construction-PPE set (valB) behind the main result "
            "has no copied pictures: its one close pair is the same gate camera with a different person.", ""]


def main():
    ft = os.path.join(DS, "ppe_finetune", "images")
    ppe6 = os.path.join(DS, "ppe6_roboflow")
    trained_on = [
        ("fine-tune train (Construction-PPE)", images(os.path.join(ft, "train_construction"))),
        ("fine-tune train (PPE-6)", images(os.path.join(ft, "train_ppe6"))),
        ("base model train (PPE-6 train)", images(os.path.join(ppe6, "train", "images"))),
    ]
    missing = [t for t, p in trained_on if not p]
    if missing:
        print("missing training folders:", missing)
        sys.exit(1)

    checks = [
        ("valB (held-out, the main result)", images(os.path.join(ft, "valB")), trained_on),
        ("valA (used to choose the fine-tuned model)", images(os.path.join(ft, "valA")), trained_on),
        ("Construction-PPE val, all 200", images(os.path.join(DS, "construction_ppe", "images", "val")), trained_on),
        ("PPE-6 public test split (per-class numbers, §8.3)", images(os.path.join(ppe6, "test", "images")), trained_on),
    ]
    results, closest = [], []
    for name, q, t in checks:
        if not q:
            print(f"skipped {name}: no images")
            continue
        s, rows = compare(name, q, t)
        results.append({"summary": s, "closest_20": rows[:20]})
        closest += [dict(r, set=name.split(" (")[0]) for r in rows if r["distance"] <= CLOSE]
        print(json.dumps(s, indent=2))
        if name.startswith("PPE-6"):
            # the de-duplicated test list, for eval_ppe_model.py --only
            os.makedirs(OUT, exist_ok=True)
            with open(os.path.join(OUT, "ppe6_test_deduplicated.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(os.path.basename(r["image"]) for r in rows if r["distance"] > NEAR) + "\n")
            # pairs at 7-10 were, by eye, mostly other frames of the same videos: a stricter list drops them too
            with open(os.path.join(OUT, "ppe6_test_deduplicated_strict.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(os.path.basename(r["image"]) for r in rows if r["distance"] > CLOSE) + "\n")
        if name.startswith("valB"):
            sheet([dict(r, set="valB") for r in rows[:4]], os.path.join(OUT, "leakage_valB_closest.jpg"))

    os.makedirs(OUT, exist_ok=True)
    closest.sort(key=lambda r: r["distance"])
    has_sheet = sheet(closest, os.path.join(OUT, "leakage_pairs.jpg"))
    sheet([r for r in closest if r["distance"] >= 4][::-1], os.path.join(OUT, "leakage_borderline.jpg"))
    with open(os.path.join(OUT, "LEAKAGE_CHECK.json"), "w", encoding="utf-8") as f:
        json.dump({"method": "64-bit DCT perceptual hash, image and mirror image, Hamming distance",
                   "near_threshold": NEAR, "close_threshold": CLOSE, "results": results}, f, indent=2)

    md = ["# Train / test near-duplicate check", "",
          "Generated by `ppe_training/check_leakage.py`. Every evaluation image is compared with every image the PPE model "
          "was trained on (the fine-tune set and the base model's training set), as it is and mirrored, by a 64-bit "
          f"perceptual hash. Distance 0 is the same picture; up to {NEAR} is treated as the same scene; up to {CLOSE} is "
          "listed for a look.", "",
          f"| Evaluation set | Images | Identical | Near-duplicate (≤ {NEAR}) | Close (≤ {CLOSE}) | Median distance |",
          "|---|---:|---:|---:|---:|---:|"]
    for r in results:
        s = r["summary"]
        md.append(f"| {s['name']} | {s['images']} | {s['identical (0)']} | {s[f'near-duplicate (<= {NEAR})']} | "
                  f"{s[f'close, worth a look (<= {CLOSE})']} | {s['median distance to the closest training image']:.0f} |")
    md += ["", "Training images compared against: " + ", ".join(f"{t} ({len(p)})" for t, p in trained_on) + ".", ""]
    if has_sheet:
        md += ["The closest pairs (evaluation image left, training image right) are in `leakage_pairs.jpg`; the pairs at "
               "distance 4 to 10 in `leakage_borderline.jpg`; valB's four closest in `leakage_valB_closest.jpg`.", ""]
    md += remeasured()
    with open(os.path.join(OUT, "LEAKAGE_CHECK.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("written", os.path.join(OUT, "LEAKAGE_CHECK.md"))


if __name__ == "__main__":
    main()
