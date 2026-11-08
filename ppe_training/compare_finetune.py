"""
Is the fine-tuned model better than the one in use? Same images, same code.

Compares runs/ppe_yolov8m_v1 (in use) with runs/ppe_yolov8m_ft1 (fine-tuned) on
  * valB: 100 Construction-PPE images neither the training nor the choice of best
    epoch ever saw (mAP50 via Ultralytics, plus the per-frame helmet/vest reading
    against the explicit no_helmet / no_vest labels);
  * the original ppe6 TEST split (213 images), to check it has not forgotten what
    it knew.

Writes ppe_training/reports/FINETUNE_COMPARISON.md.

Run:  venv/Scripts/python.exe ppe_training/compare_finetune.py
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OLD = os.path.join(HERE, "runs", "ppe_yolov8m_v1", "weights", "best.pt")
NEW = os.path.join(HERE, "runs", "ppe_yolov8m_ft1", "weights", "best.pt")
DATA_B = os.path.join(HERE, "datasets", "ppe_finetune", "data_valB.yaml")
VALB = os.path.join(HERE, "datasets", "ppe_finetune", "valB.txt")
PPE6 = os.path.join(HERE, "datasets", "ppe6_roboflow", "data.yaml")
REPORTS = os.path.join(HERE, "reports")


def map50(weights, data, split, imgsz):
    from ultralytics import YOLO
    m = YOLO(weights)
    r = m.val(data=data, split=split, imgsz=imgsz, batch=8, device=0, verbose=False, plots=False)
    names = m.names
    per = {names[int(c)]: (float(p), float(rc), float(a))
           for c, p, rc, a in zip(r.box.ap_class_index, r.box.p, r.box.r, r.box.ap50)}
    return per


def item_numbers(weights, tag):
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "eval_construction_ppe.py"), "--only", VALB,
         "--weights", weights, "--tag", tag], capture_output=True, text=True, cwd=ROOT)
    if out.returncode:
        sys.exit(out.stderr[-2000:])
    return json.load(open(os.path.join(REPORTS, f"CONSTRUCTION_PPE_EVAL{tag}.json")))


def main():
    for p in (OLD, NEW):
        if not os.path.isfile(p):
            sys.exit(f"missing weights: {p}")
    lines = ["# Fine-tune comparison\n",
             "Current model = `ppe_yolov8m_v1`; fine-tuned = `ppe_yolov8m_ft1`. "
             "valB = 100 Construction-PPE images held out from training AND from picking the best epoch. "
             "ppe6 test = the original 213-image test split.\n"]
    results = {}
    for label, w in (("current", OLD), ("fine-tuned", NEW)):
        results[label] = {
            "valB": map50(w, DATA_B, "val", 960),
            "ppe6_test": map50(w, PPE6, "test", 640),
            "items": item_numbers(w, "_CMP_" + label.replace("-", "")),
        }
    lines.append("\n## mAP50 / precision / recall (Ultralytics, helmet and safety_vest)\n")
    lines.append("| set | class | current P / R / mAP50 | fine-tuned P / R / mAP50 |")
    lines.append("|---|---|---|---|")
    for st, key in (("valB (construction)", "valB"), ("ppe6 test (original)", "ppe6_test")):
        for cls in ("helmet", "safety_vest"):
            a = results["current"][key].get(cls)
            b = results["fine-tuned"][key].get(cls)
            f = lambda t: "n/a" if t is None else f"{t[0]:.3f} / {t[1]:.3f} / {t[2]:.3f}"
            lines.append(f"| {st} | {cls} | {f(a)} | {f(b)} |")
    lines.append("\n## One-frame reading against the explicit labels (valB)\n")
    lines.append("| item | measure | current | fine-tuned |")
    lines.append("|---|---|---:|---:|")
    for item in ("helmet", "vest"):
        a, b = results["current"]["items"]["items"][item], results["fine-tuned"]["items"]["items"][item]

        def rate(n, d):
            return f"{100.0 * n / d:.1f}% ({n}/{d})" if d else "n/a"
        lines.append(f"| {item} | worn but read absent, clear frames | {rate(a['clear_wrongly_read_absent'], a['clear_present'])} | {rate(b['clear_wrongly_read_absent'], b['clear_present'])} |")
        lines.append(f"| {item} | worn but read absent, all people | {rate(a['wrongly_read_absent_of_present'], a['labelled_present'])} | {rate(b['wrongly_read_absent_of_present'], b['labelled_present'])} |")
        lines.append(f"| {item} | missing and read absent, clear frames | {rate(a['clear_absent_caught'], a['clear_absent'])} | {rate(b['clear_absent_caught'], b['clear_absent'])} |")
    text = "\n".join(lines) + "\n"
    os.makedirs(REPORTS, exist_ok=True)
    open(os.path.join(REPORTS, "FINETUNE_COMPARISON.md"), "w", encoding="utf-8").write(text)
    print(text)


if __name__ == "__main__":
    main()
