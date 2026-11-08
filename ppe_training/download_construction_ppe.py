"""
Download the Ultralytics Construction-PPE images listed in an NDJSON export.

The export (`construction-ppe.ndjson`, from platform.ultralytics.com/ultralytics/
datasets/construction-ppe) holds the labels and a time-limited signed link per
image. This fetches each image into
    ppe_training/datasets/construction_ppe/images/<split>/<file>
and writes the labels beside them in YOLO format
    ppe_training/datasets/construction_ppe/labels/<split>/<file>.txt
plus data.yaml. Licence: CC BY 4.0, Ultralytics (credit when used).

Safe to re-run: files already downloaded (and non-empty) are skipped. The signed
links expire (the date is in each URL), so run it soon after exporting.

Run:  venv/Scripts/python.exe ppe_training/download_construction_ppe.py [path/to/construction-ppe.ndjson]
"""

import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INDEX = os.path.abspath(os.path.join(HERE, "..", "construction-ppe.ndjson"))
OUT = os.path.join(HERE, "datasets", "construction_ppe")
WORKERS = 8
RETRIES = 3


def read_index(path):
    meta, images = None, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("type") == "dataset":
                meta = d
            elif d.get("type") == "image":
                images.append(d)
    if meta is None:
        sys.exit("no dataset header line in the file")
    return meta, images


def safe_name(name):
    base = os.path.basename(str(name).replace("\\", "/"))
    if not base or base.startswith("."):
        raise ValueError(f"unsafe file name {name!r}")
    return base


def fetch(entry):
    split = entry["split"] if entry.get("split") in ("train", "val", "test") else "train"
    name = safe_name(entry["file"])
    dest = os.path.join(OUT, "images", split, name)
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return name, "skipped", 0
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(entry["url"], headers={"User-Agent": "visionworks-dataset-fetch"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".part", "wb") as out:
                data = r.read()
                out.write(data)
            if not data:
                raise IOError("empty response")
            os.replace(dest + ".part", dest)
            return name, "ok", len(data)
        except Exception as e:                                # noqa: BLE001
            last = e
            time.sleep(1 + attempt)
    if os.path.exists(dest + ".part"):
        os.remove(dest + ".part")
    return name, f"FAILED {type(last).__name__}: {last}", 0


def write_labels(meta, images):
    names = {int(k): v for k, v in meta["class_names"].items()}
    for entry in images:
        split = entry["split"] if entry.get("split") in ("train", "val", "test") else "train"
        name = os.path.splitext(safe_name(entry["file"]))[0]
        os.makedirs(os.path.join(OUT, "labels", split), exist_ok=True)
        rows = entry.get("annotations", {}).get("boxes", [])
        with open(os.path.join(OUT, "labels", split, name + ".txt"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(f"{int(r[0])} {r[1]:.6f} {r[2]:.6f} {r[3]:.6f} {r[4]:.6f}\n")
    with open(os.path.join(OUT, "data.yaml"), "w", encoding="utf-8") as f:
        f.write("# Ultralytics Construction-PPE, CC BY 4.0. Made by download_construction_ppe.py\n")
        f.write("train: images/train\nval: images/val\n")
        f.write(f"nc: {len(names)}\nnames: {[names[i] for i in sorted(names)]}\n")


def main():
    index = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INDEX
    meta, images = read_index(index)
    print(f"{len(images)} images listed; saving under {OUT}")
    write_labels(meta, images)
    done = failed = skipped = total = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch, e) for e in images]
        for i, fut in enumerate(as_completed(futures), 1):
            name, status, size = fut.result()
            total += size
            if status == "ok":
                done += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
                print(f"  {name}: {status}")
            if i % 100 == 0:
                print(f"  {i}/{len(images)}  {total / 1e6:.0f} MB  {time.time() - started:.0f} s")
    print(f"downloaded {done}, already there {skipped}, failed {failed}; {total / 1e6:.0f} MB in {time.time() - started:.0f} s")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
