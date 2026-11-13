# VisionWorks: manual test guide, from an empty database

This guide walks you through the whole app from nothing to a finished safety report. Work through it top to bottom and tick each box as you go. If something does not do what the **Expect** line says, note the step number and what you saw.

**Starting state, wiped on 2026-11-13:**
- **Supabase:**
  - no organisations, profiles, members, sites, cameras, zones, rules, events, decisions, actions or reports;
  - no sign-in accounts;
  - only the three plan limits (Starter / Growth / Enterprise) are left.
- **Local pipeline database** (`workplace_analytics.db`): empty.
- **Evidence pictures** (`backend/evidence/`): empty.

**What you need:**
- Two email addresses, or one Google account plus one email. The second one is for the "invite a member" steps.
- A second browser or a private window, for the second person.
- A test video with people in it. Good choices from `sample_videos/`:
  - `ppe_demo.mp4`
  - `ppe_demo_construction.mp4`
  - `ppe_demo_gaps.mp4`
- Your phone, optional, for the phone-camera step. It must be on the same Wi-Fi as the laptop.

---

## 0. Start the app

- [ ] **0.1** Double-click `start_visionworks.bat` in the project folder.
  - **Expect:** a console window opens, builds the frontend if needed, and the browser opens at `http://localhost:3000`.
- [ ] **0.2** Check the backend at `http://localhost:8001/docs`.
  - **Expect:** the FastAPI docs page loads.

> Phone camera (step 6.5) needs HTTPS. For that step, close the window and run `start_visionworks.bat phone`, then open the `https://<laptop-ip>:3000` address it prints.

---

## 1. Public pages (signed out)

### 1.1 First-visit intro
- [ ] Open `http://localhost:3000` in a fresh tab (or a private window).
  - **Expect:** the VisionWorks intro animation plays once, about 1.5 s, then the landing page shows.
- [ ] Reload the page.
  - **Expect:** no intro this time, because it plays only once per browser session.

### 1.2 Landing page
- [ ] Scroll the whole page.
  - **Expect:** every section loads, nothing overlaps, and there is no sideways scrolling.
- [ ] Click each link in the top navbar.
  - **Expect:** each one scrolls or goes to the right section or page.
- [ ] Click the **theme toggle** (sun/moon).
  - **Expect:** light ↔ dark switches instantly. Light mode is a soft rose-white with black borders; dark is near-black.
- [ ] Reload.
  - **Expect:** the theme you picked is remembered.
- [ ] Footer links: open **Features** (`/features`) and **Security** (`/security`).
  - **Expect:** both pages load and are themed.
- [ ] Make the browser window narrow (phone width), then wide.
  - **Expect:** text and layout shrink smoothly, the navbar collapses to a menu, and nothing is cut off.

### 1.3 Sign-in page look
- [ ] Click **Log in** on the landing page.
  - **Expect:** the sign-in page slides in smoothly with no lag.
- [ ] Watch the left panel (desktop width only) for about 30 s.
  - **Expect:**
    - workers walk around randomly with trails;
    - helmet/vest checks fire and green **OK**, red **NO HELMET** / **NO VEST** and grey **UNVERIFIED** labels appear;
    - sometimes a worker falls and shows **MAN DOWN**;
    - the **Live events** list under the scene fills in, newest row on top.
- [ ] Switch theme on this page.
  - **Expect:** the scene recolours straight away.

---

## 2. Accounts

### 2.1 Validation
- [ ] On **Sign in**, click **Sign in** with both fields empty.
  - **Expect:** a clear message asking for an email and password.
- [ ] Type a wrong email and password and submit.
  - **Expect:** "invalid credentials"-style error; you stay on the page.
- [ ] Click **Show** next to the password.
  - **Expect:** the password becomes visible, and **Hide** hides it again.

### 2.2 Create account A (you, the administrator)
Choose one way.

**Option 1, email:**
- [ ] Click **Create an account** → fill in name, email and password → submit.
  - **Expect:** you are signed in straight away and land on Home. No verification email is sent.
- [ ] Try signing up again with the same email.
  - **Expect:** "An account with that email already exists. Try signing in instead."

**Option 2, Google:**
- [ ] Click **Continue with Google** → pick your account.
  - **Expect:** you come back to `http://localhost:3000/...` signed in, with **no SSL error**.

### 2.3 Where you land
- [ ] **Expect:** with no organisation yet, you land on **Home** (plans) or **Onboarding**, not the dashboard.

---

## 3. Home and plan choice

