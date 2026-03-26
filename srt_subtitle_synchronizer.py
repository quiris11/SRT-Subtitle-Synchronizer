#!/usr/bin/env python3
"""
SRT Subtitle Synchronizer
Linearly stretches/shifts all subtitle timestamps so that:
- the first subtitle's start time maps to a target start time
- the last subtitle's START time maps to a target end time
(so the last subtitle begins at the correct moment; its duration is preserved)

Supported input formats:
*.srt – SubRip
*.txt – MPL2 (times in deciseconds; '/' = italic, '|' = line break)

Input file:  original_name.lang.srt / .txt
Output file: original_name.lang.srt  (same name – original backed up as original_name.bkp.lang.srt)
"""

import re
import os
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── time helpers ──────────────────────────────────────────────────────────────

TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")

def time_to_ms(t: str) -> int:
    """'HH:MM:SS,mmm' → milliseconds"""
    m = TIME_RE.fullmatch(t.strip())
    if not m:
        raise ValueError(f"Invalid time format: '{t}' (expected HH:MM:SS,mmm)")
    h, mi, s, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    return ((h * 60 + mi) * 60 + s) * 1000 + ms

def ms_to_time(ms: int) -> str:
    """milliseconds → 'HH:MM:SS,mmm'"""
    ms = max(0, int(round(ms)))
    h, rem = divmod(ms, 3_600_000)
    mi, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1_000)
    return f"{h:02d}:{mi:02d}:{s:02d},{msec:03d}"

# ── SRT parsing ───────────────────────────────────────────────────────────────

ARROW = " --> "
TIMESTAMP_LINE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")

def parse_srt(path: str):
    """Return list of (index_line, start_ms, end_ms, text_lines)."""
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()

    blocks = []
    for block in re.split(r"\n{2,}", raw.strip()):
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        ts_idx = None
        for i, ln in enumerate(lines):
            if TIMESTAMP_LINE_RE.match(ln):
                ts_idx = i
                break
        if ts_idx is None:
            continue
        m = TIMESTAMP_LINE_RE.match(lines[ts_idx])
        start_ms = time_to_ms(m[1])
        end_ms   = time_to_ms(m[2])
        index_line = "\n".join(lines[:ts_idx])
        text_lines = lines[ts_idx + 1:]
        blocks.append((index_line, start_ms, end_ms, text_lines))
    return blocks

# ── MPL2 parsing ──────────────────────────────────────────────────────────────

MPL2_LINE_RE = re.compile(r"^\[(\d+)\]\[(\d+)\]\s?(.*)", re.DOTALL)

def _mpl2_text_to_srt_lines(raw_text: str) -> list:
    segments = raw_text.split("|")
    result = []
    for seg in segments:
        if seg.startswith("/"):
            content = seg[1:]
            result.append(f"<i>{content}</i>")
        else:
            result.append(seg)
    return result

def _read_mpl2_raw(path: str) -> str:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, encoding="cp1250") as f:
            return f.read()

def parse_mpl2(path: str):
    raw = _read_mpl2_raw(path)
    blocks = []
    for line in raw.splitlines():
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        m = MPL2_LINE_RE.match(line)
        if not m:
            continue
        start_ms = int(m.group(1)) * 100
        end_ms   = int(m.group(2)) * 100
        text_lines = _mpl2_text_to_srt_lines(m.group(3))
        blocks.append(("", start_ms, end_ms, text_lines))
    return blocks

# ── unified parse dispatcher ──────────────────────────────────────────────────

def parse_subtitle_file(path: str):
    if path.lower().endswith(".txt"):
        return parse_mpl2(path)
    return parse_srt(path)

def first_start_last_start(path: str):
    """Return (first_subtitle_start_ms, last_subtitle_START_ms) from any supported file."""
    blocks = parse_subtitle_file(path)
    if not blocks:
        raise ValueError("No subtitle blocks found in the file.")
    return blocks[0][1], blocks[-1][1]  # ← last START, not last END

# ── SRT writing ───────────────────────────────────────────────────────────────

def write_srt(blocks, target_start_ms: int, target_last_start_ms: int, out_path: str):
    """
    Linearly remap timestamps so that:
      - blocks[0].start → target_start_ms
      - blocks[-1].start → target_last_start_ms
    The duration of every subtitle is preserved (scaled uniformly).
    """
    orig_start      = blocks[0][1]
    orig_last_start = blocks[-1][1]   # ← anchor on last START
    span_orig = orig_last_start - orig_start
    span_tgt  = target_last_start_ms - target_start_ms

    if span_orig == 0:
        raise ValueError("Source first and last start times are identical – cannot scale.")
    if span_tgt <= 0:
        raise ValueError("Target last-subtitle start must be later than target start time.")

    def remap(ms):
        return target_start_ms + (ms - orig_start) * span_tgt / span_orig

    lines_out = []
    for idx, (index_line, s, e, text) in enumerate(blocks):
        new_s = ms_to_time(remap(s))
        new_e = ms_to_time(remap(e))
        lines_out.append(index_line or str(idx + 1))
        lines_out.append(f"{new_s}{ARROW}{new_e}")
        lines_out.extend(text)
        lines_out.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))

