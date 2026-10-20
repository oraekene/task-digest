"""A quiet desktop note for today's focus. Standard library only.

Run: pythonw sticky.py

Borderless paper surface, drawn on a canvas. Click a row to tick it, right-click
for Started or Open, drag the paper to move it. Position, size and pin state live
in sticky.json. It refreshes every minute and stays out of the way of other
windows, while coming back when the desktop itself is shown.
"""
import ctypes
import json
import pathlib
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import date

import tracker

HERE = pathlib.Path(__file__).parent
TASKS = HERE / "tasks.json"
GEOMETRY = HERE / "sticky.json"
CONSOLE = "http://127.0.0.1:8765/"

PAPER = "#FAF9F6"
INK = "#1D1D1F"
MUTED = "#6E6E73"
HAIRLINE = "#ECEAE4"
HOVER = "#F1EFE9"
PRESSED = "#E8E5DD"
RING = "#CFCDC6"
DONE_INK = "#A1A1A6"
ACCENT = "#0A84FF"

PAD = 16
ROW_H = 34
HEADER_H = 46
ROWS_Y = 52
FOOTER_H = 26
DEFAULT_W = 360
MIN_W = 300
MIN_H = 150
FIT_VERSION = 2


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
        return {
            "w": int(data["w"]),
            "h": int(data["h"]),
            "x": int(data["x"]),
            "y": int(data["y"]),
            "pinned": bool(data.get("pinned")),
            "fit": int(data.get("v") or 0) == FIT_VERSION,
        }
    except Exception:
        return {"w": DEFAULT_W, "h": 0, "x": 90, "y": 90, "pinned": False, "fit": False}


def pick_font(root, display=False):
    families = set(tkfont.families(root))
    for name in ("Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"):
        if name in families:
            return name
    return "Segoe UI"


def blend(a, b, t):
    a = a.lstrip("#")
    b = b.lstrip("#")
    out = []
    for i in (0, 2, 4):
        av = int(a[i : i + 2], 16)
        bv = int(b[i : i + 2], 16)
        out.append(round(av + (bv - av) * t))
    return "#%02x%02x%02x" % tuple(out)


def ease_out(t):
    return 1 - (1 - t) ** 3


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


def ellipsize(text, font, max_width):
    if font.measure(text) <= max_width:
        return text
    mark = "\u2026"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if font.measure(text[:mid].rstrip() + mark) <= max_width:
            lo = mid + 1
        else:
            hi = mid
    return text[: max(0, lo - 1)].rstrip() + mark


def raise_from_desktop(hwnd, topmost):
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    parent = user32.GetParent(ctypes.c_void_p(hwnd)) or hwnd
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.SetWindowPos(
        ctypes.c_void_p(parent),
        ctypes.c_void_p(-1 if topmost else -2),
        0, 0, 0, 0, 0x13,
    )


def desktop_in_front():
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        return buf.value in ("Progman", "WorkerW")
    except Exception:
        return False