- [ ] **3.1** On Home, read the **Plans** section.
  - **Expect:** Starter, Growth and Enterprise cards. One is marked **Most popular**.
- [ ] **3.2** Flip the **Billing period** switch (monthly ↔ yearly).
  - **Expect:** prices change, and the yearly price says "12 months for the price of 10".
- [ ] **3.3** Click **Choose a plan** on a paid plan (for example Growth).
  - **Expect:** the **Checkout** page opens with:
    - the plan name;
    - the billing period switch;
    - "What's included";
    - "Total charged today".
- [ ] **3.4** Change the billing period on Checkout.
  - **Expect:** the total updates.
- [ ] **3.5** Continue.
  - **Expect:** you move on to onboarding, with the chosen plan shown there.

> No real payment is taken. The checkout is a demo flow.

---

## 4. Onboarding (organisation → site → camera)

The progress bar at the top shows **Organisation · Site · Camera**.

### Step 1, Organisation
- [ ] Leave **Organisation name** empty and press next.
  - **Expect:** it will not continue and asks for a name.
- [ ] Enter a name (for example `Test Plant Ltd`) and continue.

### Step 2, Site
- [ ] Fill in:
  - **Site name** (for example `Plant 1 — Assembly hall`);
  - **Timezone**;
  - **Working days** (click days on and off);
  - **Working hours** (start and end).
- [ ] Try an end time earlier than the start time.
  - **Expect:** either a validation message or it is treated as an overnight shift. Note which.
- [ ] Use **Back**.
  - **Expect:** step 1 still holds what you typed.
- [ ] Continue.

### Step 3, Camera
- [ ] Enter a **Camera name** (for example `Gate 1 — Entrance`).
- [ ] Click each **Video source** option in turn:
  - **Upload a video**;
  - **Webcam**;
  - **IP camera (RTSP)**: a **Stream URL** field appears.
- [ ] Pick **Upload a video** and finish.
  - **Expect:** the organisation is created and you land on the **Dashboard**.

---

## 5. Dashboard shell

- [ ] **5.1** Sidebar sections:
  - **Monitor:** Overview, Review, Live feed, Events.
  - **Safety:** Cameras, Zones, Rules, Actions, Reports, Evaluation.
  - **Account:** Plan, Members, Organisation.
  - **Expect:** clicking each item switches the page with a short red progress bar at the top and a fade-in. The page scrolls back to the top.
- [ ] **5.2** Make the window short (about 600 px tall).
  - **Expect:** the sidebar still fits with no scrollbar.
- [ ] **5.3** Narrow the window to phone width.
  - **Expect:** the sidebar becomes a menu button and the content stays readable.
- [ ] **5.4** **Refresh** button (top right).
  - **Expect:** the data reloads. On a phone it shows as an icon only.
- [ ] **5.5** **Account** menu (your avatar).
  - **Expect:** it shows your name/email, **Plans** and **Sign out**. Don't sign out yet.
- [ ] **5.6** Theme toggle inside the dashboard.
  - **Expect:** every panel switches, with no leftover dark blocks in light mode.
- [ ] **5.7** Footer note "Workers stay anonymous."
  - **Expect:** it is visible, or hidden on short screens.

---

## 6. Cameras and live feed

### 6.1 Cameras screen
- [ ] Open **Cameras**.
  - **Expect:**
    - the camera from onboarding is listed;
    - the plan limit is shown, for example "1 of 1 cameras in use".
- [ ] **Add a camera**:
  - type a name (for example `Gate camera`);
  - choose **Video comes from**;
  - click **Add camera**.
  - **Expect:** it is added, or, if the plan limit is reached, a clear message saying so.
- [ ] Click **Use this camera** on a camera.
  - **Expect:** it becomes the active camera for Live feed and Zones.
- [ ] Remove the extra camera.
  - **Expect:** it disappears from the list.

### 6.2 Zones (do this before uploading video)
- [ ] Open **Zones**.
  - **Expect:** the **Zone Mapping** editor shows a camera image, or "No camera image." until video has been run.
- [ ] Use the backdrop picker (**Live webcam** or a sample video).
  - **Expect:** a frame appears behind the editor.
- [ ] Click **Draw zone**, click points on the image to outline an area, then **Save**.
- [ ] Give the zone a **name** and a **type**. Make all three of these:
  - `Checkpoint`, type **PPE checkpoint**;
  - `Press area`, type **Restricted**;
  - `Exit door`, type **Exit**.
- [ ] Mid-drawing, try **Undo** and **Cancel**.
  - **Expect:** **Undo** removes the last point; **Cancel** throws the shape away.
