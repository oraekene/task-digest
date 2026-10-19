"""Local graphical console for the task digest. Standard library only.

Run:  python console.py            (opens http://127.0.0.1:8765/)
      python console.py --no-open
      python console.py --port 8770

It edits tasks.json and settings.json. It never touches Simplenote, and it
refuses to change read_only. Editing happens on this machine only.
"""
import argparse
import json
import os
import pathlib
import random
import subprocess
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import categories
import tracker

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
SETTINGS = HERE / "settings.json"
LOG = HERE / "logs" / "run_daily.log"
EDITABLE = {"tier", "impact", "status", "category"}
STATUSES = ("open", "started", "done")
SETTING_KEYS = {"use_deadlines", "auto_extract", "notify_top"}
LOCK = threading.Lock()


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def tail(path, lines=100):
    if not path.exists():
        return "no log yet"
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(text[-lines:])


def latest_digest():
    files = sorted(HERE.glob("digest-*.md"))
    return files[-1].read_text(encoding="utf-8") if files else "No digest yet."


def state():
    tasks = read_json(TASKS, {"tasks": []})["tasks"]
    ranked = tracker.ranked_open({"tasks": tasks})
    advisory = read_json(HERE / "marker-report.json", [])
    return {
        "tasks": ranked,
        "counts": {
            "open": sum(1 for t in tasks if t.get("status") == "open"),
            "started": sum(1 for t in tasks if t.get("status") == "started"),
            "done": sum(1 for t in tasks if t.get("status") == "done"),
        },
        "settings": read_json(SETTINGS, {}),
        "categories": list(categories.CATEGORY_TIERS),
        "today": tracker.load_today(),
        "advisory": advisory,
        "digest": latest_digest(),
        "log": tail(LOG, 60),
    }


def rebuild_digest():
    return subprocess.run(
        [sys.executable, "tracker.py", "digest"],
        cwd=HERE,
        capture_output=True,
        text=True,
    )


def choose_today(ids):
    open_ids = {t["id"] for t in tracker.ranked_open(read_json(TASKS, {"tasks": []}))}
    ids = [i for i in ids if i in open_ids]
    if not ids:
        raise ValueError("no open tasks selected")
    tracker.save_today({"date": date.today().isoformat(), "mode": "manual", "ids": ids})
    rebuild_digest()
    return state()


def shuffle_today(n=7):
    ranked = tracker.ranked_open(read_json(TASKS, {"tasks": []}))
    tier_one = [t for t in ranked if t.get("tier") == 1]
    pool = tier_one if len(tier_one) >= n else ranked
    picks = random.sample(pool, min(n, len(pool)))
    tracker.save_today({"date": date.today().isoformat(), "mode": "shuffle", "ids": [t["id"] for t in picks]})
    rebuild_digest()
    return state()


def auto_today():
    tracker.save_today({"date": date.today().isoformat(), "mode": "auto", "ids": []})
    rebuild_digest()
    return state()


