# DUO-WARE 2 — Prompts

## Setup (once, before prompt 1)

1. Create the project folder `C:\Users\khusw\Downloads\duoware` (outside OneDrive).
2. Copy `PROJECT_BRIEF.md` into it as `docs/PROJECT_BRIEF.md`.
3. Create an empty GitHub repository for it (no README), so the first milestone can be pushed.
4. Check the Android tools from a terminal: `adb devices` lists your phone, and `java -version` works.
   If `adb` isn't found, add `%LOCALAPPDATA%\Android\Sdk\platform-tools` to PATH.
5. Open a Claude Code session in the new folder with the model and effort from the table below, and paste prompt 1.

## Model and effort per prompt

Set both before sending the prompt. Start a new session for each prompt.

| Prompt | Model | Effort |
| --- | --- | --- |
| 1 — architecture and M0 | Opus 5.5 | high |
| 2 — implement M1, M2, M4, M7, M8 | Sonnet 5.5 | medium |
| 2 — implement M3 (Android app), M5 (single-car motion), M6 (two cars, reservations, deadlock) | Sonnet 5.5 | high |
| 3 — review a milestone, plan the next | Opus 5.5 | high |
| 4 — hardware problem | Opus 5.5 | high; xhigh if the first attempt didn't find the cause |
| Small change to one or two files | Sonnet 5.5 | low or medium |

If a Sonnet session gets stuck on the same failure twice, stop it and hand the problem to Opus with prompt 4
rather than raising Sonnet's effort further.

---

## Prompt 1 — Opus: architecture and M0 (paste once)

```
You are the architect for DUO-WARE 2, a rebuild of an older project. Read docs/PROJECT_BRIEF.md fully.
The old project is at C:\Users\khusw\Downloads\Duo_Ware\Duo_Ware (read its context/context.md and the
files listed in the brief's section 6 as reference; do not modify it).

Your job in this session is milestone M0 only: design, not features.

1. Review the brief critically first. Tell me where you disagree or see risk (especially camera-only
   motion control without a gyro, the Uno + HC-05 car, and the latency budget), and ask me the open
   hardware questions in section 3. Wait for my answers before writing files.
2. Then create:
   - The repository layout (android/, server/, dashboard/, firmware/car_esp32/, firmware/car_uno/,
     sim/ or inside server/, docs/, config/), with a README that says how to run each part.
   - CLAUDE.md: architecture map, conventions, how to run every test suite, the working rules from
     brief section 8, and the rule that Sonnet implements from docs/plans/M<n>.md.
   - PROTOCOL.md v1: the phone→server UDP packet, clock sync, discovery beacon, phone WebSocket
     messages, the car line protocol, and the server→dashboard WebSocket state. Exact field names,
     types, units, rates and error handling.
   - DECISIONS.md: each decision with its reason and the alternatives rejected.
   - config/ files for cars, map and tuning, with comments. Tag roles are NOT config: the admin assigns
     them on the dashboard (brief section 4.5); design the tag registry, its storage and its API.
   - Minimal skeletons that run: a server that starts and serves a health endpoint, an empty test suite
     that passes, a dashboard that builds, an Android project that builds (empty screen).
   - docs/plans/M1.md: a plan Sonnet can implement without asking questions: files to create,
     interfaces, test cases, and acceptance criteria. Also write a short outline of M2–M8.
3. Initialise git, commit, and push to the GitHub remote I give you.

Keep the design easy to change: one source of truth per concern, config over code, small modules.
Do not implement M1 yourself.
```

---

## Prompt 2 — Sonnet: implement a milestone (reuse for M1, M2, …)

Open a session in the project folder with **Sonnet 5.5** selected. Change `M1` to the milestone number.

```
Implement docs/plans/M1.md.

Before coding, read CLAUDE.md, PROTOCOL.md, DECISIONS.md and the plan. If anything in the plan is
ambiguous, contradicts PROTOCOL.md, or looks wrong, stop and tell me instead of guessing.

Rules:
- Do not change PROTOCOL.md or anything in DECISIONS.md. If you think a change is needed, write it
  under "Proposed changes" in the plan file and continue with the plan as written.
- Write tests for every acceptance criterion and run the full test suite (server, dashboard build,
  Android build if touched). All must pass before you say you're done.
- Nothing is "verified on hardware" unless I ran it. For steps that need hardware, write a short
  step-by-step test for me under "Hardware test" in the plan, with what to measure.
- Commit after each working step with clear messages. Do not push unless I ask.
- At the end, fill in the plan's "Results" section: what was built, test results, known gaps.
```

For Android work, add: `Build with the Gradle wrapper and install with adb on my connected phone.`

---

## Prompt 3 — Opus: review a milestone and plan the next one

Open a session with **Opus 5.5** selected after Sonnet finishes (and after you've run the hardware test).

```
Review milestone M1 as implemented. Read CLAUDE.md, PROTOCOL.md, docs/plans/M1.md (including
Results and Proposed changes) and the diff since the M1 plan was committed.

1. Check the code against the plan and the protocol. Look for correctness bugs, safety gaps (stops,
   E-stop, stale data, link loss), and anything that will make later milestones harder. Run the tests.
2. Decide on each "Proposed change": accept (update PROTOCOL.md / DECISIONS.md) or reject (say why).
3. Here are my hardware test results: <paste numbers, or "none yet">. Record them in
   docs/HARDWARE_LOG.md and say whether they meet the acceptance criteria.
4. List the fixes needed, then write docs/plans/M2.md in the same style as M1.
Don't implement the next milestone.
```

---

## Prompt 4 — Opus: a hardware problem

```
On the real hardware, <describe what happened: which car, what you did, what you saw>.
Here is the event log / server output from that run: <paste or give the file path>.
Find the cause from the evidence before proposing changes. Tell me what to measure if the log isn't
enough. If a fix is needed, write it as a small plan in docs/plans/ for Sonnet, or fix it directly if
it's under ~30 lines, and add a test that would have caught it.
```

---

## Changing something later

Tell Opus what you want changed and ask it to update the affected docs (PROTOCOL.md, DECISIONS.md,
config) and write a small plan in `docs/plans/`. Then give that plan to Sonnet with prompt 2.
Small changes that touch only one file can go straight to Sonnet.