- [ ] Click a zone's name to **rename** it.
  - **Expect:** the new name is saved.
- [ ] Delete a zone, then draw it again.
- [ ] Use **Refresh**.
  - **Expect:** the zones reload from the server, the same ones you saved.

### 6.3 Rules
- [ ] Open **Rules** and click **New rule**.
- [ ] Create one rule of each type. Use the zones from 6.2 where asked.

  | Rule type | Settings to try |
  |---|---|
  | **Helmet and vest on entry** | zone = Press area. Required items: Helmet and Safety vest. **When it is checked:** "At the moment of entry" and also "At a PPE checkpoint beforehand" (pick Checkpoint and set **Valid for**). |
  | **Keep helmet and vest on while inside** | zone = Press area, grace period 5 s |
  | **Longest stay** | Longest stay 60 s, warn at 0.8 |
  | **Leave through the exit zone** | Exit zone = Exit door |
  | **People in the zone (lone worker, crowding)** | at least 2, or at most 3 |
  | **Person lying down** | zone = any |
  | **Walk, do not run** | zone = any |

- [ ] For each rule, check:
  - **Severity**: Info, Warning or Critical;
  - **Least evidence to make a claim** slider;
  - **This rule is on** toggle.
- [ ] Choose a rule type that needs a zone type you have not drawn.
  - **Expect:** a notice says to draw that zone first.
- [ ] **Edit** a rule, change its severity, and save.
  - **Expect:** the list updates.
- [ ] **Delete** a rule.
  - **Expect:** "Delete this rule?" appears. **Keep it** cancels; **Yes, delete** removes it.
- [ ] A rule the engine does not act on yet shows "Saved, but the safety engine does not act on this kind of rule yet."
  - That message is expected, not a bug.

### 6.4 Live feed: upload a video
- [ ] Open **Live feed**. Click **Upload Video** and pick `sample_videos/ppe_demo.mp4`.
  - **Expect:**
    - the status shows "Uploading…", then the detection model starts;
    - the video plays with pose skeletons, PPE boxes and zone outlines drawn on top.
- [ ] Use **Pause** / **Play** and the **seek bar**.
  - **Expect:** playback follows your clicks.
- [ ] Watch the side panels.
  - **Expect:**
    - the **PPE status** and **zone activity** panels update;
    - alert banners pop up for flags;
    - you can close a banner with its **×**.
- [ ] When processing finishes, upload a second video: `ppe_demo_gaps.mp4` or `ppe_demo_construction.mp4`.

### 6.5 Live feed: webcam and phone (optional)
- [ ] **Turn On Camera** (laptop webcam). Allow camera access.
  - **Expect:** a live picture with detections.
- [ ] Click **Stop Camera**.
  - **Expect:** the camera light goes off.
- [ ] Phone (HTTPS mode only):
  - click **Use my phone as a camera** → **Turn on phone camera**;
  - scan the QR code or open the link on the phone;
  - press start on the phone.
  - **Expect:**
    - the laptop shows "Phone paired" and the phone's video;
    - **New code** makes a new pairing code;
    - **Stop** ends the pairing.

---

## 7. Overview, Review, Events

### 7.1 Overview
- [ ] Open **Overview**.
  - **Expect:**
    - headline numbers (decisions, flags waiting, compliance);
    - the **Open alerts** panel;
    - "What needs a person first, then how the site did."
- [ ] Click **Open the Review queue** in the alerts panel.
  - **Expect:** you jump to Review.
- [ ] Change the date **range picker**.
  - **Expect:** the numbers and charts change to match.

### 7.2 Review queue
- [ ] Open **Review**.
  - **Expect:** flagged decisions, oldest first. If there are none: "Nothing to review. New flags will appear here."
  - The sidebar badge shows the waiting count.
- [ ] Open a flag. Check the detail page:
  - [ ] **evidence picture**: the face is blurred; only admins and managers can open it;
  - [ ] **What was measured**: helmet and vest confidence per item;
  - [ ] **The limits it was judged by**;
  - [ ] **This worker around that moment**: a timeline of zone visits.
- [ ] **Your review**: click **Confirm: it was a real violation**, add an optional reason, and submit.
  - **Expect:** the flag leaves the queue and the badge count drops.
- [ ] Open another flag. Click **Dismiss: it was not** and submit.
- [ ] Submit without choosing either.
  - **Expect:** "Choose Confirm or Dismiss."
- [ ] On a confirmed flag, click **Create a corrective action**. Fill in:
  - **What will be done**;
  - **Details**;
  - **Assigned to**;
  - **Due**;
  - **Priority**.
  - Then save.
