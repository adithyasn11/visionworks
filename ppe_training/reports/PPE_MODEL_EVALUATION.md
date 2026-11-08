# PPE model evaluation — `ppe_training/runs/ppe_yolov8m_v1/weights/best.pt`

Reproduce: `venv\Scripts\python.exe ppe_training\eval_ppe_model.py --split test`
(raw numbers: `test_split.json`, `valid_split.json`). Matching: class-aware, IoU >= 0.5.

## Per-class, test split (213 images), conf >= 0.35

| class | GT | precision | recall | F1 |
|---|---|---|---|---|
| helmet | 88 | 0.832 | 0.955 | 0.889 |
| safety_vest | 138 | 0.864 | 0.877 | 0.871 |
| person | 157 | 0.873 | 0.924 | 0.898 |

Validation split (425 images), conf >= 0.35: helmet P 0.919 / R 0.940, vest P 0.896 / R 0.932.
Direct confusion-matrix recomputation (`recompute_confusion.py`): true helmets predicted as
helmet 94%, true vests as vest 92.4%, **no helmet->person or helmet->vest confusion**.
Every missed box is "nothing detected", never a wrong class.

A first reading of `ppe_training/runs/ppe_yolov8m_v1/confusion_matrix_normalized.png` suggested only
60% helmet recall with 19%/13% leaking into person/vest. The direct measurements above
do not reproduce that, so that image should not be quoted.

## What this does NOT show

* All numbers are on Roboflow-style photos, resized to 640x640 by *stretching*. Not your
  cameras, not your room. Use `eval_ppe_footage.py` on your own footage.
* Precision 0.83-0.86 means roughly one in six helmet/vest detections is spurious. That is
  a false-COMPLIANCE risk, which is why `ppe_evidence.py` needs a worker to be seen with the
  item in >= 60% of clear frames before calling it present.
* Recall of 0.88 for vests means ~12% of real vests are missed in any single frame, so a
  single frame must never trigger a violation; a violation needs >= 90% of clear frames
  empty.

## Simulated verdict quality (`backend/tests/test_ppe_evidence.py`)

Uses the measured recall above and a 6% per-frame false-detection rate, 30-frame window,
15% of frames unclear. Frames are independent, which is optimistic.

| truth | COMPLIANT | NON_COMPLIANT | UNVERIFIABLE |
|---|---|---|---|
| fully equipped | 100% | 0% | 0% |
| no helmet | 0% | 81.5% | 18.5% |
| no vest | 0% | 81.1% | 18.9% |
| neither | 0% | 96.5% | 3.5% |

Nobody missing PPE is ever called compliant, and nobody equipped is accused.
