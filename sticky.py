"""A Windows Sticky Note style window for today's focus. Standard library only.

Run: pythonw sticky.py

Borderless yellow note, drag it by the top strip, resize from the bottom right.
Tick a task to mark it done; right-click a task for Started or Open. The note
saves its position and size in sticky.json and refreshes every minute.
"""
import json
import pathlib
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date

import tracker

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
GEOMETRY = HERE / "sticky.json"
CONSOLE = "http://127.0.0.1:8765/"
PAPER = "#FFF9B1"
EDGE = "#E8D96B"
INK = "#262626"
MUTED = "#8a7f45"


def read_tasks():
    return json.loads(TASKS.read_text(encoding="utf-8"))


def write_tasks(data):
    tmp = TASKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TASKS)


def set_status(task_id, status):
    data = read_tasks()
    for task in data["tasks"]:
        if task["id"] == task_id:
            task["status"] = status
            task["completed"] = date.today().isoformat() if status == "done" else None
            break
    write_tasks(data)
    threading.Thread(
        target=lambda: subprocess.run(
            [sys.executable, "tracker.py", "digest"], cwd=HERE, capture_output=True
        ),
        daemon=True,
    ).start()


def load_geometry():
    try:
        data = json.loads(GEOMETRY.read_text(encoding="utf-8"))
        return int(data["w"]), int(data["h"]), int(data["x"]), int(data["y"])
    except Exception:
        return 400, 560, 90, 90


class Sticky(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.attributes("-topmost", False)
        self.configure(bg=EDGE)
        w, h, x, y = load_geometry()
        self.geometry(f"{w}x{h}+{x}+{y}")
        self._move = None
        self._size = None

        self.paper = tk.Frame(self, bg=PAPER)
        self.paper.pack(fill="both", expand=True, padx=1, pady=1)

        top = tk.Frame(self.paper, bg=PAPER, cursor="fleur")
        top.pack(fill="x", padx=10, pady=(6, 0))
        self.head = tk.Label(top, bg=PAPER, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.head.pack(side="left")
        for text, command in (
            ("x", self.close),
            ("console", lambda: webbrowser.open(CONSOLE)),
            ("shuffle", self.shuffle),
            ("pin", self.toggle_pin),
        ):
            link = tk.Label(top, text=text, bg=PAPER, fg=MUTED, font=("Segoe UI", 8), cursor="hand2")
            link.pack(side="right", padx=(8, 0))
            link.bind("<Button-1>", lambda _e, c=command: c())
        top.bind("<ButtonPress-1>", self.start_move)
        top.bind("<B1-Motion>", self.move)

        self.body = tk.Frame(self.paper, bg=PAPER)
        self.body.pack(fill="both", expand=True, padx=12, pady=(2, 2))

        bottom = tk.Frame(self.paper, bg=PAPER)
        bottom.pack(fill="x", padx=10, pady=(0, 6))
        self.count = tk.Label(bottom, bg=PAPER, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.count.pack(side="left")
        grip = tk.Label(bottom, text="//", bg=PAPER, fg=MUTED, font=("Segoe UI", 8), cursor="size_nw_se")
        grip.pack(side="right")
        grip.bind("<ButtonPress-1>", self.start_size)
        grip.bind("<B1-Motion>", self.resize)

        self.bind("<Escape>", lambda _e: self.close())
        self.reload()
        self.after(60000, self.tick)

    def start_move(self, event):
        self._move = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def move(self, event):
        if self._move:
            self.geometry(f"+{event.x_root - self._move[0]}+{event.y_root - self._move[1]}")

    def start_size(self, event):
        self._size = (event.x_root, event.y_root, self.winfo_width(), self.winfo_height())

    def resize(self, event):
        if self._size:
            x0, y0, w0, h0 = self._size
            self.geometry(f"{max(280, w0 + event.x_root - x0)}x{max(220, h0 + event.y_root - y0)}")

    def close(self):
        GEOMETRY.write_text(
            json.dumps(
                {
                    "w": self.winfo_width(),
                    "h": self.winfo_height(),
                    "x": self.winfo_x(),
                    "y": self.winfo_y(),
                }
            ),
            encoding="utf-8",
        )
        self.destroy()

    def toggle_pin(self):
        on = not bool(self.attributes("-topmost"))
        self.attributes("-topmost", on)
        self.head.configure(text=f"{date.today():%d %b}" + ("" if on else "  (unpinned)"))

    def tick(self):
        self.reload()
        self.after(60000, self.tick)

    def shuffle(self):
        subprocess.run([sys.executable, "tracker.py", "shuffle"], cwd=HERE, capture_output=True)
        self.reload()

    def reload(self):
        for child in self.body.winfo_children():
            child.destroy()
        tasks = read_tasks()
        picks, mode = tracker.today_picks(tasks, 7)
        self.head.configure(text=f"{date.today():%d %b}   {mode if mode != 'auto' else ''}".strip())
        if not picks:
            tk.Label(self.body, text="No open tasks.", bg=PAPER, fg=INK, anchor="w").pack(fill="x")
            self.count.configure(text="")
            return
        done = sum(1 for t in picks if t.get("status") == "done")
        for task in picks:
            finished = task.get("status") == "done"
            font = ("Segoe UI", 10, "overstrike") if finished else ("Segoe UI", 10)
            var = tk.BooleanVar(value=finished)
            text = task["title"]
            if task.get("category") and task["category"] != "none":
                text += f"  [{task['category']}]"
            check = tk.Checkbutton(
                self.body,
                text=text,
                variable=var,
                command=lambda tid=task["id"], v=var: self.toggle(tid, v),
                bg=PAPER,
                fg=INK if not finished else "#8a8464",
                activebackground=PAPER,
                activeforeground=INK,
                selectcolor="#EFE59A",
                highlightthickness=0,
                bd=0,
                anchor="w",
                justify="left",
                wraplength=max(220, self.winfo_width() - 70),
                font=font,
                cursor="hand2",
            )
            check.pack(fill="x", pady=1)
            check.bind("<Button-3>", lambda e, t=task: self.menu(e, t))
        self.count.configure(text=f"{done} of {len(picks)} done")

    def toggle(self, task_id, var):
        set_status(task_id, "done" if var.get() else "open")
        self.after(20, self.reload)

    def menu(self, event, task):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Mark started", command=lambda: self.set_and_reload(task["id"], "started"))
        menu.add_command(label="Mark open", command=lambda: self.set_and_reload(task["id"], "open"))
        menu.add_command(label="Mark done", command=lambda: self.set_and_reload(task["id"], "done"))
        menu.tk_popup(event.x_root, event.y_root)

    def set_and_reload(self, task_id, status):
        set_status(task_id, status)
        self.reload()


if __name__ == "__main__":
    Sticky().mainloop()