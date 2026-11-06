"""Asks PioSOLVER (through its command interface) a few questions about one saved solve and
writes the answers to pio_probe.txt, small enough to send. Used once, to build the exporter
against your Pio version's real output.

    py -3.13 solver/pio_probe.py "D:\\path\\to\\SRP_BTN_vs_BB_30bb_Ks7d2c.cfr"

It looks for PioSOLVER2-edge.exe in the usual places; if it can't find it, add
--pio "C:\\PioSOLVER\\PioSOLVER2-edge.exe" (wherever yours is).
"""

import argparse
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

END = "END_OF_PIO_COMMAND"
GUESSES = [r"C:\PioSOLVER", r"C:\PioSOLVER2", r"C:\Program Files\PioSOLVER",
           r"C:\Program Files (x86)\PioSOLVER", r"D:\PioSOLVER", r"D:\PioSOLVER2"]
NAMES = ["PioSOLVER2-edge.exe", "PioSOLVER2-pro.exe", "PioSOLVER-edge.exe", "PioSOLVER-pro.exe"]


def find_pio(given):
    if given:
        return Path(given)
    for d in GUESSES:
        for n in NAMES:
            p = Path(d) / n
            if p.exists():
                return p
    return None


class Pio:
    def __init__(self, exe):
        self.p = subprocess.Popen([str(exe)], cwd=str(exe.parent), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  bufsize=1)
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.lines.put(line.rstrip("\n"))

    def ask(self, cmd, timeout=120.0, until_end=True):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()
        out, deadline = [], time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=0.5)
            except queue.Empty:
                if not until_end and out:
                    break
                continue
            if line.strip() == END:
                break
            out.append(line)
        return out

    def close(self):
        try:
            self.p.stdin.write("exit\n")
            self.p.stdin.flush()
        except OSError:
            pass
        time.sleep(0.5)
        self.p.kill()


def main():
    ap = argparse.ArgumentParser(description="Probe one Pio save for the exporter.")
    ap.add_argument("cfr")
    ap.add_argument("--pio", help="path to PioSOLVER2-edge.exe")
    ap.add_argument("--out", default="pio_probe.txt")
    args = ap.parse_args()
    exe = find_pio(args.pio)
    if exe is None or not exe.exists():
        sys.exit('Could not find PioSOLVER2-edge.exe. Run again with --pio "C:\\...\\PioSOLVER2-edge.exe"')
    cfr = Path(args.cfr).resolve()
    if not cfr.exists():
        sys.exit(f"No such file: {cfr}")
    log = []

    def note(title, lines):
        log.append(f"### {title}")
        log.extend(lines)
        log.append("")
        print(f"{title}: {len(lines)} lines")

    pio = Pio(exe)
    try:
        note("start", pio.ask("is_ready", 30, until_end=False))
        note("set_end_string", pio.ask(f"set_end_string {END}", 30, until_end=False))
        note("is_ready", pio.ask("is_ready", 30))
        note("load_tree", pio.ask(f'load_tree "{cfr}"', 600))
        for cmd in ("show_tree_info", "show_hand_order", "show_node r:0", "show_children r:0",
                    "show_range OOP r:0", "show_range IP r:0", "show_strategy r:0"):
            note(cmd, pio.ask(cmd, 120))
        # One level deeper: the first child of the root (often the OOP check).
        kids = [ln for ln in log if ln.startswith("r:0:")]
        if kids:
            child = kids[0].split()[0]
            for cmd in (f"show_node {child}", f"show_children {child}", f"show_strategy {child}"):
                note(cmd, pio.ask(cmd, 120))
    finally:
        pio.close()
    Path(args.out).write_text("\n".join(log), encoding="utf-8")
    size = os.path.getsize(args.out) / 1024
    print(f"\nwrote {args.out} ({size:.0f} KB): send this file")


if __name__ == "__main__":
    main()
