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

additions:
- Folder browser with automatic .pl.srt/.pl.txt → .en.srt pair detection
- Prev / Next navigation between file pairs
- Green tick (✓) validation for start/end times when source is SRT
- Mark start/end buttons moved into text panel header (alongside Top/End)
- Both text panels share equal height
- Synchronize button moved into the times section
- Red "Unsaved changes" warning when text was modified but not saved
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
            result.append(f"<i>{seg[1:]}</i>")
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
    return blocks[0][1], blocks[-1][1]


# ── SRT writing ───────────────────────────────────────────────────────────────

def write_srt(blocks, target_start_ms: int, target_last_start_ms: int, out_path: str):
    """
    Linearly remap timestamps so that:
      - blocks[0].start → target_start_ms
      - blocks[-1].start → target_last_start_ms
    The duration of every subtitle is preserved (scaled uniformly).
    """
    orig_start      = blocks[0][1]
    orig_last_start = blocks[-1][1]
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
    Build backup path by inserting '.bkp' before the language tag,
    preserving the original extension:
      movie.pl.srt -> movie.bkp.pl.srt
      movie.pl.txt -> movie.bkp.pl.txt
      movie.srt    -> movie.bkp.srt
      movie.txt    -> movie.bkp.txt
    """
    directory = os.path.dirname(src_path)
    basename  = os.path.basename(src_path)
    stem, ext = os.path.splitext(basename)   # "movie.pl", ".srt"/".txt"
    stem2, ext2 = os.path.splitext(stem)     # "movie",    ".pl"
    if ext2:
        bkp_name = f"{stem2}.bkp{ext2}{ext}"   # movie.bkp.pl.srt / .txt
    else:
        bkp_name = f"{stem}.bkp{ext}"           # movie.bkp.srt / .txt
    return os.path.join(directory, bkp_name)


# ── folder pair detection ─────────────────────────────────────────────────────

def find_pairs_in_folder(folder: str) -> list:
    """
    Scan *folder* and return a sorted list of (src_path, ref_path) tuples.

    Source  : *.pl.srt  or  *.pl.txt
    Reference: <same_stem>.en.srt

    Sorting is ascending by source filename.
    """
    try:
        all_files = os.listdir(folder)
    except Exception:
        return []

    files_lower_map = {f.lower(): f for f in all_files}
    pairs = []

    for fname in sorted(all_files):
        lower = fname.lower()
        stem = None
        if lower.endswith(".pl.srt"):
            stem = fname[:-7]          # strip ".pl.srt"
        elif lower.endswith(".pl.txt"):
            stem = fname[:-7]          # strip ".pl.txt"

        if stem is None:
            continue

        src_path = os.path.join(folder, fname)
        ref_name_lower = (stem + ".en.srt").lower()

        if ref_name_lower in files_lower_map:
            ref_path = os.path.join(folder, files_lower_map[ref_name_lower])
            pairs.append((src_path, ref_path))

    return pairs


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SRT Subtitle Synchronizer")
        self.resizable(True, True)
        self.minsize(980, 640)
        self._active_text: tk.Text | None = None
        self._folder_pairs: list = []
        self._pair_idx: int = -1
        self._loading: bool = False       # suppress unsaved-flag during file load
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = dict(padx=10, pady=5)

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

        # Row 0 – Source file
        ttk.Label(ctrl, text="Source file (SRT / MPL2 TXT):",
                  font=("", 10, "bold")).grid(row=0, column=0, sticky="w", **PAD)
        self.src_var = tk.StringVar()
        ttk.Entry(ctrl, textvariable=self.src_var, width=55).grid(
            row=0, column=1, sticky="ew", **PAD)
        ttk.Button(ctrl, text="Browse…", command=self._browse_src).grid(
            row=0, column=2, **PAD)

        # Row 1 – Reference file
        ttk.Label(ctrl, text="Reference SRT / TXT\n(optional – loads times):",
                  font=("", 10)).grid(row=1, column=0, sticky="w", **PAD)
        self.ref_var = tk.StringVar()
        ttk.Entry(ctrl, textvariable=self.ref_var, width=55).grid(
            row=1, column=1, sticky="ew", **PAD)
        ttk.Button(ctrl, text="Browse…", command=self._browse_ref).grid(
            row=1, column=2, **PAD)

        # Row 2 – Folder + navigation
        ttk.Label(ctrl, text="Folder (batch mode):",
                  font=("", 10)).grid(row=2, column=0, sticky="w", **PAD)

        folder_row = ttk.Frame(ctrl)
        folder_row.grid(row=2, column=1, columnspan=2, sticky="ew")
        # column 0 (entry) stretches; all other columns are fixed width
        folder_row.columnconfigure(0, weight=1)

        self.folder_var = tk.StringVar()
        ttk.Entry(folder_row, textvariable=self.folder_var).grid(
            row=0, column=0, sticky="ew", padx=(10, 4), pady=5)
        ttk.Button(folder_row, text="Browse folder…",
                   command=self._browse_folder).grid(
            row=0, column=1, padx=(0, 6), pady=5)

        # pair info label – between Browse and nav buttons, left-aligned inside
        # its cell; width is fixed so the buttons never shift
        self.pair_info_var = tk.StringVar(value="")
        ttk.Label(folder_row, textvariable=self.pair_info_var,
                  foreground="#555", font=("", 8, "italic"),
                  width=28, anchor="w").grid(
            row=0, column=2, padx=(2, 4), pady=5)

        # Nav buttons are in the last two columns – always at the right edge
        ttk.Button(folder_row, text="◀ Prev", width=8,
                   command=self._prev_pair).grid(
            row=0, column=3, padx=(0, 4), pady=5)
        ttk.Button(folder_row, text="Next ▶", width=8,
                   command=self._next_pair).grid(
            row=0, column=4, padx=(0, 6), pady=5)

        # Separator
        ttk.Separator(ctrl, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=4)

        # Row 4 – Start time  +  ✓  +  Mark start  |  Synchronize button  +  ⚠ warning
        ttk.Label(ctrl, text="Target start time\n(first subtitle appears):",
                  font=("", 10)).grid(row=4, column=0, sticky="w", **PAD)

        start_row = ttk.Frame(ctrl)
        start_row.grid(row=4, column=1, columnspan=2, sticky="ew")

        self.start_var = tk.StringVar(value="00:00:00,000")
        ttk.Entry(start_row, textvariable=self.start_var, width=18).pack(
            side="left", padx=(10, 4), pady=5)

        self.start_tick_var = tk.StringVar(value="")
        ttk.Label(start_row, textvariable=self.start_tick_var,
                  foreground="#1a9c1a", font=("", 13, "bold")).pack(
            side="left", padx=(0, 8))

        # Synchronize + unsaved warning – pinned to the right of start_row
        sync_frame = ttk.Frame(start_row)
        sync_frame.pack(side="right", padx=(0, 10))
        ttk.Button(sync_frame, text="⟳ Synchronize subtitles",
                   command=self._run, width=24).pack(side="left")
        self.unsaved_var = tk.StringVar(value="")
        ttk.Label(sync_frame, textvariable=self.unsaved_var,
                  foreground="red", font=("", 9, "bold")).pack(
            side="left", padx=(10, 0))

        # Row 5 – End time  +  ✓  +  Mark end  +  format hint
        ttk.Label(ctrl, text="Target time of last subtitle\n(last subtitle starts):",
                  font=("", 10)).grid(row=5, column=0, sticky="w", **PAD)

        end_row = ttk.Frame(ctrl)
        end_row.grid(row=5, column=1, columnspan=2, sticky="ew")

        self.end_var = tk.StringVar(value="00:00:00,000")
        ttk.Entry(end_row, textvariable=self.end_var, width=18).pack(
            side="left", padx=(10, 4), pady=5)

        self.end_tick_var = tk.StringVar(value="")
        ttk.Label(end_row, textvariable=self.end_tick_var,
                  foreground="#1a9c1a", font=("", 13, "bold")).pack(
            side="left", padx=(0, 8))

        ttk.Label(end_row, text="Format: HH:MM:SS,mmm",
                  foreground="gray", font=("", 8)).pack(side="left")

        # Row 6 – Output file
        ttk.Label(ctrl, text="Output file:", font=("", 10)).grid(
            row=6, column=0, sticky="w", **PAD)
        self.out_var = tk.StringVar(value="(select source file first)")
        out_label = ttk.Label(ctrl, textvariable=self.out_var,
                              foreground="#0055aa", justify="left")
        out_label.grid(row=6, column=1, columnspan=2, sticky="ew", **PAD)
        out_label.bind("<Configure>",
                       lambda e: out_label.config(wraplength=e.width - 4))

        ttk.Separator(ctrl, orient="horizontal").grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=4)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(ctrl, textvariable=self.status_var,
                  foreground="gray", font=("", 9)).grid(
            row=8, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 4))

        # Traces
        self.src_var.trace_add("write", lambda *_: (
            self._update_out_preview(), self._check_time_validity()))
        self.start_var.trace_add("write", lambda *_: self._check_time_validity())
        self.end_var.trace_add("write",   lambda *_: self._check_time_validity())

        # ── Preview panels ────────────────────────────────────────────────────
        preview = ttk.LabelFrame(outer, text="File Preview", padding=8)
        preview.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)

        self.src_text = self._make_text_panel(
            preview, column=0, label="◀ Source file", mark_buttons=False)
        self.ref_text = self._make_text_panel(
            preview, column=1, label="Reference file ▶", mark_buttons=True)

        for widget in (self.src_text, self.ref_text):
            widget.bind("<FocusIn>",  lambda e, w=widget: self._set_active(w))
            widget.bind("<Button-1>",
                        lambda e, w=widget: self.after(0, lambda: self._set_active(w)))

        # Unsaved-changes detection
        self.src_text.bind("<<Modified>>", self._on_text_modified)
        self.ref_text.bind("<<Modified>>", self._on_text_modified)

        # Bottom toolbar
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

    # ── Text panel factory ────────────────────────────────────────────────────

    def _make_text_panel(self, parent, column: int, label: str,
                         mark_buttons: bool = False) -> tk.Text:
        """
        Left panel  (column=0): only bold label, no header buttons.
        Right panel (column=1): Top/End (both panels) + Mark start/end.
        """
        f = ttk.Frame(parent)
        f.grid(row=0, column=column, sticky="nsew",
               padx=(0, 6) if column == 0 else (6, 0))
        f.rowconfigure(1, weight=1)
        f.columnconfigure(0, weight=1)

        # ── Header row ────────────────────────────────────────────────────────────────────
        hdr = ttk.Frame(f)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3))
        hdr.columnconfigure(0, weight=1)

        ttk.Label(hdr, text=label, font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w")

        if mark_buttons:
            btn_frame = ttk.Frame(hdr)
            btn_frame.grid(row=0, column=1, sticky="e")

            # Top/End scroll BOTH panels simultaneously
            ttk.Button(btn_frame, text="⬆ Top", width=7,
                       command=self._scroll_both_top
                       ).pack(side="left", padx=(0, 2))
            ttk.Button(btn_frame, text="⬇ End", width=7,
                       command=self._scroll_both_end
                       ).pack(side="left", padx=(0, 8))

            ttk.Separator(btn_frame, orient="vertical").pack(
                side="left", fill="y", padx=(0, 8))
            ttk.Button(btn_frame, text="⏱ Mark start", width=12,
                       command=self._mark_start_time).pack(side="left", padx=(0, 2))
            ttk.Button(btn_frame, text="⏱ Mark end", width=11,
                       command=self._mark_end_time).pack(side="left")
        # ── Text widget + scrollbars ──────────────────────────────────────────
        txt = tk.Text(f, wrap="none", width=48, height=22,
                      undo=True, font=("Monospace", 9),
                      relief="sunken", borderwidth=1,
                      selectbackground="#3399ff", selectforeground="white")
        scrolly = ttk.Scrollbar(f, orient="vertical",   command=txt.yview)
        scrollx = ttk.Scrollbar(f, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=scrolly.set, xscrollcommand=scrollx.set)
        txt.grid(row=1, column=0, sticky="nsew")
        scrolly.grid(row=1, column=1, sticky="ns")
        scrollx.grid(row=2, column=0, sticky="ew")

        return txt

    # ── Folder / pair navigation ──────────────────────────────────────────────

    def _browse_folder(self):
        folder = filedialog.askdirectory(
            title="Select folder containing subtitle pairs (.pl.srt / .en.srt)")
        if not folder:
            return
        self.folder_var.set(folder)
        self._folder_pairs = find_pairs_in_folder(folder)
        if not self._folder_pairs:
            messagebox.showinfo(
                "No pairs found",
                "No matching pairs found.\n\n"
                "Expected files like:\n"
                "  • movie.pl.srt  (or .pl.txt)  – source\n"
                "  • movie.en.srt                – reference")
            self._pair_idx = -1
            self.pair_info_var.set("No pairs found")
            return
        self._pair_idx = 0
        self._load_current_pair()

    def _load_current_pair(self):
        if self._pair_idx < 0 or self._pair_idx >= len(self._folder_pairs):
            return
        src, ref = self._folder_pairs[self._pair_idx]
        n = len(self._folder_pairs)
        self.pair_info_var.set(
            f"Pair {self._pair_idx + 1} / {n}  –  {os.path.basename(src)}")
        self.src_var.set(src)
        self.ref_var.set(ref)
        self._load_preview(self.src_text, src)
        self._load_preview(self.ref_text, ref)
        self._load_times_from(ref, "reference")
        self._clear_unsaved()
        self._check_time_validity()

    def _prev_pair(self):
        if not self._folder_pairs:
            self.status_var.set("No folder loaded.")
            return
        if self._pair_idx > 0:
            self._pair_idx -= 1
            self._load_current_pair()
        else:
            self.status_var.set("Already at the first pair.")

    def _next_pair(self):
        if not self._folder_pairs:
            self.status_var.set("No folder loaded.")
            return
        if self._pair_idx < len(self._folder_pairs) - 1:
            self._pair_idx += 1
            self._load_current_pair()
        else:
            self.status_var.set("Already at the last pair.")

    # ── Time validity check (green ticks) ────────────────────────────────────

    def _check_time_validity(self):
        """
        If the source file is SRT, compare its actual first/last start times
        against the current start_var / end_var values and show ✓ when they match.
        """
        self.start_tick_var.set("")
        self.end_tick_var.set("")
        src = self.src_var.get().strip()
        if not src or not src.lower().endswith(".srt"):
            return
        if not os.path.isfile(src):
            return
        try:
            first_ms, last_ms = first_start_last_start(src)
            if self.start_var.get().strip() == ms_to_time(first_ms):
                self.start_tick_var.set("✓")
            if self.end_var.get().strip() == ms_to_time(last_ms):
                self.end_tick_var.set("✓")
        except Exception:
            pass  # silently ignore parse errors here

    # ── Unsaved changes detection ─────────────────────────────────────────────

    def _on_text_modified(self, event):
        if self._loading:
            return
        widget = event.widget
        if widget.edit_modified():
            self.unsaved_var.set("⚠ Unsaved changes!")
            # Defer reset so next edit triggers the event again
            self.after(0, lambda: widget.edit_modified(False))

    def _clear_unsaved(self):
        self.unsaved_var.set("")
        self.after(0, lambda: (
            self.src_text.edit_modified(False),
            self.ref_text.edit_modified(False),
        ))

    # ── Mark times ────────────────────────────────────────────────────────────

    def _scroll_both_top(self):
        """Scroll both preview panels to the top."""
        self.src_text.see("1.0")
        self.ref_text.see("1.0")

    def _scroll_both_end(self):
        """Scroll both preview panels to the bottom."""
        self.src_text.see(tk.END)
        self.ref_text.see(tk.END)

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

    # ── File dialogs ──────────────────────────────────────────────────────────

    _FILE_TYPES = [
        ("Subtitle files",       "*.srt *.txt"),
        ("SubRip subtitles",     "*.srt"),
        ("MPL2 subtitles (TXT)", "*.txt"),
        ("All files",            "*.*"),
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

    # ── Preview helpers ───────────────────────────────────────────────────────

    def _load_preview(self, text_widget: tk.Text, path: str):
        self._loading = True
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
            text_widget.edit_modified(False)
        except Exception as exc:
            messagebox.showerror("Preview error", str(exc))
        finally:
            self._loading = False

    # ── Edit actions ──────────────────────────────────────────────────────────

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
            start_idx  = w.index(tk.SEL_FIRST)
            end_idx    = w.index(tk.SEL_LAST)
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
            self._clear_unsaved()
        except Exception as exc:
            messagebox.showerror("Save error", str(exc))

    def _save_src(self):
        self._save_panel(self.src_text, self.src_var, "source")

    def _save_ref(self):
        self._save_panel(self.ref_text, self.ref_var, "reference")

    # ── Times ────────────────────────────────────────────────────────────────

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

    # ── Synchronize ───────────────────────────────────────────────────────────

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
            messagebox.showerror(
                "Invalid range",
                "Target last-subtitle start must be later than target start time.")
            return
        try:
            blocks = parse_subtitle_file(src)
        except Exception as exc:
            messagebox.showerror("Parse error", str(exc))
            return
        if not blocks:
            messagebox.showerror("Empty file", "No subtitle blocks found.")
            return

        out_path = make_output_path(src)
        bkp_path = make_backup_path(src)

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
        self.status_var.set(
            f"✓ Saved → {os.path.basename(out_path)}  |  "
            f"backup → {os.path.basename(bkp_path)}")
        # Reload preview so content matches the file on disk
        self._load_preview(self.src_text, out_path)
        self._clear_unsaved()
        self._check_time_validity()
        messagebox.showinfo(
            "Done",
            f"Synchronized {len(blocks)} subtitle blocks [{fmt}].\n\n"
            f"Output:  {out_path}\n"
            f"Backup:  {bkp_path}")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().mainloop()