- [ ] Open an **UNVERIFIABLE** decision.
  - **Expect:** "The system could not tell, and did not guess."

### 7.3 Events
- [ ] Open **Events**. Use **What to list** to switch between decisions and zone activity.
- [ ] Filter by zone (**Any zone** → a named zone), status and date.
  - **Expect:** the list narrows. Impossible filters show "Nothing matches these filters."
- [ ] Use **Previous** / **Next**.
  - **Expect:** you move between pages.
- [ ] Click a decision row.
  - **Expect:** the same detail view as in Review opens.

---

## 8. Actions

- [ ] Open **Actions**.
  - **Expect:** the action from 7.2 is listed with its assignee, due date and priority.
- [ ] Use the **Which actions** filter (open, overdue, done…).
- [ ] Move one action through its states:
  - **Start**;
  - **Close** (marks it done);
  - **Reopen**;
  - **Back to open**;
  - **Cancel the action**.
  - **Expect:** each change sticks after a reload.
- [ ] Set a due date in the past.
  - **Expect:** it shows as overdue.

---

## 9. Reports

- [ ] Open **Reports** and set the **Period** (or **Choose days**).
- [ ] Check every section has numbers and charts:
  - [ ] At a glance;
  - [ ] When: by day, and by hour of the day;
  - [ ] By camera, by zone and by rule;
  - [ ] Helmet and vest;
  - [ ] What happened to the flags (confirmed, dismissed, waiting for review);
  - [ ] Why reviewers decided as they did;
  - [ ] Time inside restricted zones;
  - [ ] Man-down and head count;
  - [ ] Corrective actions.
- [ ] **Download** the PDF.
  - **Expect:** the file opens with the same numbers as the screen.
- [ ] **Download** the CSV.
  - **Expect:** it opens in Excel.
- [ ] **Audit trail for a shift**: pick a shift and download.
  - **Expect:** every decision and zone event, in time order.
- [ ] **Reports made before**.
  - **Expect:** your downloads are listed. Click one: it is rebuilt from the data and downloads again.

---

## 10. Evaluation

- [ ] Open **Evaluation**. Read **How to read this page**.
- [ ] Check the headline stats:
  - frames a second;
  - flags raised;
  - could not verify;
  - judged by a person;
  - people agreed.
- [ ] **The human review loop (your organisation)**.
  - **Expect:** the numbers reflect the confirms and dismisses you did in 7.2.
- [ ] **Scenarios through the real engine**, **The whole chain on real video** and the helmet/vest benchmark.
  - **Expect:** results, or a clear "has not been run yet" note naming the script to run.
- [ ] Open each **More info** expander.

---

## 11. Plan

- [ ] Open **Plan**.
  - **Expect:**
    - "Your current plan";
    - usage bars for Cameras, Sites, Members and History;
    - the billing-period progress bar.
- [ ] Switch the billing period and pick another plan. Then:
  - **Confirm** the switch;
  - **Expect:** the plan changes and the limits update.
  - Try **Cancel** too.
- [ ] Go down to Starter while using more than Starter allows.
  - **Expect:** a clear warning about the limit.
- [ ] **Limits** (Growth: 10 cameras, 5 sites, 25 members, 90 days of history; Starter: 1, 1, 3, 7):
  - **Cameras** screen → add cameras until the limit. **Expect:** the next one is refused with "The Growth plan allows 10 cameras…".
  - **Cameras** screen → **Sites** panel → **Add site** until the limit. **Expect:** the next one is refused the same way. With more than one site, **Add a camera** shows a **Site** picker.
  - **Members** → invite until the limit. **Expect:** the next invitation is refused. Suspend a member, fill the seat, then **Restore access**. **Expect:** also refused.
- [ ] **Scheduled PDF reports** (Growth and Enterprise only): **Reports** → **Scheduled reports** → **Weekly**.
  - **Expect:** "Scheduled. The first report is made on …" and a "Next report" time.
  - On Starter, the panel says it is part of Growth and Enterprise instead.
  - When one is made it appears under **Reports made before** with a red **Scheduled** tag; **Download** rebuilds it as a PDF.

### 11.1 Expiry and payment reminders
A term lasts a month or a year from the day it starts. You can't wait a month, so move the end date with one of these from `frontend/` (replace the organisation name):

```bash
node scripts/sql.mjs "update organisations set \"planRenewsAt\" = now() + interval '3 days' where name = 'Test Plant Ltd'"
```
```bash
node scripts/sql.mjs "update organisations set \"planRenewsAt\" = now() - interval '1 minute' where name = 'Test Plant Ltd'"
```

