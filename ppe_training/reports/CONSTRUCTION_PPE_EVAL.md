# Construction-PPE: per-frame reading against explicit labels

Images: 1000 (all); with people: 991; people: 2443 (of which clear by the live loop's gate: 1743).

Dataset: Ultralytics Construction-PPE, CC BY 4.0. Part of it is generated imagery. Our PPE model was not trained on this export, but its source images may overlap with public sets we used.

Reading: PRESENT = best matching detection >= 0.45; ABSENT = nothing >= 0.20 matches; AMBIGUOUS = between. This is ONE frame. The deployed system needs 8+ clear frames over a window before it says anything, so these are the numbers the temporal layer starts from, not the system's final accuracy.


## Helmet

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 1532 | PRESENT | 1038 (67.8%) | 118 (7.7%) | 376 (24.5%) |
| all people | 803 | ABSENT | 9 (1.1%) | 15 (1.9%) | 779 (97.0%) |
| clear frames only | 1236 | PRESENT | 991 (80.2%) | 98 (7.9%) | 147 (11.9%) |
| clear frames only | 494 | ABSENT | 7 (1.4%) | 14 (2.8%) | 473 (95.7%) |
| height 90-200 px | 319 | PRESENT | 192 (60.2%) | 42 (13.2%) | 85 (26.6%) |
| height 90-200 px | 152 | ABSENT | 4 (2.6%) | 8 (5.3%) | 140 (92.1%) |
| height <90 px | 237 | PRESENT | 19 (8.0%) | 15 (6.3%) | 203 (85.7%) |
| height <90 px | 285 | ABSENT | 0 (0.0%) | 1 (0.4%) | 284 (99.6%) |
| height >=200 px | 976 | PRESENT | 827 (84.7%) | 61 (6.2%) | 88 (9.0%) |
| height >=200 px | 366 | ABSENT | 5 (1.4%) | 6 (1.6%) | 355 (97.0%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 24.5%, clear frames only 11.9%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 97.0%, clear frames only 95.7%.
- **Violation missed as present:** all 1.1%.

## Vest

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 1806 | PRESENT | 1128 (62.5%) | 167 (9.2%) | 511 (28.3%) |
| all people | 561 | ABSENT | 5 (0.9%) | 5 (0.9%) | 551 (98.2%) |
| clear frames only | 1353 | PRESENT | 1029 (76.1%) | 118 (8.7%) | 206 (15.2%) |
| clear frames only | 378 | ABSENT | 5 (1.3%) | 3 (0.8%) | 370 (97.9%) |
| height 90-200 px | 409 | PRESENT | 288 (70.4%) | 28 (6.8%) | 93 (22.7%) |
| height 90-200 px | 65 | ABSENT | 2 (3.1%) | 1 (1.5%) | 62 (95.4%) |
| height <90 px | 373 | PRESENT | 61 (16.4%) | 36 (9.7%) | 276 (74.0%) |
| height <90 px | 161 | ABSENT | 0 (0.0%) | 2 (1.2%) | 159 (98.8%) |
| height >=200 px | 1024 | PRESENT | 779 (76.1%) | 103 (10.1%) | 142 (13.9%) |
| height >=200 px | 335 | ABSENT | 3 (0.9%) | 2 (0.6%) | 330 (98.5%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 28.3%, clear frames only 15.2%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 98.2%, clear frames only 97.9%.
- **Violation missed as present:** all 0.9%.
