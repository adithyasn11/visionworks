# VisionWorks demo runbook

For a 6–8 minute live demonstration. Everything the system does in it is real; the rehearsal and fallback steps below exist so that a failed camera, a flat battery or a bad network does not end the demo.

---

## 1. The set

**Layout (taped on the floor, about 4 m × 3 m, in view of one camera)**

```
  [ PPE checkpoint ]  ->  [ approach ]  ->  [ RESTRICTED ]  ->  [ EXIT mark ]
        (A)                    (B)               (C)                 (D)
```

- One camera: the laptop webcam, or a phone paired by QR (use `python run_system.py --https`, open the printed address on the phone, scan the code on the Live feed tab). Mount it high enough to see all four areas and the people's full height.
- Props: two helmets, two high-visibility vests, one grey/dark vest-less top.
- People: three volunteers in clearly different clothing: **Worker 1** fully equipped, **Worker 2** vest but no helmet, **Worker 3** who will lie down and stand far away.
- Lighting: even, no strong window behind the area.
- Laptop on power, set to "best performance", other heavy apps closed.

**On the screen**: the dashboard on the laptop display; a second window with the Evaluation screen ready; the printed results table (section 6) beside you.

---

## 2. Before every run (ten minutes)

```bash
python run_system.py --https
```

```bash
venv/Scripts/python.exe scripts/demo_preflight.py
```

Preflight must say READY. Then, in the dashboard:

1. Sign in as a manager.
2. Cameras tab: a camera must be registered and selected (the Live feed shows a red notice if there is none). Without it the flags show on the Live feed but never reach Review, Events or Reports.
3. Zones tab: delete any leftover zones so the drawing is part of the show.
4. Rules tab: delete rules from earlier runs.
5. Review tab: dismiss or leave old flags (the Overview counts them), whichever you prefer to show.
6. Stand in the taped area for 10 seconds on the Live feed: the people boxes should be steady on each person.

If the browser says the certificate is not trusted, trust it once on the laptop (the phone only needs to open the printed address and continue):

```bash
certutil -user -addstore Root "D:\major project\certs\dev-cert.pem"
```

---

## 3. Storyline

Times are guides. Say the sentence in *italics*, do the action, point at the result.

| # | Do | See | Say |
|---|----|-----|-----|
| 1 | Zones tab: draw A as **PPE checkpoint**, B **Approach**, C **Restricted**, D **Exit**. Rules tab: add *PPE at entry* (helmet and vest, via checkpoint) on C, *max time* 20 s on C, *lone worker* (min 2) on C, *man-down* (5 s, CRITICAL). | Four coloured zones, four rules with plain-language summaries | *The rules are data, not code. A manager sets them in a minute.* |
| 2 | Worker 1 (equipped) walks A → B → C. | Live feed: green checks. Review/Events: **COMPLIANT** with the numbers | *It looked at many frames, not one, and saw both items in nearly all of them.* |
| 3 | Worker 2 (no helmet) walks in. | After about 1–2 s: **POTENTIAL VIOLATION**, evidence picture (head blurred), reason with frame counts | *It says "potential": a person decides. The reason shows its working.* |
| 4 | Worker 3 stands far from the camera and walks in. | **UNVERIFIABLE**: "could not verify" with the reason | *Too far to see a helmet, so it refuses to accuse. The simpler systems we compared against would have flagged this person.* |
| 5 | Set the time limit to 10 s (Rules tab, edit). Worker 1 stays in C. | Warning at 80 %, then the limit-reached flag | *Same engine, a different rule.* |
| 6 | Worker 1 alone in C (the others step out). | After the grace period: lone-worker flag | *Fewer than two people where two are required.* |
| 7 | Worker 3 lies down in C and stays still. | About 5–6 s later: **CRITICAL** man-down flag | *It also does not flag someone bending or sitting; that was tested.* |
| 8 | Review tab: confirm Worker 2's flag, dismiss one with a reason. Reports tab: generate the shift report (PDF). Evaluation tab. | Review counts change; PDF downloads; evaluation table | *Humans stay in the loop; every action is audited. Here is where it is wrong:* then read the "not measured" list. |