class Sticky(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.configure(bg=PAPER)
        geo = load_geometry()
        self.win_w = geo["w"]
        self.win_h = geo["h"]
        self.pos_x = geo["x"]
        self.pos_y = geo["y"]
        self.pinned = geo["pinned"]
        self._fit_wanted = not geo["fit"]
        self.attributes("-topmost", self.pinned)
        self.geometry(f"{self.win_w}x{max(1, self.win_h)}+{self.pos_x}+{self.pos_y}")

        display = pick_font(self, display=True)
        text = pick_font(self)
        self.title_font = tkfont.Font(family=display, size=12, weight="bold")
        self.date_font = tkfont.Font(family=text, size=9)
        self.link_font = tkfont.Font(family=text, size=9)
        self.row_font = tkfont.Font(family=text, size=10)
        self.meta_font = tkfont.Font(family=text, size=9)

        self.canvas = tk.Canvas(self, bg=PAPER, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.picks = []
        self.mode = "auto"
        self.hover = -1
        self.hover_link = ""
        self.pressed = -1
        self.scroll = 0
        self._links = []
        self._move = None
        self._size = None
        self._click = None
        self._check_anim = {}
        self._busy = False
        self._reload_evt = threading.Event()
        self._closing = False
        self._auto_top = False

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", self.on_leave)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.bind("<Escape>", lambda _e: self.close())

        self.reload()
        self._round_corners()
        self.after(400, self._pump)
        self.after(60000, self._tick)
        self.after(600, self.save_geometry)

    def _round_corners(self):
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
            )
        except Exception:
            pass

    def _pump(self):
        if self._closing:
            return
        if self._reload_evt.is_set():
            self._reload_evt.clear()
            self._busy = False
            self.scroll = 0
            self.reload(grow=True)
        if not self.pinned:
            front = desktop_in_front()
            if front != self._auto_top:
                self._auto_top = front
                raise_from_desktop(self.winfo_id(), front)
        self.after(400, self._pump)

    def _tick(self):
        self.reload()
        self.after(60000, self._tick)

    def reload(self, grow=False):
        tasks = read_tasks()
        picks, mode = tracker.today_picks(tasks, 7)
        changed = len(picks) != len(self.picks)
        self.picks = picks
        self.mode = mode
        if self.scroll:
            content = len(picks) * ROW_H
            viewport = self.win_h - ROWS_Y - FOOTER_H
            self.scroll = max(0, min(self.scroll, max(0, content - viewport)))
        needed = ROWS_Y + len(picks) * ROW_H + FOOTER_H
        if self._fit_wanted:
            self.win_h = needed
            self._fit_wanted = False
            self._apply_size()
        elif (grow or changed) and needed > self.win_h:
            self.win_h = needed
            self._apply_size()
        self.draw()

    def _apply_size(self):
        self.geometry(f"{self.win_w}x{self.win_h}+{self.pos_x}+{self.pos_y}")

    def draw(self):
        c = self.canvas
        c.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return

        c.create_text(PAD, 20, text="Today", anchor="w", font=self.title_font, fill=INK)
        tw = self.title_font.measure("Today")
        c.create_text(
            PAD + tw + 10, 21, text=f"{date.today():%a %d %b}",
            anchor="w", font=self.date_font, fill=MUTED,
        )

        self._links = []
        x = w - PAD
        for name, label in (
            ("close", "\u00d7"),
            ("pin", "Pin"),
            ("console", "Console"),
            ("shuffle", "Shuffle"),
        ):
            color = ACCENT if (name == "pin" and self.pinned) else (
                INK if self.hover_link == name else MUTED
            )
            width = self.link_font.measure(label)
            c.create_text(
                x - width, 20, text=label, anchor="w", font=self.link_font, fill=color
            )
            self._links.append((name, x - width, 8, x, 32))
            x -= width + 14

        c.create_line(PAD, HEADER_H - 4, w - PAD, HEADER_H - 4, fill=HAIRLINE)

        done_count = sum(1 for t in self.picks if t.get("status") == "done")
        if not self.picks:
            c.create_text(
                w / 2, ROWS_Y + 30, text="Nothing picked for today.",
                font=self.row_font, fill=MUTED,
            )

        top = ROWS_Y - self.scroll
        limit = h - FOOTER_H
        for i, task in enumerate(self.picks):
            y = top + i * ROW_H
            if y + ROW_H < ROWS_Y or y > limit:
                continue
            finished = task.get("status") == "done"
            hovered = i == self.hover
            pressed = i == self.pressed
            if hovered or pressed:
                round_rect(
                    c, 8, y + 1, w - 8, y + ROW_H - 1, 8,
                    fill=PRESSED if pressed else HOVER, outline="",
                )
            base = PRESSED if pressed else HOVER if hovered else PAPER
            progress = self._check_progress(task["id"], finished)
            fill = blend(base, ACCENT, progress)
            ring = blend(RING, ACCENT, progress) if progress < 0.5 else ACCENT
            cy = y + ROW_H / 2
            cx = PAD + 8
            c.create_oval(
                cx - 8, cy - 8, cx + 8, cy + 8,
                outline=ring, width=1, fill=fill,
            )
            if progress > 0.4:
                c.create_line(
                    cx - 3.5, cy + 0.5, cx - 1, cy + 3,
                    fill="#FFFFFF", width=1.6, capstyle="round",
                )
                c.create_line(
                    cx - 1, cy + 3, cx + 4, cy - 3.5,
                    fill="#FFFFFF", width=1.6, capstyle="round",
                )
            text_x = PAD + 26
            title = ellipsize(task["title"], self.row_font, w - text_x - PAD)
            c.create_text(
                text_x, cy, text=title, anchor="w", font=self.row_font,
                fill=DONE_INK if finished else INK,
            )

        c.create_text(
            PAD, h - 13, text=f"{done_count} of {len(self.picks)} done",
            anchor="w", font=self.meta_font, fill=MUTED,
        )
        if self._busy:
            c.create_text(
                w - PAD, h - 13, text="shuffling\u2026", anchor="e",
                font=self.meta_font, fill=MUTED,
            )
        elif self.mode in ("shuffle", "manual"):
            c.create_text(
                w - PAD, h - 13, text="shuffled" if self.mode == "shuffle" else "picked",
                anchor="e", font=self.meta_font, fill=MUTED,
            )
        c.create_rectangle(0.5, 0.5, w - 0.5, h - 0.5, outline="#E9E7E0")

    def _check_progress(self, task_id, finished):
        anim = self._check_anim.get(task_id)
        if not anim:
            return 1.0 if finished else 0.0
        start, frm, to = anim
        p = min(1.0, (time.time() - start) / 0.14)
        return frm + (to - frm) * ease_out(p)

    def _animate(self):
        now = time.time()
        for task_id, (start, _frm, _to) in list(self._check_anim.items()):
            if now - start > 0.16:
                del self._check_anim[task_id]
        self.draw()
        if self._check_anim:
            self.after(16, self._animate)

    def row_at(self, x, y):
        if y < ROWS_Y or y >= self.winfo_height() - FOOTER_H:
            return None
        i = int((y - ROWS_Y + self.scroll) // ROW_H)
        if 0 <= i < len(self.picks):
            return i
        return None

    def link_at(self, x, y):
        for name, x1, y1, x2, y2 in self._links:
            if x1 - 6 <= x <= x2 + 6 and y1 <= y <= y2:
                return name
        return ""

    def grip_at(self, x, y):
        return x >= self.winfo_width() - 18 and y >= self.winfo_height() - 18

    def on_press(self, event):
        name = self.link_at(event.x, event.y)
        if name:
            self.link_action(name)
            return
        if self.grip_at(event.x, event.y):
            self._size = (event.x_root, event.y_root, self.win_w, self.win_h)
            return
        i = self.row_at(event.x, event.y)
        if i is not None:
            self.pressed = i
            self._click = (i, event.x_root, event.y_root)
            self.draw()
            return
        self._move = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def move_to(self, x, y):
        self.pos_x, self.pos_y = x, y
        self.geometry(f"+{x}+{y}")

    def on_drag(self, event):
        if self._size:
            x0, y0, w0, h0 = self._size
            self.win_w = max(MIN_W, w0 + event.x_root - x0)
            self.win_h = max(MIN_H, h0 + event.y_root - y0)
            self.geometry(f"{self.win_w}x{self.win_h}")
            return
        if self._click:
            i, x0, y0 = self._click
            if abs(event.x_root - x0) + abs(event.y_root - y0) > 6:
                self._click = None
                self.pressed = -1
                self._move = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())
                self.draw()
            return
        if self._move:
            self.move_to(event.x_root - self._move[0], event.y_root - self._move[1])

    def on_release(self, _event):
        if self._click:
            i = self._click[0]
            self._click = None
            self.pressed = -1
            self.toggle(i)
            return
        if self._size or self._move:
            self._size = None
            self._move = None
            self.save_geometry()

    def on_motion(self, event):
        name = self.link_at(event.x, event.y)
        i = self.row_at(event.x, event.y)
        if name == self.hover_link and i == self.hover:
            return
        self.hover_link = name
        self.hover = i
        if self.grip_at(event.x, event.y):
            self.canvas.configure(cursor="size_nw_se")
        elif name or i is not None:
            self.canvas.configure(cursor="hand2")
        elif event.y < HEADER_H:
            self.canvas.configure(cursor="fleur")
        else:
            self.canvas.configure(cursor="")
        self.draw()

    def on_leave(self, _event):
        if self.hover == -1 and not self.hover_link:
            return
        self.hover = -1
        self.hover_link = ""
        self.canvas.configure(cursor="")
        self.draw()

    def on_wheel(self, event):
        content = len(self.picks) * ROW_H
        viewport = self.winfo_height() - ROWS_Y - FOOTER_H
        maxoff = max(0, content - viewport)
        if maxoff == 0:
            return
        self.scroll = max(0, min(maxoff, self.scroll - (event.delta / 120) * 60))
        self.draw()

    def on_right(self, event):
        i = self.row_at(event.x, event.y)
        if i is None:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Mark started", command=lambda: self.set_and_reload(i, "started"))
        menu.add_command(label="Mark open", command=lambda: self.set_and_reload(i, "open"))
        menu.add_command(label="Mark done", command=lambda: self.set_and_reload(i, "done"))
        menu.tk_popup(event.x_root, event.y_root)

    def link_action(self, name):
        if name == "close":
            self.close()
        elif name == "console":
            webbrowser.open(CONSOLE)
        elif name == "shuffle":
            self.shuffle()
        elif name == "pin":
            self.pinned = not self.pinned
            self._auto_top = False
            self.attributes("-topmost", self.pinned)
            self.save_geometry()
            self.draw()

    def toggle(self, i):
        task = self.picks[i]
        was_done = task.get("status") == "done"
        self._check_anim[task["id"]] = (time.time(), 1.0 if was_done else 0.0, 0.0 if was_done else 1.0)
        set_status(task["id"], "open" if was_done else "done")
        self.reload()
        self._animate()

    def set_and_reload(self, i, status):
        task = self.picks[i]
        was_done = task.get("status") == "done"
        target = 1.0 if status == "done" else 0.0
        self._check_anim[task["id"]] = (time.time(), 1.0 if was_done else 0.0, target)
        set_status(task["id"], status)
        self.reload()
        self._animate()

    def shuffle(self):
        if self._busy:
            return
        self._busy = True
        self.draw()

        def work():
            subprocess.run(
                [sys.executable, "tracker.py", "shuffle"], cwd=HERE, capture_output=True
            )
            self._reload_evt.set()

        threading.Thread(target=work, daemon=True).start()

    def save_geometry(self):
        GEOMETRY.write_text(
            json.dumps(
                {
                    "v": FIT_VERSION,
                    "w": self.win_w,
                    "h": self.win_h,
                    "x": self.pos_x,
                    "y": self.pos_y,
                    "pinned": self.pinned,
                }
            ),
            encoding="utf-8",
        )

    def close(self):
        self._closing = True
        self.save_geometry()
        self.destroy()


if __name__ == "__main__":
    Sticky().mainloop()
