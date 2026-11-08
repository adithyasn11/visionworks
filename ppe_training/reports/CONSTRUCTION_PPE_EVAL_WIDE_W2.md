# Construction-PPE: per-frame reading against explicit labels

WIDE SCENES ONLY (3-8 people, each under 30% of the image height). Images: 1000 (all); used: 152; people: 734 (of which clear by the live loop's gate: 349).

Dataset: Ultralytics Construction-PPE, CC BY 4.0. Part of it is generated imagery. Our PPE model was not trained on this export, but its source images may overlap with public sets we used.

Reading: PRESENT = best matching detection >= 0.45; ABSENT = nothing >= 0.20 matches; AMBIGUOUS = between. This is ONE frame. The deployed system needs 8+ clear frames over a window before it says anything, so these are the numbers the temporal layer starts from, not the system's final accuracy.


## Helmet

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 378 | PRESENT | 232 (61.4%) | 31 (8.2%) | 115 (30.4%) |
| all people | 326 | ABSENT | 4 (1.2%) | 11 (3.4%) | 311 (95.4%) |
| clear frames only | 236 | PRESENT | 186 (78.8%) | 15 (6.4%) | 35 (14.8%) |
| clear frames only | 113 | ABSENT | 1 (0.9%) | 8 (7.1%) | 104 (92.0%) |
| height 90-200 px | 204 | PRESENT | 158 (77.5%) | 14 (6.9%) | 32 (15.7%) |
| height 90-200 px | 101 | ABSENT | 2 (2.0%) | 6 (5.9%) | 93 (92.1%) |
| height <90 px | 137 | PRESENT | 42 (30.7%) | 15 (10.9%) | 80 (58.4%) |
| height <90 px | 209 | ABSENT | 2 (1.0%) | 3 (1.4%) | 204 (97.6%) |
| height >=200 px | 37 | PRESENT | 32 (86.5%) | 2 (5.4%) | 3 (8.1%) |
| height >=200 px | 16 | ABSENT | 0 (0.0%) | 2 (12.5%) | 14 (87.5%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 30.4%, clear frames only 14.8%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 95.4%, clear frames only 92.0%.
- **Violation missed as present:** all 1.2%.

## Vest

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 563 | PRESENT | 348 (61.8%) | 40 (7.1%) | 175 (31.1%) |
| all people | 152 | ABSENT | 7 (4.6%) | 2 (1.3%) | 143 (94.1%) |
| clear frames only | 307 | PRESENT | 253 (82.4%) | 17 (5.5%) | 37 (12.1%) |
| clear frames only | 42 | ABSENT | 6 (14.3%) | 0 (0.0%) | 36 (85.7%) |
| height 90-200 px | 268 | PRESENT | 220 (82.1%) | 16 (6.0%) | 32 (11.9%) |
| height 90-200 px | 37 | ABSENT | 4 (10.8%) | 0 (0.0%) | 33 (89.2%) |
| height <90 px | 249 | PRESENT | 90 (36.1%) | 22 (8.8%) | 137 (55.0%) |
| height <90 px | 108 | ABSENT | 1 (0.9%) | 2 (1.9%) | 105 (97.2%) |
| height >=200 px | 46 | PRESENT | 38 (82.6%) | 2 (4.3%) | 6 (13.0%) |
| height >=200 px | 7 | ABSENT | 2 (28.6%) | 0 (0.0%) | 5 (71.4%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 31.1%, clear frames only 12.1%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 94.1%, clear frames only 85.7%.
- **Violation missed as present:** all 4.6%.