**Questions you should expect, and honest answers**

- *Does it identify people?* No. Workers are anonymous track numbers; the pictures are blurred at the head; nothing stores a name or a face.
- *Why not just flag everything it cannot see?* That is exactly what makes alert systems get ignored. It counts them as "could not verify" and reports how many there were.
- *What are the errors?* 6 false flags in 321 correctly-equipped items on held-out images, all items the detector missed in every frame; it was not zero and the document says so. Man-down recall on real falls was not measured.
- *Why PPE at 0.6 / 0.9?* From the 64-setting sweep, which is shown on the Evaluation screen; the middle of a flat band.

---

## 4. Fallback clips

Record one clip per scenario *during a rehearsal*, with the same camera and layout, 10–30 seconds each, and save them in `sample_videos/demo_fallback/` (git-ignored) with these names:

| File | Scene |
|------|-------|
| `compliant.mp4` | Equipped worker enters the restricted zone |
| `no_helmet.mp4` | Worker without a helmet enters |
| `unverifiable.mp4` | Worker far away or partly hidden enters |
| `dwell.mp4` | Worker stays longer than a short limit |
| `lone_worker.mp4` | One person in the two-person zone |
| `man_down.mp4` | Worker lies down and stays still |

`scripts/demo_preflight.py` lists which are present. To use one: Live feed tab → Upload video → pick the clip. It runs through exactly the same pipeline, with the same zones and rules, and works with no camera and no internet (the local database keeps the decisions and the dashboard shows them once the network is back; the Live feed page itself shows the verdicts immediately).

If the **whole laptop** fails: the Evaluation page content is in `docs/SAFETY_EVALUATION.md`; print it, or open it on a phone.

---

## 5. If something goes wrong

| Symptom | Do |
|---------|----|
| Page unstyled or assets 404 | Stop the servers, delete `frontend/.next`, start again |
| "Not private" warning | Trust the certificate (section 2) |
| Camera black | Another app holds it. Close it, or switch to a phone camera or a fallback clip |
| Nobody gets detected | Raise the camera, light the area, step back so full height is visible |
| Flags not on the Review tab yet | They sync in the background (about a minute). The Live feed shows them at once |
| Backend stopped | `python run_system.py --https` again; open visits were closed as "camera stopped" and nothing was lost |
| Camera unplugged mid-demo | After 5 s the page says the camera stopped; open stays are closed as UNVERIFIABLE; plug it back in and carry on |

---

## 6. Printed results table (from `docs/SAFETY_EVALUATION.md`)

**Helmet and vest, 454 held-out person-and-item segments**

| System | False flags (of 321 wearing it) | Missed (of 133 without) | Could not verify | Error among verdicts |
|---|---:|---:|---:|---:|
| One frame decides | 37 | 11 | 0 | 10.6 % |
| Majority over the window | 47 | 9 | 0 | 12.3 % |
| **This system** | **6** | **6** | **116 (25.5 %)** | **3.5 %** |

**Scenario suite:** 270 of 270 runs correct (entry/exit times within 0.1 s; man-down alert 5.6 s after a scripted fall).

**Speed on the RTX 4060 laptop GPU:** 13.7 to 34 processed frames a second on the four test clips (target 10).

**Not measured:** own-site footage; man-down recall on real falls; human-judged precision; the speed with identity on.

---

## 7. Rehearsal log (fill in)

The demo is ready when the storyline has run end to end twice in a row, and each fallback clip plays offline.

| Run | Date | All 8 steps | Anything to fix |
|-----|------|-------------|-----------------|
| 1 | | | |
| 2 | | | |
| 3 | | | |

Fallback clips recorded: ☐ compliant ☐ no_helmet ☐ unverifiable ☐ dwell ☐ lone_worker ☐ man_down
