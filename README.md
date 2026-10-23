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

`python console.py` opens a local page at `http://127.0.0.1:8765/` with six
tabs: tasks, digest, advisory, lapsed, settings, and log. Use it to search and
filter tasks, change status, tier, impact, category, a hard or soft deadline,
switch settings, rebuild the digest, and start the morning pipeline by hand. It
refuses to unlock read only. When you mark a task done, it warns if a
prerequisite is still open.

The tasks tab also chooses the day's focus. Tick tier 1 tasks and press Use
checked, press Shuffle to randomize the normal rows, or press Automatic to rank
by score. The choice is stored in `today.json` and the digest and sticky note
follow it. The automatic list is frozen at the first digest of the day, so
with refill off, finishing a task does not shuffle the list under your fingers.
Band and value rows stay in place when you shuffle.

The advisory tab shows every completion marker found in your notes, with the
note line as proof. It also shows the findings of the state audit: a full pass
where an agent rereads every note against the tasks extracted from it and
proposes state changes with a quoted evidence line. Each row carries a source
pill (marker or audit) and a confidence. Apply sets the suggested state. Rescan
rebuilds the marker report; the audit is rebuilt by hand. Everything stays
advisory: nothing changes until you press Apply.

The digest shows up to `lapse_alert_top` lapsed tasks in a short Lapsed section,
so they do not disappear without a question. The lapsed tab lists every open or
started task whose deadline is older than `lapse_limit_days`, grouped by how
the cost ages. Compounding cost is oldest
first, expiring chance is newest lapse first, and fixed loss is highest score
first. Done marks the task finished. Re-date asks for a new deadline and puts
the task back in the band. Keep gives it a new `keep_until` window and returns
it to the normal list. Drop sets the status to dropped, asks for an optional
reason, and stores it in `drop_reason`; the task leaves the focus, the digest
and the counts.

## The sticky note

`pythonw sticky.py` opens a warm paper note with the same picks as the digest.
Each task is one line, and a long title ends in an ellipsis, so shuffling never
clips or stacks rows. Band rows carry a short due chip: late, today, or the
number of days left. A lapsed filler carries a `lapsed Nd` chip instead.
Hover a line to see the full title, tier, category,
status, deadline, keep window, and any open prerequisite in a small dark card. Empty means open, a blue dash means started, and a filled tick means
done; a click moves open to started, started to done, and done back to open.
Done lines stay for the day, greyed. Right-click a line to jump straight to any
state, and on a line with a deadline, Keep 7 days exempts it from the lapsed
list for a week. Drag anywhere to move
the note, drag the bottom right corner to resize, and scroll when the note is
shorter than the list. The small top links are shuffle, console, pin, and close.
The note is not always on top: the pin link raises it above other windows, and
it lifts itself above the desktop when you show the desktop. Position and size
are saved in `sticky.json`. It refreshes itself every minute.

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