def update_task(body):
    field, value, task_id = body.get("field"), body.get("value"), body.get("id")
    if field not in EDITABLE:
        raise ValueError(f"cannot edit {field}")
    if field == "tier" and int(value) not in (1, 2, 3, 4):
        raise ValueError("tier must be 1 to 4")
    if field == "impact" and float(value) not in (0, 0.1, 0.2, 0.3, 0.4, 0.5):
        raise ValueError("impact must be 0 to 0.5 in tenths")
    if field == "status" and value not in STATUSES:
        raise ValueError("bad status")
    if field == "category" and value not in categories.CATEGORY_TIERS:
        raise ValueError("bad category")
    data = read_json(TASKS, {"tasks": []})
    task = next((t for t in data["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise ValueError("no such task")
    if field == "tier":
        task["tier"] = int(value)
    elif field == "impact":
        task["impact"] = float(value)
    elif field == "status":
        task["status"] = value
        if value == "done":
            task.setdefault("completed", date.today().isoformat())
            task["completed"] = task["completed"] or date.today().isoformat()
        else:
            task["completed"] = None
    else:
        task["category"] = value
    write_json(TASKS, data)
    return task


def update_settings(body):
    key, value = body.get("key"), body.get("value")
    if key == "read_only":
        raise ValueError("read_only is locked")
    if key not in SETTING_KEYS:
        raise ValueError(f"cannot edit {key}")
    data = read_json(SETTINGS, {})
    if key == "notify_top":
        value = max(1, min(int(value), 20))
    elif isinstance(data.get(key), bool) or key in ("use_deadlines", "auto_extract"):
        value = bool(value)
    data[key] = value
    write_json(SETTINGS, data)
    return data


def render_digest():
    result = subprocess.run(
        [sys.executable, "tracker.py", "digest"],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    return {"ok": result.returncode == 0, "output": (result.stdout + result.stderr).strip(), "digest": latest_digest()}


def start_run():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen(
        ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "run_daily.ps1")],
        cwd=HERE,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"started": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send(200, PAGE, "text/html")
        elif self.path == "/api/state":
            self.send(200, state())
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            with LOCK:
                if self.path == "/api/task":
                    self.send(200, update_task(body))
                elif self.path == "/api/settings":
                    self.send(200, update_settings(body))
                elif self.path == "/api/digest":
                    self.send(200, render_digest())
                elif self.path == "/api/today":
                    if body.get("mode") == "auto":
                        self.send(200, auto_today())
                    else:
                        self.send(200, choose_today(body.get("ids") or []))
                elif self.path == "/api/shuffle":
                    self.send(200, shuffle_today(int(body.get("n") or 7)))
                elif self.path == "/api/scan":
                    subprocess.run(
                        [sys.executable, "scan_markers.py"],
                        cwd=HERE,
                        capture_output=True,
                    )
                    self.send(200, state())
                elif self.path == "/api/run":
                    self.send(200, start_run())
                else:
                    self.send(404, {"error": "not found"})
        except Exception as exc:  # report the reason, never crash the console
            self.send(400, {"error": str(exc)})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Task console</title>
<style>
  :root { color-scheme: light dark; --line: #8884; --accent: #2f6fed; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 system-ui, sans-serif; }
  header { display: flex; gap: 8px; align-items: center; padding: 10px 16px;
           border-bottom: 1px solid var(--line); position: sticky; top: 0;
           background: Canvas; z-index: 2; flex-wrap: wrap; }
  nav button { border: 1px solid var(--line); background: transparent; padding: 6px 12px;
               border-radius: 6px; cursor: pointer; font: inherit; }
  nav button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  main { padding: 16px; max-width: 1500px; margin: 0 auto; }
  #bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; align-items: center; }
  input[type=search], select, input[type=number] { font: inherit; padding: 5px 7px;
               border: 1px solid var(--line); border-radius: 6px; background: Canvas; color: CanvasText; }
  input[type=search] { min-width: 280px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
  th { position: sticky; top: 57px; background: Canvas; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  td.num { color: #888; width: 46px; font-variant-numeric: tabular-nums; }
  td.title { min-width: 380px; }
  td.why { color: #888; font-size: 12px; max-width: 320px; }
  td select { width: 100%; }
  tr.done td.title { text-decoration: line-through; opacity: .55; }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--line); font-size: 12px; }
  pre { white-space: pre-wrap; background: #8881; padding: 14px; border-radius: 8px; }
  #toast { position: fixed; right: 16px; bottom: 16px; background: #222; color: #fff;
           padding: 8px 14px; border-radius: 8px; opacity: 0; transition: opacity .2s; pointer-events: none; }
  #toast.show { opacity: 1; }
  .row { display: flex; gap: 12px; align-items: center; margin: 10px 0; }
  .hide { display: none; }
</style></head><body>
<header>
  <strong>Task console</strong>
  <nav id="tabs">
    <button data-tab="tasks" class="active">Tasks</button>
    <button data-tab="digest">Digest</button>
    <button data-tab="advisory">Advisory</button>
    <button data-tab="settings">Settings</button>
    <button data-tab="log">Log</button>
  </nav>
  <span id="counts" style="margin-left:auto;color:#888"></span>
</header>
<main>
  <section id="tasks">
    <div id="bar">
      <input id="search" type="search" placeholder="Search title or reason">
      <select id="f-status"><option value="">Any status</option><option>open</option><option>started</option><option>done</option></select>
      <select id="f-tier"><option value="">Any tier</option><option value="1">Tier 1</option><option value="2">Tier 2</option><option value="3">Tier 3</option><option value="4">Tier 4</option></select>
      <select id="f-cat"><option value="">Any category</option></select>
      <span id="shown" style="color:#888"></span>
      <span id="mode" style="color:#888"></span>
      <button id="shuffle" title="Pick today's focus at random from tier 1">Shuffle tier 1</button>
      <button id="use-picked" title="Use the ticked tier 1 tasks for today">Use checked</button>
      <button id="auto-pick" title="Back to the top ranked tasks">Automatic</button>
      <button id="refresh">Refresh</button>
    </div>
    <table>
      <thead><tr><th>#</th><th>Pick</th><th>Task</th><th>Status</th><th>Tier</th><th>Impact</th><th>Category</th><th>Since</th><th>Why</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </section>
  <section id="digest" class="hide">
    <div class="row"><button id="rebuild">Rebuild digest</button><span id="build-msg" style="color:#888"></span></div>
    <pre id="digest-text"></pre>
  </section>
  <section id="advisory" class="hide">
    <div class="row">
      <button id="rescan">Rescan notes</button>
      <label><input type="checkbox" id="only-diff"> Only where the suggestion differs</label>
      <span id="adv-count" style="color:#888"></span>
    </div>
    <table>
      <thead><tr><th>Task</th><th>Note says</th><th>Suggestion</th><th>Current</th><th></th></tr></thead>
      <tbody id="adv-rows"></tbody>
    </table>
  </section>
  <section id="settings" class="hide">
    <div class="row"><label><input type="checkbox" id="s-deadlines"> Use deadlines for urgency</label></div>
    <div class="row"><label><input type="checkbox" id="s-extract"> Extract new notes automatically each morning</label></div>
    <div class="row"><label>Tasks in the toast: <input type="number" id="s-top" min="1" max="20"></label></div>
    <div class="row"><span class="pill">read only: locked on</span> The gate stops the morning run if this is ever false.</div>
    <div class="row"><button id="save-settings">Save settings</button></div>
    <hr>
    <div class="row"><button id="run-now">Run the morning pipeline now</button><span id="run-msg" style="color:#888"></span></div>
  </section>
  <section id="log" class="hide">
    <div class="row"><button id="refresh-log">Refresh</button><span style="color:#888">last 60 lines</span></div>
    <pre id="log-text"></pre>
  </section>
</main>
<div id="toast"></div>
<script>
const $ = (s) => document.querySelector(s);
let state = null, activeTab = "tasks";
const applied = new Set();

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 1600);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? undefined : {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "request failed");
  return data;
}

function options(values, current) {
  return values.map(v => `<option value="${v}" ${String(v) === String(current) ? "selected" : ""}>${v}</option>`).join("");
}

function matched(t) {
  const q = $("#search").value.trim().toLowerCase();
  if (q && !(t.title + " " + (t.why || "") + " " + (t.source_title || "")).toLowerCase().includes(q)) return false;
  if ($("#f-status").value && t.status !== $("#f-status").value) return false;
  if ($("#f-tier").value && String(t.tier) !== $("#f-tier").value) return false;
  if ($("#f-cat").value && (t.category || "none") !== $("#f-cat").value) return false;
  return true;
}

function render() {
  const rows = state.tasks.filter(matched);
  $("#shown").textContent = rows.length + " shown";
  const mode = state.today.mode || "auto";
  $("#mode").textContent = mode === "auto" ? "focus: automatic" : `focus: ${mode} (${(state.today.ids || []).length})`;
  $("#rows").innerHTML = rows.map((t, i) => `
    <tr class="${t.status === "done" ? "done" : ""}">
      <td class="num">${i + 1}</td>
      <td>${t.tier === 1 ? `<input type="checkbox" class="pickbox" data-id="${t.id}">` : ""}</td>
      <td class="title">${escapeHtml(t.title)}${t.status === "started" ? ' <span class="pill">started</span>' : ""}</td>
      <td><select data-id="${t.id}" data-field="status">${options(["open","started","done"], t.status)}</select></td>
      <td><select data-id="${t.id}" data-field="tier">${options([1,2,3,4], t.tier)}</select></td>
      <td><select data-id="${t.id}" data-field="impact">${options([0,0.1,0.2,0.3,0.4,0.5], t.impact)}</select></td>
      <td><select data-id="${t.id}" data-field="category">${options(state.categories, t.category || "none")}</select></td>
      <td>${t.first_seen || ""}</td>
      <td class="why">${escapeHtml(t.why || "")}</td>
    </tr>`).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function renderAdvisory() {
  const live = Object.fromEntries(state.tasks.map(t => [t.id, t.status]));
  const onlyDiff = $("#only-diff").checked;
  const rows = (state.advisory || []).filter(e => {
    if (applied.has(e.task_id)) return false;
    const current = live[e.task_id] || e.current;
    return !onlyDiff || e.suggested !== current;
  });
  $("#adv-count").textContent = rows.length + " suggestions";
  $("#adv-rows").innerHTML = rows.map(e => {
    const current = live[e.task_id] || e.current;
    return `
    <tr>
      <td>${escapeHtml(e.title)}</td>
      <td class="why">${escapeHtml(e.line)}${e.caveat ? " <em>(" + escapeHtml(e.caveat) + ")</em>" : ""}</td>
      <td>${e.suggested} <span class="pill">${escapeHtml(e.marker)}</span></td>
      <td>${current}</td>
      <td><button data-adv="${e.task_id}" data-set="${e.suggested}">Apply</button></td>
    </tr>`;
  }).join("");
}

async function load() {
  state = await api("/api/state");
  $("#counts").textContent = `${state.counts.open} open · ${state.counts.started} started · ${state.counts.done} done`;
  const cat = $("#f-cat");
  const current = cat.value;
  cat.innerHTML = '<option value="">Any category</option>' + options(state.categories, current);
  render();
  renderAdvisory();
  $("#digest-text").textContent = state.digest;
  $("#log-text").textContent = state.log;
  $("#s-deadlines").checked = !!state.settings.use_deadlines;
  $("#s-extract").checked = !!state.settings.auto_extract;
  $("#s-top").value = state.settings.notify_top || 3;
}

$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  activeTab = btn.dataset.tab;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b === btn));
  for (const id of ["tasks", "digest", "advisory", "settings", "log"]) $("#" + id).classList.toggle("hide", id !== activeTab);
  if (activeTab === "digest") api("/api/state").then(s => $("#digest-text").textContent = s.digest);
  if (activeTab === "log") api("/api/state").then(s => $("#log-text").textContent = s.log);
  if (activeTab === "advisory") api("/api/state").then(s => { state = s; renderAdvisory(); });
});

$("#rows").addEventListener("change", async (e) => {
  const sel = e.target.closest("select");
  if (!sel) return;
  try {
    await api("/api/task", { id: sel.dataset.id, field: sel.dataset.field, value: sel.value });
    await load();
    toast("Saved");
  } catch (err) { toast(err.message); }
});

for (const id of ["#search", "#f-status", "#f-tier", "#f-cat"]) {
  $(id).addEventListener("input", render);
}

$("#refresh").addEventListener("click", load);

$("#shuffle").addEventListener("click", async () => {
  try { await api("/api/shuffle", { n: 7 }); await load(); toast("Shuffled tier 1"); }
  catch (err) { toast(err.message); }
});

$("#auto-pick").addEventListener("click", async () => {
  try { await api("/api/today", { mode: "auto" }); await load(); toast("Focus back to automatic"); }
  catch (err) { toast(err.message); }
});

$("#use-picked").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll(".pickbox:checked")].map(x => x.dataset.id);
  if (!ids.length) return toast("Tick at least one tier 1 task");
  try { await api("/api/today", { ids }); await load(); toast(ids.length + " picks saved for today"); }
  catch (err) { toast(err.message); }
});

