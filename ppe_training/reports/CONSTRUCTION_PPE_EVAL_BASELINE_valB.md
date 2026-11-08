# Construction-PPE: per-frame reading against explicit labels

Images: 100 (all); used: 100; people: 236 (of which clear by the live loop's gate: 173).

Dataset: Ultralytics Construction-PPE, CC BY 4.0. Part of it is generated imagery. Our PPE model was not trained on this export, but its source images may overlap with public sets we used.

Reading: PRESENT = best matching detection >= 0.45; ABSENT = nothing >= 0.20 matches; AMBIGUOUS = between. This is ONE frame. The deployed system needs 8+ clear frames over a window before it says anything, so these are the numbers the temporal layer starts from, not the system's final accuracy.


## Helmet

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 165 | PRESENT | 112 (67.9%) | 13 (7.9%) | 40 (24.2%) |
| all people | 60 | ABSENT | 0 (0.0%) | 1 (1.7%) | 59 (98.3%) |
| clear frames only | 130 | PRESENT | 102 (78.5%) | 10 (7.7%) | 18 (13.8%) |
| clear frames only | 38 | ABSENT | 0 (0.0%) | 1 (2.6%) | 37 (97.4%) |
| height 90-200 px | 42 | PRESENT | 26 (61.9%) | 5 (11.9%) | 11 (26.2%) |
| height 90-200 px | 12 | ABSENT | 0 (0.0%) | 1 (8.3%) | 11 (91.7%) |
| height <90 px | 26 | PRESENT | 2 (7.7%) | 2 (7.7%) | 22 (84.6%) |
| height <90 px | 21 | ABSENT | 0 (0.0%) | 0 (0.0%) | 21 (100.0%) |
| height >=200 px | 97 | PRESENT | 84 (86.6%) | 6 (6.2%) | 7 (7.2%) |
| height >=200 px | 27 | ABSENT | 0 (0.0%) | 0 (0.0%) | 27 (100.0%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 24.2%, clear frames only 13.8%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 98.3%, clear frames only 97.4%.
- **Violation missed as present:** all 0.0%.

## Vest

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 156 | PRESENT | 100 (64.1%) | 9 (5.8%) | 47 (30.1%) |
| all people | 73 | ABSENT | 0 (0.0%) | 1 (1.4%) | 72 (98.6%) |
| clear frames only | 117 | PRESENT | 91 (77.8%) | 7 (6.0%) | 19 (16.2%) |
| clear frames only | 53 | ABSENT | 0 (0.0%) | 1 (1.9%) | 52 (98.1%) |
| height 90-200 px | 50 | PRESENT | 41 (82.0%) | 2 (4.0%) | 7 (14.0%) |
| height 90-200 px | 5 | ABSENT | 0 (0.0%) | 0 (0.0%) | 5 (100.0%) |
| height <90 px | 31 | PRESENT | 4 (12.9%) | 1 (3.2%) | 26 (83.9%) |
| height <90 px | 18 | ABSENT | 0 (0.0%) | 0 (0.0%) | 18 (100.0%) |
| height >=200 px | 75 | PRESENT | 55 (73.3%) | 6 (8.0%) | 14 (18.7%) |
| height >=200 px | 50 | ABSENT | 0 (0.0%) | 1 (2.0%) | 49 (98.0%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 30.1%, clear frames only 16.2%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 98.6%, clear frames only 98.1%.
- **Violation missed as present:** all 0.0%.
