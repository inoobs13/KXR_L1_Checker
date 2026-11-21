import contextlib
import io
import re
import sys
import traceback
import tkinter as tk
from tkinter import filedialog, scrolledtext

# A windowed .exe has no console, so give stray print() calls somewhere to go
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

from ork_parser import Rocket
import L1_check as checks

# Edit this list to match the functions in L1_check.py (run in this order)
CHECKS = [
    ("Materials",       checks.check_materials),
    ("Nose cone",       checks.check_nosecone),
    ("Body tube",       checks.check_body_tube),
    ("Fins",            checks.check_fins),
    ("Centering rings", checks.check_centering_rings),
    ("Motor tube",      checks.check_motor_tube),
    ("Motor",           checks.check_motor),
    ("Retainer",        checks.check_retainer),
    ("Eyebolt",         checks.check_eyebolt),
    ("Parachute",       checks.check_parachute),
    ("Shock cord",      checks.check_shock_cord),
]

ANSI = re.compile(r"\033\[([\d;]*)m")
COLOR_TAGS = {"31": "red", "91": "red", "32": "green", "92": "green", "33": "yellow", "93": "yellow"}


def insert_ansi(widget, text):
    """Insert text into the box, turning the terminal colour codes into coloured text."""
    tag, pos = None, 0
    for m in ANSI.finditer(text):
        widget.insert("end", text[pos:m.start()], *([tag] if tag else []))
        codes = m.group(1).split(";")
        if codes in ([""], ["0"]):
            tag = None
        else:
            tag = next((COLOR_TAGS[c] for c in codes if c in COLOR_TAGS), tag)
        pos = m.end()
    widget.insert("end", text[pos:], *([tag] if tag else []))


def run_checks(path):
    out.config(state="normal")
    out.delete("1.0", "end")
    try:
        rocket = Rocket(path)
    except Exception as e:
        insert_ansi(out, f"{checks.RED}Could not read file:{checks.RESET} {e}\n")
        out.config(state="disabled")
        return

    insert_ansi(out, f"File: {path}\nRocket: {rocket.name}\n\n")
    passed = 0
    for name, fn in CHECKS:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fn(rocket)
        except Exception:
            buf.write(f"{checks.RED}{name}: check crashed{checks.RESET}\n{traceback.format_exc(limit=2)}\n")
        text = buf.getvalue()
        if "FLAGS" not in text and "crashed" not in text:
            passed += 1
        insert_ansi(out, text)

    color = checks.GREEN if passed == len(CHECKS) else checks.RED
    insert_ansi(out, f"\n{color}{passed} of {len(CHECKS)} checks passed{checks.RESET}\n")
    out.config(state="disabled")
    status.config(text=path)


def open_file():
    path = filedialog.askopenfilename(title="Select OpenRocket file",
                                      filetypes=[("OpenRocket files", "*.ork"), ("All files", "*.*")])
    if path:
        run_checks(path)


root = tk.Tk()
root.title("L1 Rocket Checker")
root.geometry("820x640")

bar = tk.Frame(root)
bar.pack(fill="x", padx=8, pady=8)
tk.Button(bar, text="Open .ork file…", command=open_file, width=18).pack(side="left")
tk.Button(bar, text="Re-check", width=10,
          command=lambda: status.cget("text") and run_checks(status.cget("text"))).pack(side="left", padx=6)
status = tk.Label(bar, text="", anchor="w", fg="gray")
status.pack(side="left", fill="x", expand=True)

out = scrolledtext.ScrolledText(root, font=("Consolas", 10), bg="#1e1e1e", fg="#dddddd",
                                insertbackground="white", wrap="word")
out.pack(fill="both", expand=True, padx=8, pady=(0, 8))
out.tag_config("red", foreground="#ff5c5c")
out.tag_config("green", foreground="#4cd964")
out.tag_config("yellow", foreground="#ffcc00")
insert_ansi(out, "Click 'Open .ork file…' to check a design.\n")
out.config(state="disabled")

root.mainloop()