# ── path helpers ──────────────────────────────────────────────────────────────

def make_output_path(src_path: str) -> str:
    """Output overwrites the original file (same path)."""
    return src_path

def make_backup_path(src_path: str) -> str:
    """
    Build backup path by inserting '.bkp' before the language tag:
      movie.pl.srt  →  movie.bkp.pl.srt
      movie.srt     →  movie.bkp.srt
    """
    directory = os.path.dirname(src_path)
    basename  = os.path.basename(src_path)
    stem, _ext = os.path.splitext(basename)    # "movie.pl", ".srt"
    stem2, ext2 = os.path.splitext(stem)       # "movie",    ".pl"
    if ext2:
        bkp_name = f"{stem2}.bkp{ext2}.srt"   # movie.bkp.pl.srt
    else:
        bkp_name = f"{stem}.bkp.srt"           # movie.bkp.srt
    return os.path.join(directory, bkp_name)

# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SRT Subtitle Synchronizer")
        self.resizable(True, True)
        self.minsize(900, 560)
        self._active_text: tk.Text | None = None
        self._build_ui()

    def _build_ui(self):
        PAD = dict(padx=10, pady=6)

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        # ── Top controls ──────────────────────────────────────────────────────
        ctrl = ttk.Frame(outer)
        ctrl.grid(row=0, column=0, sticky="ew")
        ctrl.columnconfigure(1, weight=1)

        ttk.Label(ctrl, text="Source file (SRT / MPL2 TXT):",
                  font=("", 10, "bold")).grid(row=0, column=0, sticky="w", **PAD)
        self.src_var = tk.StringVar()
        ttk.Entry(ctrl, textvariable=self.src_var, width=55).grid(
            row=0, column=1, sticky="ew", **PAD)
        ttk.Button(ctrl, text="Browse…", command=self._browse_src).grid(
            row=0, column=2, **PAD)

        ttk.Label(ctrl, text="Reference SRT / TXT\n(optional – loads times):",
                  font=("", 10)).grid(row=1, column=0, sticky="w", **PAD)
        self.ref_var = tk.StringVar()
        ttk.Entry(ctrl, textvariable=self.ref_var, width=55).grid(
            row=1, column=1, sticky="ew", **PAD)
        ttk.Button(ctrl, text="Browse…", command=self._browse_ref).grid(
            row=1, column=2, **PAD)

        ttk.Separator(ctrl, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=4)

        ttk.Label(ctrl, text="Target start time\n(first subtitle appears):",
                  font=("", 10)).grid(row=3, column=0, sticky="w", **PAD)
        self.start_var = tk.StringVar(value="00:00:00,000")
        ttk.Entry(ctrl, textvariable=self.start_var, width=18).grid(
            row=3, column=1, sticky="w", **PAD)

        ttk.Label(ctrl, text="Target time of last subtitle\n(last subtitle starts):",
                  font=("", 10)).grid(row=4, column=0, sticky="w", **PAD)
        self.end_var = tk.StringVar(value="00:00:00,000")
        ttk.Entry(ctrl, textvariable=self.end_var, width=18).grid(
            row=4, column=1, sticky="w", **PAD)

        ttk.Label(ctrl, text="Format: HH:MM:SS,mmm",
                  foreground="gray", font=("", 8)).grid(
            row=4, column=1, sticky="se", padx=10)

        ttk.Label(ctrl, text="Output file:", font=("", 10)).grid(
            row=5, column=0, sticky="w", **PAD)
        self.out_var = tk.StringVar(value="(select source file first)")
        ttk.Label(ctrl, textvariable=self.out_var, foreground="#0055aa",
                  wraplength=480, justify="left").grid(
            row=5, column=1, columnspan=2, sticky="w", **PAD)

        ttk.Separator(ctrl, orient="horizontal").grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=4)

        ttk.Button(ctrl, text="⟳ Synchronize subtitles",
                   command=self._run, width=30).grid(
            row=7, column=0, columnspan=3, pady=8)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(ctrl, textvariable=self.status_var,
                  foreground="gray", font=("", 9)).grid(
            row=8, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 4))

        self.src_var.trace_add("write", lambda *_: self._update_out_preview())

        # ── Preview panel ─────────────────────────────────────────────────────
        preview = ttk.LabelFrame(outer, text="File Preview", padding=8)
        preview.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)

        self.src_text = self._make_text_panel(preview, column=0, label="◀ Source file")
        self.ref_text = self._make_text_panel(preview, column=1, label="Reference file ▶", mark_buttons=True)

        for widget in (self.src_text, self.ref_text):
            widget.bind("<FocusIn>",  lambda e, w=widget: self._set_active(w))
            widget.bind("<Button-1>", lambda e, w=widget: self.after(0, lambda: self._set_active(w)))

        tb = ttk.Frame(preview)
        tb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Button(tb, text="📋 Copy selection",
                   command=self._copy_selection).pack(side="left", padx=4)
        ttk.Button(tb, text="🗑 Delete selected lines",
                   command=self._delete_selected_lines).pack(side="left", padx=4)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(tb, text="💾 Save source file",
                   command=self._save_src).pack(side="left", padx=4)
        ttk.Button(tb, text="💾 Save reference file",
                   command=self._save_ref).pack(side="left", padx=4)
        ttk.Label(tb, text="Click inside a panel to make it active",
                  foreground="gray", font=("", 8)).pack(side="right", padx=8)

    def _make_text_panel(self, parent, column: int, label: str, mark_buttons: bool = False) -> tk.Text:
        f = ttk.Frame(parent)
        f.grid(row=0, column=column, sticky="nsew",
               padx=(0, 6) if column == 0 else (6, 0))
        txt_row = 3 if mark_buttons else 2
        f.rowconfigure(txt_row, weight=1)
        f.columnconfigure(0, weight=1)

        # ── Header row: label + scroll buttons ────────────────────────────────
        hdr = ttk.Frame(f)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        hdr.columnconfigure(0, weight=1)

        ttk.Label(hdr, text=label, font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w")

        btn_frame = ttk.Frame(hdr)
        btn_frame.grid(row=0, column=1, sticky="e")

        # Placeholder – real text widget bound after creation
        _txt_ref = [None]

        ttk.Button(
            btn_frame, text="⬆ Top", width=7,
            command=lambda: _txt_ref[0].see("1.0") if _txt_ref[0] else None
        ).pack(side="left", padx=(0, 2))
        ttk.Button(
            btn_frame, text="⬇ End", width=7,
            command=lambda: _txt_ref[0].see(tk.END) if _txt_ref[0] else None
        ).pack(side="left")

        if mark_buttons:
            mark_frame = ttk.Frame(f)
            mark_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
            ttk.Button(mark_frame, text="⏱ Mark start time",
                       command=self._mark_start_time).pack(side="left", padx=(0, 6))
            ttk.Button(mark_frame, text="⏱ Mark end time",
                       command=self._mark_end_time).pack(side="left")

        # ── Text widget + scrollbars ───────────────────────────────────────────
        txt = tk.Text(f, wrap="none", width=48, height=18,
                      undo=True, font=("Monospace", 9),
                      relief="sunken", borderwidth=1,
                      selectbackground="#3399ff", selectforeground="white")
        scrolly = ttk.Scrollbar(f, orient="vertical",   command=txt.yview)
        scrollx = ttk.Scrollbar(f, orient="horizontal",  command=txt.xview)
        txt.configure(yscrollcommand=scrolly.set, xscrollcommand=scrollx.set)
        txt.grid(row=txt_row, column=0, sticky="nsew")
        scrolly.grid(row=txt_row, column=1, sticky="ns")
        scrollx.grid(row=txt_row + 1, column=0, sticky="ew")

        _txt_ref[0] = txt  # wire the buttons to the real widget
        return txt

    def _mark_start_time(self):
        w = self._active_text
        if w is None:
            self.status_var.set("Click inside a panel first.")
            return
        try:
            sel = w.get(tk.SEL_FIRST, tk.SEL_LAST).strip()
            self.start_var.set(sel)
            self.status_var.set(f"Start time set to: {sel}")
        except tk.TclError:
            self.status_var.set("No text selected.")

    def _mark_end_time(self):
        w = self._active_text
        if w is None:
            self.status_var.set("Click inside a panel first.")
            return
        try:
            sel = w.get(tk.SEL_FIRST, tk.SEL_LAST).strip()
            self.end_var.set(sel)
            self.status_var.set(f"End time set to: {sel}")
        except tk.TclError:
            self.status_var.set("No text selected.")

    def _set_active(self, widget: tk.Text):
        self._active_text = widget

    _FILE_TYPES = [
        ("Subtitle files",    "*.srt *.txt"),
        ("SubRip subtitles",  "*.srt"),
        ("MPL2 subtitles (TXT)", "*.txt"),
        ("All files",         "*.*"),
    ]

    def _browse_src(self):
        path = filedialog.askopenfilename(
            title="Select source subtitle file (SRT or MPL2 TXT)",
            filetypes=self._FILE_TYPES)
        if not path:
            return
        self.src_var.set(path)
        if not self.ref_var.get().strip():
            self._load_times_from(path, "source")
        self._load_preview(self.src_text, path)

    def _browse_ref(self):
        path = filedialog.askopenfilename(
            title="Select reference subtitle file (SRT or MPL2 TXT)",
            filetypes=self._FILE_TYPES)
        if not path:
            return
        self.ref_var.set(path)
        self._load_times_from(path, "reference")
        self._load_preview(self.ref_text, path)

    def _load_preview(self, text_widget: tk.Text, path: str):
        try:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(path, encoding="cp1250") as f:
                    content = f.read()
            text_widget.config(state="normal")
            text_widget.delete("1.0", tk.END)
            text_widget.insert("1.0", content)
        except Exception as exc:
            messagebox.showerror("Preview error", str(exc))

    def _copy_selection(self):
        w = self._active_text
        if w is None:
            self.status_var.set("Click inside a panel first.")
            return
        try:
            sel = w.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(sel)
            self.status_var.set("Selection copied to clipboard.")
        except tk.TclError:
            self.status_var.set("No text selected.")

    def _delete_selected_lines(self):
        w = self._active_text
        if w is None:
            self.status_var.set("Click inside a panel first.")
            return
        try:
            start_idx = w.index(tk.SEL_FIRST)
            end_idx   = w.index(tk.SEL_LAST)
            start_line = int(start_idx.split(".")[0])
            end_line   = int(end_idx.split(".")[0])
            end_col    = int(end_idx.split(".")[1])
            if end_col == 0 and end_line > start_line:
                end_line -= 1
            w.delete(f"{start_line}.0", f"{end_line + 1}.0")
            self.status_var.set(
                f"Deleted lines {start_line}–{end_line}. Use 'Save' to persist.")
        except tk.TclError:
            self.status_var.set("No text selected.")

    def _save_panel(self, text_widget: tk.Text, path_var: tk.StringVar, label: str):
        path = path_var.get().strip()
        if not path:
            messagebox.showwarning("No file", f"No {label} file loaded.")
            return
        content = text_widget.get("1.0", tk.END)
        if content.endswith("\n"):
            content = content[:-1]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self.status_var.set(
                f"✓ {label.capitalize()} saved → {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Save error", str(exc))

    def _save_src(self):
        self._save_panel(self.src_text, self.src_var, "source")

    def _save_ref(self):
        self._save_panel(self.ref_text, self.ref_var, "reference")

    def _load_times_from(self, path: str, label: str):
        try:
            s_ms, e_ms = first_start_last_start(path)
            self.start_var.set(ms_to_time(s_ms))
            self.end_var.set(ms_to_time(e_ms))
            self.status_var.set(
                f"Times loaded from {label}: {ms_to_time(s_ms)} → {ms_to_time(e_ms)}")
        except Exception as exc:
            messagebox.showerror("Cannot read times", str(exc))

    def _update_out_preview(self):
        src = self.src_var.get().strip()
        if src:
            bkp = make_backup_path(src)
            self.out_var.set(
                f"{os.path.basename(src)}  (backup → {os.path.basename(bkp)})")
        else:
            self.out_var.set("(select source file first)")

    def _run(self):
        src = self.src_var.get().strip()
        if not src:
            messagebox.showwarning("No file", "Please select a source subtitle file.")
            return
        try:
            start_ms      = time_to_ms(self.start_var.get())
            last_start_ms = time_to_ms(self.end_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid time", str(exc))
            return
        if last_start_ms <= start_ms:
            messagebox.showerror("Invalid range",
                                 "Target last-subtitle start must be later than start time.")
            return
        try:
            blocks = parse_subtitle_file(src)
        except Exception as exc:
            messagebox.showerror("Parse error", str(exc))
            return
        if not blocks:
            messagebox.showerror("Empty file", "No subtitle blocks found.")
            return

        out_path = make_output_path(src)   # same as source
        bkp_path = make_backup_path(src)   # original_name.bkp.lang.srt

        # Back up original before overwriting
        try:
            shutil.copy2(src, bkp_path)
        except Exception as exc:
            messagebox.showerror("Backup error",
                                 f"Could not create backup:\n{exc}")
            return

        try:
            write_srt(blocks, start_ms, last_start_ms, out_path)
        except Exception as exc:
            messagebox.showerror("Write error", str(exc))
            return

        fmt = "MPL2→SRT" if src.lower().endswith(".txt") else "SRT"
        self.status_var.set(f"✓ Saved → {os.path.basename(out_path)}  |  backup → {os.path.basename(bkp_path)}")
        messagebox.showinfo(
            "Done",
            f"Synchronized {len(blocks)} subtitle blocks [{fmt}].\n\n"
            f"Output:  {out_path}\n"
            f"Backup:  {bkp_path}")


if __name__ == "__main__":
    App().mainloop()
