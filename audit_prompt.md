# State audit of extracted tasks

You audit whether the CURRENT STATE of already-extracted tasks matches its source note. You do not create tasks. You do not edit any file.

Input file (one batch): `audit_in_NN.json`. It is an array of notes: `id`, `date`, `truncated`, `tasks` (id, title, status, tier, category), `content` (note text, clipped at 12000 chars).

For every task listed under a note, compare the note text against its current status. Propose a change ONLY when the note itself shows, in context, that the task is started, done or partial:

- suggested `done`: the note says the task is finished. Markers like DONE, DONE, NOT REVIEWED, "finished", "completed", "sent", "paid", "submitted" qualify when the sentence is about this task.
- suggested `started`: the note says work is in progress or partial: WORKING, WIP, STARTED, HALF-DONE, SEMI DONE, "working on", "still on it", "part-way".
- suggested `open`: the note corrects a wrong done/started: "not done", "NOT DONE", "didn't finish", "still to start".
- If the note gives no evidence about this task, emit nothing for it.
- "when i am done with X" is a plan, not a marker; never a marker. Same for "if i finish", "once completed".
- A marker must end its clause: it is followed by punctuation or end of line, so "DONE with the first part, still working" is a partial (suggested `started`), not done.
- Do not propose `done` for the same task twice in one batch.

Evidence: quote the exact words from the note that justify the change, max 200 characters. Prefer the sentence that names this task. Confidence: `high` when the evidence sentence names the task or its clear object; `medium` when it needs inference; skip `low`.

Do not propose anything else: no tier or impact or category changes, no new tasks, no title edits.

## Output

Write `audit_out_NN.json` (NN = same number as the input batch) in the same folder, a JSON array, utf-8, no BOM. Every element:

    {"task_id": "b0123", "was": "open", "status": "done", "evidence": "the exact quoted note text", "confidence": "high"}

- `task_id` must come from the input.
- `was` must be the current status shown in the input.
- `status` is the one you propose: open, started or done.
- Empty array if there is nothing. Never invent ids.

Also write one line as your final reply: `audit batch NN: X notes, Y changes`.

Example: note text "call the bank DONE" and its task b0042 status open gives:

    {"task_id": "b0042", "was": "open", "status": "done", "evidence": "call the bank DONE", "confidence": "high"}

Only evidence that is really in the note, never invented.

You are a subagent. Do not run memo. Do not edit tasks.json.
