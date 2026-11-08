# Fine-tune comparison

Current model = `ppe_yolov8m_v1`; fine-tuned = `ppe_yolov8m_ft1`. valB = 100 Construction-PPE images held out from training AND from picking the best epoch. ppe6 test = the original 213-image test split.


## mAP50 / precision / recall (Ultralytics, helmet and safety_vest)

| set | class | current P / R / mAP50 | fine-tuned P / R / mAP50 |
|---|---|---|---|
| valB (construction) | helmet | 0.881 / 0.698 / 0.729 | 0.939 / 0.808 / 0.868 |
| valB (construction) | safety_vest | 0.941 / 0.706 / 0.763 | 0.967 / 0.866 / 0.931 |
| ppe6 test (original) | helmet | 0.842 / 0.909 / 0.879 | 0.855 / 0.875 / 0.894 |
| ppe6 test (original) | safety_vest | 0.921 / 0.841 / 0.913 | 0.908 / 0.859 / 0.929 |

## One-frame reading against the explicit labels (valB)

| item | measure | current | fine-tuned |
|---|---|---:|---:|
| helmet | worn but read absent, clear frames | 13.8% (18/130) | 2.3% (3/130) |
| helmet | worn but read absent, all people | 24.2% (40/165) | 9.1% (15/165) |
| helmet | missing and read absent, clear frames | 97.4% (37/38) | 84.2% (32/38) |
| vest | worn but read absent, clear frames | 16.2% (19/117) | 2.6% (3/117) |
| vest | worn but read absent, all people | 30.1% (47/156) | 5.1% (8/156) |
| vest | missing and read absent, clear frames | 98.1% (52/53) | 98.1% (52/53) |