When `TYPESAFE_API_KEY` is set, or `.jev_key` next to the scripts holds the
key, `merge_pending.py` asks Jev (Typesafe System One, a cheap hosted
classifier) to label each new task that arrived as none, so
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
python tracker.py config focus_size 7
```

- `read_only` must stay true. `gate.py` fails the run if it is not.
- `use_deadlines` turns on the deadline band and date-based urgency. Off means
  age drives urgency.
- `auto_extract` turns the headless extraction step on or off.
- `notify_top` sets how many tasks the toast shows.
- `focus_size` sets how many open tasks the focus list holds (1-10).
- `value_slots` protects slots for high-value tasks with no band deadline
  (`tier` 2 or higher and `impact` 0.3 or higher).
- `band_late_days` and `band_ahead_days` set the deadline band (7-14 each).
- `refill_when_done` adds the next task below a completed row. It is off by
  default.
- `lapse_limit_days` sets when a past deadline makes a task lapsed.
  `keep_days` is the default keep window for a lapsed task.
- `routine_slots`, `lapse_alert_top`, and `lapse_alert_days` are reserved for
  the routines panel and the lapse alerts.

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
python tracker.py check                   # report bad or cyclic prerequisites
python tracker.py shuffle                 # randomize the normal focus rows
python tracker.py pick b0123 b0456        # choose the focus by id
python tracker.py auto                    # focus back to the score order
python test_rank.py                       # self-check the ranking core
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
- `categories.py` — one table of categories, tiers and impact floors, and how a
  missed deadline ages.
- `tracker.py` — scoring, rank, digest, today's picks, and dependency checks.
- `test_rank.py` — self-check for band, value, lapsed order and fillers, soft
  deadlines, refill, effort, and dependency rules.
- `console.py` — the local graphical console.
- `sticky.py` — the small desktop paper note. It is not always on top by
  default; the pin link raises it above other windows when you want that.
- `today.json` — today's focus choice: automatic, shuffled, or picked. It also
  records the band, value, routine, lapsed-filler, and auto-added task ids.
- `marker-report.md` and `marker-report.json` — the marker scan report.
- `audit_prompt.md`, `audit_dump.py`, `audit_apply.py`, `audit-report.json` —
  the full note-by-note state audit: dump notes with their tasks, run one agent
  per batch against the prompt, then merge. Output is advisory, like markers.
- `logs/run_daily.log` — morning run log.
- `.token` — your Simplenote access token. Do not share this file.
- `.jev_key` — your Jev (Typesafe) API key, used only when `TYPESAFE_API_KEY`
  is not set. Do not share this file.

## How the focus list is built

Each morning `tracker.py` ranks open tasks by score. Before it picks the focus,
it removes lapsed tasks: open or started tasks whose deadline is more than
`lapse_limit_days` old and whose `keep_until` is not in the future. It also
removes routine occurrences. The focus list then has four parts.

1. The deadline band: overdue tasks, tasks due within 3 days, tasks due within
   7 days, and tasks due within `band_ahead_days` when that is more than 7. Band
   rows rank by bucket, then score, then deadline, then age, then id.
2. Value slots: high-impact tasks with no band deadline.
3. Normal rows: the remaining tasks by score.
4. Lapsed fillers: when the live rows do not fill `focus_size`, lapsed tasks
   fill the empty slots in their own order and carry `[lapsed Nd, filler]`.
   They stay out of the main rank and `list.md`.

The list holds up to `focus_size` open tasks and never invents a task. If there
are not enough live tasks or lapsed fillers, it stays short. Shuffle keeps the
band and value rows and randomizes only the normal rows. With
`refill_when_done` on, a completed row stays visible and the next live task
appears below it; the open count stays at `focus_size`. Reopening the sticky
note removes auto-added rows.

## Effort and dependencies

A task may carry `"effort": "S"`, `"M"`, or `"L"`, and
`"prerequisites": ["b0123"]`. The extraction step sets effort; prerequisite
links are added later. When two tasks have the same score, the task that
unblocks more open tasks ranks first. Then S ranks before M, and M before L.
Unknown effort counts as M. A task with an unfinished prerequisite gets a
`[prereq]` marker in the digest and list. The console warns when you complete a
task with an unfinished prerequisite.

`python tracker.py check` lists missing prerequisite ids, self dependencies,
and cycles.

## Dates and drops

A task may carry `"deadline": "YYYY-MM-DD"` for a hard date, or
`"soft_deadline": "YYYY-MM-DD"` for a target date with no hard consequence.
The band uses only the hard deadline. Scoring uses the hard deadline first, then
the soft deadline, when `use_deadlines` is on. A dropped task may carry
`"drop_reason"` text. The console stores the reason when you drop from the
Lapsed tab.

## How scoring works

The score comes from the prioritization engine spec (specs 01 and 03).
Tier weight is 100, 50, 25, or 12 for P1, P2, P3, P4. A hard `deadline` or a
`soft_deadline` target adds urgency when `use_deadlines` is on, otherwise age
adds urgency up to 0.5. Impact adds 0 to 0.5. Higher score ranks
first. Ties go to the task that unblocks more open tasks, then to S/M/L effort,
then to the oldest task, so new tasks do not leapfrog older important ones. Any
open task older than 45 days drops one tier, because it was not acted
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
- No task has a hard or soft deadline yet, so the band and lapsed behavior are
  tested only with synthetic data.
- No task has an effort label or prerequisite link yet, so the new tiebreak and
  completion warning are tested only with synthetic data.