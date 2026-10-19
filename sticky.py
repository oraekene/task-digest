"""Always-on-top sticky note with today's focus. Standard library only.

Run: pythonw sticky.py

It shows the same picks as the digest. Buttons change state and rebuild the
digest. Everything else lives in the console; the Console button opens it.
"""
import json
import pathlib
import subprocess
import sys
import tkinter as tk
import webbrowser
from datetime import date

import tracker

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
CONSOLE = "http://127.0.0.1:8765/"
BG = "#FFF7C2"
INK = "#2b2b2b"


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
    subprocess.run([sys.executable, "tracker.py", "digest"], cwd=HERE, capture_output=True)


class Sticky(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Today")
        self.configure(bg=BG)
        self.geometry("390x560+90+90")
        self.attributes("-topmost", True)
        self.minsize(320, 260)
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=12, pady=(10, 2))
        self.head = tk.Label(header, bg=BG, fg=INK, font=("Segoe UI", 11, "bold"), anchor="w")
        self.head.pack(side="left")
        self.mode = tk.Label(header, bg=BG, fg="#777", font=("Segoe UI", 8), anchor="e")
        self.mode.pack(side="right")
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=8, pady=4)
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=8, pady=(0, 8))
        for text, command in (
            ("Shuffle", self.shuffle),
            ("Console", lambda: webbrowser.open(CONSOLE)),
            ("Refresh", self.reload),
        ):
            tk.Button(
                footer, text=text, command=command, font=("Segoe UI", 9),
                relief="flat", bg="#efe3a8", activebackground="#e6d78f", cursor="hand2",
            ).pack(side="left", padx=(0, 6))
        self.reload()
        self.after(60000, self.tick)

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
        self.head.configure(text=f"Today - {date.today():%d %b}")
        self.mode.configure(text={"auto": "automatic", "shuffle": "shuffled", "manual": "your picks"}[mode])
        if not picks:
            tk.Label(self.body, text="No open tasks.", bg=BG, fg=INK).pack(anchor="w")
            return
        for i, task in enumerate(picks, 1):
            row = tk.Frame(self.body, bg=BG)
            row.pack(fill="x", pady=3)
            label = f"{i}. {task['title']}"
            if task.get("category") and task["category"] != "none":
                label += f"  [{task['category']}]"
            tk.Label(
                row, text=label, bg=BG, fg=INK, font=("Segoe UI", 9),
                wraplength=350, justify="left", anchor="w",
            ).pack(anchor="w")
            buttons = tk.Frame(row, bg=BG)
            buttons.pack(anchor="w")
            for text, status in (("Done", "done"), ("Started", "started"), ("Open", "open")):
                tk.Button(
                    buttons, text=text, font=("Segoe UI", 8), relief="flat",
                    bg="#efe3a8", activebackground="#e6d78f", cursor="hand2",
                    command=lambda t=task["id"], s=status: self.mark(t, s),
                ).pack(side="left", padx=(0, 5))
            tk.Frame(self.body, bg="#e2d48f", height=1).pack(fill="x", pady=2)

    def mark(self, task_id, status):
        set_status(task_id, status)
        self.reload()


if __name__ == "__main__":
    Sticky().mainloop()