$("#only-diff").addEventListener("change", renderAdvisory);

$("#adv-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-adv]");
  if (!btn) return;
  try {
    await api("/api/task", { id: btn.dataset.adv, field: "status", value: btn.dataset.set });
    applied.add(btn.dataset.adv);
    await load();
    toast("State updated");
  } catch (err) { toast(err.message); }
});

$("#rescan").addEventListener("click", async () => {
  $("#adv-count").textContent = "scanning...";
  try { await api("/api/scan", {}); applied.clear(); await load(); renderAdvisory(); }
  catch (err) { toast(err.message); }
});

$("#rebuild").addEventListener("click", async () => {
  $("#build-msg").textContent = "working...";
  try {
    const out = await api("/api/digest", {});
    $("#build-msg").textContent = out.output;
    $("#digest-text").textContent = out.digest;
  } catch (err) { $("#build-msg").textContent = err.message; }
});

$("#save-settings").addEventListener("click", async () => {
  try {
    await api("/api/settings", { key: "use_deadlines", value: $("#s-deadlines").checked });
    await api("/api/settings", { key: "auto_extract", value: $("#s-extract").checked });
    await api("/api/settings", { key: "notify_top", value: $("#s-top").value });
    toast("Settings saved");
    await load();
  } catch (err) { toast(err.message); }
});

$("#run-now").addEventListener("click", async () => {
  try {
    await api("/api/run", {});
    $("#run-msg").textContent = "started in the background, see the Log tab";
  } catch (err) { $("#run-msg").textContent = err.message; }
});

$("#refresh-log").addEventListener("click", () => api("/api/state").then(s => $("#log-text").textContent = s.log));

load();
</script></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"task console on {url}  (Ctrl+C to stop)")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())