# Construction-PPE: per-frame reading against explicit labels

WIDE SCENES ONLY (3-8 people, each under 30% of the image height). Images: 1000 (all); used: 152; people: 734 (of which clear by the live loop's gate: 349).

Dataset: Ultralytics Construction-PPE, CC BY 4.0. Part of it is generated imagery. Our PPE model was not trained on this export, but its source images may overlap with public sets we used.

Reading: PRESENT = best matching detection >= 0.45; ABSENT = nothing >= 0.20 matches; AMBIGUOUS = between. This is ONE frame. The deployed system needs 8+ clear frames over a window before it says anything, so these are the numbers the temporal layer starts from, not the system's final accuracy.


## Helmet

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 378 | PRESENT | 200 (52.9%) | 24 (6.3%) | 154 (40.7%) |
| all people | 326 | ABSENT | 2 (0.6%) | 9 (2.8%) | 315 (96.6%) |
| clear frames only | 236 | PRESENT | 186 (78.8%) | 15 (6.4%) | 35 (14.8%) |
| clear frames only | 113 | ABSENT | 1 (0.9%) | 8 (7.1%) | 104 (92.0%) |
| height 90-200 px | 204 | PRESENT | 158 (77.5%) | 14 (6.9%) | 32 (15.7%) |
| height 90-200 px | 101 | ABSENT | 2 (2.0%) | 6 (5.9%) | 93 (92.1%) |
| height <90 px | 137 | PRESENT | 10 (7.3%) | 8 (5.8%) | 119 (86.9%) |
| height <90 px | 209 | ABSENT | 0 (0.0%) | 1 (0.5%) | 208 (99.5%) |
| height >=200 px | 37 | PRESENT | 32 (86.5%) | 2 (5.4%) | 3 (8.1%) |
| height >=200 px | 16 | ABSENT | 0 (0.0%) | 2 (12.5%) | 14 (87.5%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 40.7%, clear frames only 14.8%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 96.6%, clear frames only 92.0%.
- **Violation missed as present:** all 0.6%.

## Vest

| condition | people | ground truth | read PRESENT | AMBIGUOUS | read ABSENT |
|---|---:|---|---:|---:|---:|
| all people | 563 | PRESENT | 297 (52.8%) | 51 (9.1%) | 215 (38.2%) |
| all people | 152 | ABSENT | 6 (3.9%) | 4 (2.6%) | 142 (93.4%) |
| clear frames only | 307 | PRESENT | 253 (82.4%) | 28 (9.1%) | 26 (8.5%) |
| clear frames only | 42 | ABSENT | 6 (14.3%) | 2 (4.8%) | 34 (81.0%) |
| height 90-200 px | 268 | PRESENT | 220 (82.1%) | 24 (9.0%) | 24 (9.0%) |
| height 90-200 px | 37 | ABSENT | 4 (10.8%) | 0 (0.0%) | 33 (89.2%) |
| height <90 px | 249 | PRESENT | 39 (15.7%) | 23 (9.2%) | 187 (75.1%) |
| height <90 px | 108 | ABSENT | 0 (0.0%) | 2 (1.9%) | 106 (98.1%) |
| height >=200 px | 46 | PRESENT | 38 (82.6%) | 4 (8.7%) | 4 (8.7%) |
| height >=200 px | 7 | ABSENT | 2 (28.6%) | 2 (28.6%) | 3 (42.9%) |

- **False accusation, one frame** (labelled worn but read ABSENT): all 38.2%, clear frames only 8.5%.
- **Violation caught, one frame** (labelled missing and read ABSENT): all 93.4%, clear frames only 81.0%.
- **Violation missed as present:** all 3.9%.
