# Task digest — from Simplenote

What this is: a daily morning digest of your most important tasks, and one stable
reference list of every open task ordered by importance. Tasks come from your
Simplenote notes. A scheduled job refreshes the notes, rebuilds the digest, and
shows a notification on this laptop at 7am.

## The pipeline

The scheduled job runs `run_daily.ps1`, which does these steps in order.

1. `gate.py` proves nothing here can write to Simplenote. If it fails, the run stops.
2. `get_token.py` reads the Simplenote desktop app token from local storage.
3. `sync_notes.py` pulls notes from the Simperium cloud into `notes.db` and
   lists what changed in `pending_changes.json`.
4. `dump_notes.py` copies the changed notes to `pending_notes.json`.
5. `opencode run` reads `extract_prompt.md` and writes `tasks_new.json`.
6. `merge_pending.py` adds those tasks to `tasks.json`, dropping duplicates.
7. `tracker.py` scores, ranks, and renders `digest-YYYY-MM-DD.md` and `list.md`.
8. `notify.ps1` shows the top tasks as a Windows toast with the run status.

If step 5 fails, the toast says how many notes need extraction and the rest of
the run continues, so you always get yesterday's digest plus a clear signal.

## The console

`python console.py` opens a local page at `http://127.0.0.1:8765/` with five
tabs: tasks, digest, advisory, settings, and log. Use it to search and filter
tasks, change status, tier, impact or category, switch settings, rebuild the
digest, and start the morning pipeline by hand. It refuses to unlock read only.

The tasks tab also chooses the day's focus. Tick tier 1 tasks and press Use
checked, press Shuffle tier 1 for a random pick, or press Automatic to rank by
score. The choice is stored in `today.json` and the digest and sticky note
follow it. The automatic list is frozen at the first digest of the day, so
finishing a task does not shuffle the list under your fingers.

The advisory tab shows every completion marker found in your notes, with the
note line as proof. It also shows the findings of the state audit: a full pass
where an agent rereads every note against the tasks extracted from it and
proposes state changes with a quoted evidence line. Each row carries a source
pill (marker or audit) and a confidence. Apply sets the suggested state. Rescan
rebuilds the marker report; the audit is rebuilt by hand. Everything stays
advisory: nothing changes until you press Apply.

## The sticky note

`pythonw sticky.py` opens a borderless yellow note with the same picks as the
digest. Tick a task to mark it done; the line stays for the day with a strike
through it. Right-click a task to mark it started or open. Drag the top strip
to move the note, drag the bottom right corner to resize, and use the small
top links for shuffle, console, pin, and close. Position and size are saved in
`sticky.json`. It refreshes itself every minute.

## Start at login

The scheduled task `TaskDigest Login` runs `login_start.ps1` at logon. It waits
for the internet, so a login before the network is ready does not lose the
morning run. If no run succeeded today, it runs one, pushes any waiting
commits, then starts the console and the sticky note.

## Categories

Every task carries one category, and the category sets a tier and an impact
floor. A category only ever raises priority. It never demotes a task, and it
skips tasks already marked stale. The labels are incoming-promised, client-job,
money-machine, blocks-work, health-safety, legal-account, outgoing-due,
incoming-window, promise, to-save, new-opportunity, and none. The full table is
in `categories.py` and in `batches/EXTRACT.md`.

When `TYPESAFE_API_KEY` is set, `merge_pending.py` asks Jev (Typesafe System
One, a cheap hosted classifier) to label each new task that arrived as none, so
new notes are categorised without an agent run. `python jev_categorize.py
check [N]` compares Jev against the current labels on a sample and writes
`jev-check.json`. The first sample agreed 40 percent: Jev is more conservative
and says none where the agent saw an income asset, so the existing labels were
left alone and Jev only handles new tasks.

## Settings

`settings.json` holds every switch. Change one with:

```
python tracker.py config                  # show all
python tracker.py config use_deadlines true
python tracker.py config auto_extract false
```

- `read_only` must stay true. `gate.py` fails the run if it is not.
- `use_deadlines` turns on date-based urgency. Off means age drives urgency.
- `auto_extract` turns the headless extraction step on or off.
- `notify_top` sets how many tasks the toast shows.

## Commands

The scheduled task runs this every morning. You can also run it by hand:

