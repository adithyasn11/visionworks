# Construction-PPE: per-frame reading against explicit labels

Images: 100 (all); used: 100; people: 236 (of which clear by the live loop's gate: 173).

Dataset: Ultralytics Construction-PPE, CC BY 4.0. Part of it is generated imagery. Our PPE model was not trained on this export, but its source images may overlap with public sets we used.

Reading: PRESENT = best matching detection >= 0.45; ABSENT = nothing >= 0.20 matches; AMBIGUOUS = between. This is ONE frame. The deployed system needs 8+ clear frames over a window before it says anything, so these are the numbers the temporal layer starts from, not the system's final accuracy.


## Helmet

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 165 | PRESENT | 141 (85.5%) | 9 (5.5%) | 15 (9.1%) |
| all people | 60 | ABSENT | 7 (11.7%) | 1 (1.7%) | 52 (86.7%) |
| clear frames only | 130 | PRESENT | 125 (96.2%) | 2 (1.5%) | 3 (2.3%) |
| clear frames only | 38 | ABSENT | 6 (15.8%) | 0 (0.0%) | 32 (84.2%) |
| height 90-200 px | 42 | PRESENT | 39 (92.9%) | 2 (4.8%) | 1 (2.4%) |
| height 90-200 px | 12 | ABSENT | 5 (41.7%) | 0 (0.0%) | 7 (58.3%) |
| height <90 px | 26 | PRESENT | 7 (26.9%) | 7 (26.9%) | 12 (46.2%) |
| height <90 px | 21 | ABSENT | 1 (4.8%) | 1 (4.8%) | 19 (90.5%) |
| height >=200 px | 97 | PRESENT | 95 (97.9%) | 0 (0.0%) | 2 (2.1%) |
| height >=200 px | 27 | ABSENT | 1 (3.7%) | 0 (0.0%) | 26 (96.3%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 9.1%, clear frames only 2.3%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 86.7%, clear frames only 84.2%.
- **Violation missed as present:** all 11.7%.

## Vest

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 156 | PRESENT | 143 (91.7%) | 5 (3.2%) | 8 (5.1%) |
| all people | 73 | ABSENT | 4 (5.5%) | 1 (1.4%) | 68 (93.2%) |
| clear frames only | 117 | PRESENT | 113 (96.6%) | 1 (0.9%) | 3 (2.6%) |
| clear frames only | 53 | ABSENT | 1 (1.9%) | 0 (0.0%) | 52 (98.1%) |
| height 90-200 px | 50 | PRESENT | 49 (98.0%) | 1 (2.0%) | 0 (0.0%) |
| height 90-200 px | 5 | ABSENT | 1 (20.0%) | 0 (0.0%) | 4 (80.0%) |
| height <90 px | 31 | PRESENT | 22 (71.0%) | 4 (12.9%) | 5 (16.1%) |
| height <90 px | 18 | ABSENT | 3 (16.7%) | 1 (5.6%) | 14 (77.8%) |
| height >=200 px | 75 | PRESENT | 72 (96.0%) | 0 (0.0%) | 3 (4.0%) |
| height >=200 px | 50 | ABSENT | 0 (0.0%) | 0 (0.0%) | 50 (100.0%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 5.1%, clear frames only 2.6%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 93.2%, clear frames only 98.1%.
- **Violation missed as present:** all 5.5%.
