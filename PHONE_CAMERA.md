# Using your phone as a camera

A laptop webcam points at your face from 40cm. A CCTV camera looks down at a
room from a corner. Everything in this project — the seat prior, the chair-exit
debounce, the cross-camera handoff — was designed for the second view, and a
phone propped on a shelf is the only way to produce it without buying hardware.

This is a one-time setup. After it, starting the phone camera is one click on
the laptop.

---

## Why there is a setup at all

A laptop cannot switch on a phone's camera. No browser or operating system
permits it, and one that did would be a serious security hole — a web page
could watch you through a device you were not holding.

So the phone grants permission **once**, by opening a page you send it. After
that it stays paired and waits, and the laptop's click is a genuine one click.

The other half of the setup is HTTPS. Browsers refuse camera access on an
insecure address, with `localhost` as the only exception — which is why the
laptop's own webcam works today and a phone at `http://192.168.3.5:3000` would
not.

---

## One-time setup

### 1. Generate a certificate

```
venv/Scripts/python.exe scripts/make_dev_cert.py
```

It reads this laptop's current network addresses and writes a certificate
naming them, into `certs/`. Both files are gitignored — the private key must
never be committed.

> **If your laptop joins a different Wi-Fi network its IP changes and the
> certificate stops matching.** Run this again when that happens; it takes a
> second.

### 2. Start both servers over HTTPS

One command:

```
python run_system.py --https
```

It generates the certificate if there is not one — so step 1 is optional, and
this is really the only command you need. It also regenerates automatically if
this laptop has changed network since the last run, because a certificate that
does not name the address the phone dialled is rejected with an error that
looks nothing like "your IP changed".

When it is up it prints the address to open. Use that one, not localhost.

Both servers listen on `0.0.0.0`, so your phone can reach them. On a home or
lab network that is fine. **Do not do this on a public network** — anything on
the same Wi-Fi can reach these ports.

Plain `python run_system.py` still starts everything over http for normal
laptop-only work, which is what you want day to day: the certificate warning is
a small tax you should not pay when no phone is involved.

### 3. Open the dashboard by IP, not localhost

On the laptop, go to **`https://<your-laptop-ip>:3000`** rather than
`https://localhost:3000`.

This matters more than it looks. The QR code contains whatever address the
dashboard is being viewed at — so a dashboard opened at `localhost` produces a
QR pointing at *the phone's own* localhost, where nothing is listening.

The script prints your IP. On the run that wrote this file it was
`192.168.3.5`.

Your browser will warn about the certificate. Click **Advanced → Proceed**.

---

## Pairing the phone

1. **Live feed** → **Use my phone as a camera**
2. Scan the QR code with the phone's camera app
3. The phone shows a security warning — tap **Advanced → Proceed**. It is the
   same self-signed certificate; the phone has simply never seen it before.
4. Allow camera access when asked
5. The phone says **Ready**, the laptop says **Phone paired**

### The second warning

The phone must accept the certificate for **both** `:3000` and `:8001`. They
are separate origins as far as the browser is concerned.

If the phone pairs but no frames arrive, this is almost always why. Open
`https://<laptop-ip>:8001/docs` on the phone once, accept the warning there
too, and go back to the camera page.

---

## Everyday use

1. Prop the phone where it can see the desks — a shelf, a monitor stand, a
   book. Higher and further back is better; the pipeline is happier with a
   whole person in frame than with a close-up.
2. On the laptop: **Live feed** → **Turn on phone camera**
3. The phone opens its camera and the feed appears on the laptop with the
   detection overlay and any zones you have drawn.

The phone screen must stay on. The page asks the operating system to keep it
awake, but not every browser honours that request — check your phone's screen
timeout if the feed stops after a minute.

---

## Drawing zones on the phone's view

Once the phone is streaming, its frames become the backdrop in **Zones**
automatically. Draw the desk outlines directly over what the phone is seeing.

Zones belong to the camera they were drawn on, so if you use both the laptop
webcam and the phone, each keeps its own set. Give them distinct camera names.

---

## When it does not work

| What you see | What it means |
|---|---|
| Phone: "Camera access was refused" | Tap the padlock in the phone's address bar, allow Camera, reload |
| Phone pairs, no frames on the laptop | The `:8001` certificate has not been accepted — see above |
| "This pairing code has expired" | Codes last five minutes. Press **New code** |
| Feed stops after a minute | The phone screen locked. Increase the screen timeout |
| QR scans to a page that will not load | The dashboard was opened at `localhost`. Use the laptop's IP |
| "Another device took over this pairing" | A second phone scanned the same code. One camera per code |

---

## What this is and is not

The pairing code is the credential on the phone side: eight characters,
single-use, expiring in five minutes, reachable only from inside your network.
The laptop side needs a real signed-in session, as every other endpoint does.

That is honest security for a development tool on a trusted network. It is
**not** enough to expose these ports to the internet, and the backend logs a
warning saying so every time a code is created.

Frames are JPEGs, each carrying the instant it was captured. A continuous video
stream would use less bandwidth but has to be buffered before it can be
decoded, which costs 1–3 seconds and blurs exactly the timing that chair-exit
detection and cross-camera handoff depend on. On your own Wi-Fi the bandwidth
is free; the timing is not.