```
pwsh -NoProfile -ExecutionPolicy Bypass -File run_daily.ps1
```

Run only one step:

```
.venv\Scripts\python.exe sync_notes.py    # incremental note sync
python tracker.py digest                  # rebuild digest and list
python tracker.py rank                    # print ranked open tasks
python tracker.py shuffle --n 7           # random tier 1 focus for today
python tracker.py pick b0123 b0456        # choose the focus by id
python tracker.py auto                    # focus back to the score order
python console.py                         # the console
pythonw sticky.py                         # the sticky note
python scan_markers.py                    # rebuild the advisory report
```

Backfill tools, run only when notes were re-batched:

```
.venv\Scripts\python.exe batch_notes.py   # split unmined notes into batches
.venv\Scripts\python.exe merge_out.py     # merge subagent output into tasks.json
.venv\Scripts\python.exe demote_stale.py  # push untouched old tasks down a tier
.venv\Scripts\python.exe inspect_out.py   # check a batch output file
.venv\Scripts\python.exe categorize_dump.py   # split tasks for classification
.venv\Scripts\python.exe categorize_apply.py  # apply category labels
.venv\Scripts\python.exe audit_dump.py        # split all notes for the state audit
.venv\Scripts\python.exe audit_apply.py       # merge audit batch output
```

## Files

- `tasks.json` — the single reference list. The merge, console, and sticky note
  edit it; the sync scripts never do.
- `notes.db` — SQLite copy of all notes.
- `pending_changes.json` — notes added, updated, or deleted in the last sync.
- `digest-YYYY-MM-DD.md` — one file per morning.
- `list.md` — every open task, most important first.
- `batches/` — backfill working files. `EXTRACT.md` holds the extraction rules.
- `categories.py` — one table of categories, tiers and impact floors.
- `console.py` — the local graphical console.
- `sticky.py` — the small desktop paper note. It is not always on top by
  default; the pin link raises it above other windows when you want that.
- `today.json` — today's focus choice: automatic, shuffled, or picked.
- `marker-report.md` and `marker-report.json` — the marker scan report.
- `audit_prompt.md`, `audit_dump.py`, `audit_apply.py`, `audit-report.json` —
  the full note-by-note state audit: dump notes with their tasks, run one agent
  per batch against the prompt, then merge. Output is advisory, like markers.
- `logs/run_daily.log` — morning run log.
- `.token` — your Simplenote access token. Do not share this file.

## How scoring works

The score comes from the prioritization engine spec (specs 01 and 03).
Tier weight is 100, 50, 25, or 12 for P1, P2, P3, P4. A deadline adds urgency,
otherwise age adds urgency up to 0.5. Impact adds 0 to 0.5. Higher score ranks
first. Ties go to the oldest task, so new tasks do not leapfrog older important
ones. Any open task older than 45 days drops one tier, because it was not acted
on. Change a task's `tier` or `impact` in `tasks.json` to move it.

## Key facts

- Simplenote cloud API base: `https://api.simperium.com/1/chalk-bump-f49`
- Auth header: `X-Simperium-Token`. The token rotates, so `get_token.py` runs first.
- The desktop app is a Microsoft Store app, package
  `22490Automattic.Simplenote_9h07f78gwnchp`.
- `api.simplenote.com` no longer exists. The Simperium API is the live path.
- Scheduled task name: `TaskDigest Morning`, daily at 07:00, start when
  available if the laptop was asleep.
- Scheduled task name: `TaskDigest Login`, at logon, waits for the internet.
- The morning run commits `tasks.json`, `list.md`, the digest, the reports and
  `today.json`, then pushes. If offline, it commits only and the login task
  retries the push.
- Hermes has its own cron (`%LOCALAPPDATA%\hermes\cron\jobs.json`) with Telegram
  delivery. A Hermes job can send this digest to Telegram later.

## Not done yet

- Backfilled tiers are provisional. Review `list.md` and correct the top of it.
- Backfill covers notes modified since 2026-03-01. Older notes are not mined.
- Deduplication compares titles by words, so rare phrasings can slip through.
- `scan_markers.py` is advisory only. It lists DONE and WORKING lines for a
  human to check, because pattern matching reads "when i am done" as DONE.
- Categories were assigned by a language model in eight batches. Spot check
  `list.md` and correct any label in the console.