- [ ] **3 days left** (first command), then reload the dashboard.
  - **Expect:** an amber banner on every screen: "Your Growth plan ends in 3 days, on …". **Renew now** opens **Plan**. The **×** hides it for this browser session.
  - On **Plan**, the renewal box offers **Month / Year** and **Renew for …**. Renewing adds the new term on to the old end date.
- [ ] **Expired** (second command), then reload.
  - **Expect:** a red banner that can't be closed: "Your Growth plan ended on … Until it is renewed you are on Starter limits…".
  - Plan screen shows **Term ended**, and the allowances drop to Starter's (1 / 1 / 3 / 7 days).
  - Nothing is removed, but adding a camera, site or member over Starter's limit is refused, and **Scheduled reports** says they are paused.
- [ ] **Renew** from Plan while expired.
  - **Expect:** "Renewed. Your plan now runs until …" (a new term from today), the banner disappears and the Growth limits come back.
- [ ] More than 7 days before the end, Plan says "Renewal opens on …" instead of a renew button.
- [ ] As a **Manager** or **Viewer**: the banner says "Ask an administrator to renew it." and has no button.

---

## 12. Members (needs the second email)

- [ ] **12.1** Open **Members** (`/settings/members`). Click **Invite someone**:
  - **Work email** = your second address;
  - **Role** = **Manager**.
  - **Expect:** an **invitation link** appears with "This link is shown once".
- [ ] **12.2** Copy the link, then close the box with **×**.
- [ ] **12.3** In a **private window**, open the link and sign up as account B with the invited email.
  - **Expect:** Home shows the invitation, with **Accept** and **Decline**.
- [ ] **12.4** Click **Accept**.
  - **Expect:** "Joining…", then the dashboard of `Test Plant Ltd`.
- [ ] **12.5** As B (Manager), check what B can do:
  - [ ] Review: can confirm and dismiss;
  - [ ] Organisation settings: read-only;
  - [ ] Plan: hidden or view-only.
- [ ] **12.6** Back as A, use the menu on B's row:
  - change **Role** to Viewer;
  - **Suspend access**;
  - **Restore access**.
  - **Expect:** as Viewer, B sees **VIEW ONLY** and no edit buttons (refresh B's window to check).
- [ ] **12.7** Invite a third email, then click **Withdraw invitation**.
  - **Expect:** the link stops working.
- [ ] **12.8** Try to demote or remove yourself while you are the only admin.
  - **Expect:** it is blocked, because the organisation must keep one admin.
- [ ] **12.9** Make B an Admin. Then **Remove from organisation** B (or decline as B on a fresh invite).

---

## 13. Organisation settings

- [ ] Open **Organisation** (`/settings/organisation`).
- [ ] **Identity**: change the **Organisation name** and save.
  - **Expect:** the sidebar shows the new name.
- [ ] Change the **Timezone**.
- [ ] **Privacy & retention**: change **Keep records for** to a shorter time.
  - **Expect:** a "Yes, shorten retention" confirmation appears first.
- [ ] Toggle **Delete uploaded video after processing**.
- [ ] Click **Back to dashboard**.
- [ ] **Delete this organisation**: do this **last** (step 15), since it ends the test.

---

## 14. Sign out and sign back in

- [ ] Account menu → **Sign out**.
  - **Expect:** you are back on the landing or sign-in page.
- [ ] Open `http://localhost:3000/dashboard` directly while signed out.
  - **Expect:** you are sent to sign in. After signing in you come back to the dashboard.
- [ ] Sign in again with account A.
  - **Expect:** you land straight on the dashboard, and all data is still there.

## 15. Clean finish (optional)

- [ ] Organisation settings → **Delete organisation** → confirm.
  - **Expect:** you go back to Home/onboarding, and the organisation and its data are gone.

---

## Platform operator console (optional)

`/platform` is for the operator who runs VisionWorks itself, not for customer organisations. After the wipe, nobody has operator access. If you want to test it, tell Claude which account to make an operator. Then check:
- [ ] **Overview**: stats, signups chart, recent signups.
- [ ] **Organisations**: filters, table, open one, and its actions.
- [ ] **Health**.
- [ ] **Audit**: filters.
- [ ] **Operators**: list, revoke.

---

## Bug note template

```
Step:        (e.g. 7.2)
Screen:      (e.g. Review → flag detail)
Theme/size:  (light/dark, window width)
What I did:
What I expected:
What happened:
Screenshot:  (attach)
```
