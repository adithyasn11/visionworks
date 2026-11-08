# Construction-PPE: per-frame reading against explicit labels

WIDE SCENES ONLY (3-8 people, each under 30% of the image height). Images: 1000 (all); used: 152; people: 734 (of which clear by the live loop's gate: 349).

Dataset: Ultralytics Construction-PPE, CC BY 4.0. Part of it is generated imagery. Our PPE model was not trained on this export, but its source images may overlap with public sets we used.

Reading: PRESENT = best matching detection >= 0.45; ABSENT = nothing >= 0.20 matches; AMBIGUOUS = between. This is ONE frame. The deployed system needs 8+ clear frames over a window before it says anything, so these are the numbers the temporal layer starts from, not the system's final accuracy.


## Helmet

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 378 | PRESENT | 156 (41.3%) | 39 (10.3%) | 183 (48.4%) |
| all people | 326 | ABSENT | 1 (0.3%) | 7 (2.1%) | 318 (97.5%) |
| clear frames only | 236 | PRESENT | 147 (62.3%) | 30 (12.7%) | 59 (25.0%) |
| clear frames only | 113 | ABSENT | 0 (0.0%) | 6 (5.3%) | 107 (94.7%) |
| height 90-200 px | 204 | PRESENT | 120 (58.8%) | 29 (14.2%) | 55 (27.0%) |
| height 90-200 px | 101 | ABSENT | 1 (1.0%) | 5 (5.0%) | 95 (94.1%) |
| height <90 px | 137 | PRESENT | 8 (5.8%) | 7 (5.1%) | 122 (89.1%) |
| height <90 px | 209 | ABSENT | 0 (0.0%) | 1 (0.5%) | 208 (99.5%) |
| height >=200 px | 37 | PRESENT | 28 (75.7%) | 3 (8.1%) | 6 (16.2%) |
| height >=200 px | 16 | ABSENT | 0 (0.0%) | 1 (6.2%) | 15 (93.8%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 48.4%, clear frames only 25.0%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 97.5%, clear frames only 94.7%.
- **Violation missed as present:** all 0.3%.

## Vest

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 563 | PRESENT | 253 (44.9%) | 46 (8.2%) | 264 (46.9%) |
| all people | 152 | ABSENT | 3 (2.0%) | 3 (2.0%) | 146 (96.1%) |
| clear frames only | 307 | PRESENT | 210 (68.4%) | 23 (7.5%) | 74 (24.1%) |
| clear frames only | 42 | ABSENT | 3 (7.1%) | 1 (2.4%) | 38 (90.5%) |
| height 90-200 px | 268 | PRESENT | 180 (67.2%) | 21 (7.8%) | 67 (25.0%) |
| height 90-200 px | 37 | ABSENT | 2 (5.4%) | 1 (2.7%) | 34 (91.9%) |
| height <90 px | 249 | PRESENT | 38 (15.3%) | 23 (9.2%) | 188 (75.5%) |
| height <90 px | 108 | ABSENT | 0 (0.0%) | 2 (1.9%) | 106 (98.1%) |
| height >=200 px | 46 | PRESENT | 35 (76.1%) | 2 (4.3%) | 9 (19.6%) |
| height >=200 px | 7 | ABSENT | 1 (14.3%) | 0 (0.0%) | 6 (85.7%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 46.9%, clear frames only 24.1%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 96.1%, clear frames only 90.5%.
- **Violation missed as present:** all 2.0%.
