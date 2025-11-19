#!/usr/bin/env python3
"""Media conversion GUI with comparison view."""
import io
import json
import os
import platform
import queue
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps, ImageTk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


@dataclass
class SourceItem:
    path: str
    kind: str  # "file" or "folder"


IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp", ".heic"}
VIDEO_FORMATS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".mts"}
SHIFT_MASK = 0x0001
IMAGE_FORMAT_CHOICES = sorted({fmt.lstrip(".") for fmt in IMAGE_FORMATS})
VIDEO_FORMAT_CHOICES = sorted({fmt.lstrip(".") for fmt in VIDEO_FORMATS})
RESOLUTION_CHOICES = ["480", "720", "1080", "1440", "2160", "4320"]

DEFAULT_VIDEO_ENCODER_ARGS = (
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "23",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
)

VIDEO_ENCODER_ARGS = {
    ".mp4": DEFAULT_VIDEO_ENCODER_ARGS + ("-movflags", "+faststart"),
    ".mov": DEFAULT_VIDEO_ENCODER_ARGS,
    ".mkv": DEFAULT_VIDEO_ENCODER_ARGS,
    ".avi": DEFAULT_VIDEO_ENCODER_ARGS,
    ".mts": DEFAULT_VIDEO_ENCODER_ARGS,
    ".flv": DEFAULT_VIDEO_ENCODER_ARGS,
    ".webm": (
        "-c:v",
        "libvpx-vp9",
        "-crf",
        "32",
        "-b:v",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "libopus",
        "-b:a",
        "128k",
    ),
    ".wmv": (
        "-c:v",
        "wmv2",
        "-b:v",
        "4M",
        "-c:a",
        "wmav2",
        "-b:a",
        "192k",
    ),
}


def format_file_size(num_bytes: Optional[int]) -> str:
    """Return a human-readable size string."""

    if num_bytes is None:
        return "Unknown"
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < step or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= step
    return f"{num_bytes} B"


def format_duration(seconds: Optional[float], *, allow_unknown: bool = True) -> str:
    """Format a duration in seconds into H:MM:SS."""

    if seconds is None or seconds <= 0:
        return "Unknown" if allow_unknown else "00:00"
    total_seconds = int(round(seconds))
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:d}:{sec:02d}"


class MediaConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Media Converter")
        root.geometry("1000x720")

        self.sources: List[SourceItem] = []
        self.mapping: List[Dict[str, str]] = []
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.target_folder: Optional[str] = None

        self.total_files_var = tk.IntVar(value=0)
        self.image_total_var = tk.IntVar(value=0)
        self.processed_images_var = tk.IntVar(value=0)
        self.image_skipped_var = tk.IntVar(value=0)
        self.video_total_var = tk.IntVar(value=0)
        self.processed_videos_var = tk.IntVar(value=0)
        self.video_skipped_var = tk.IntVar(value=0)
        self.other_files_var = tk.IntVar(value=0)
        self.total_processed_var = tk.IntVar(value=0)

        self.local_bin_dir = Path.home() / ".media_converter" / "bin"
        self.local_bin_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_path = self._find_binary("ffmpeg")
        self.ffprobe_path = self._find_binary("ffprobe")
        self._dependency_window: Optional[tk.Toplevel] = None
        self._dependency_status: Optional[tk.StringVar] = None
        self._dependency_button: Optional[ttk.Button] = None

        self.copy_other_files_var = tk.BooleanVar(value=False)
        self._control_widgets: List[tk.Widget] = []
        self._control_states: Dict[tk.Widget, str] = {}
        self._conversion_active = False

        self._build_ui()
        self._poll_log_queue()
        self.root.after(200, self._check_dependencies_on_start)

    # --- UI construction -------------------------------------------------
    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        source_target_frame = ttk.Frame(main)
        source_target_frame.pack(fill=tk.X)

        selection_frame = ttk.LabelFrame(source_target_frame, text="Sources", padding=10)
        selection_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 10))

        btn_frame = ttk.Frame(selection_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        add_folder_btn = ttk.Button(btn_frame, text="Add Folder", command=self.add_folder)
        add_folder_btn.pack(side=tk.LEFT)
        self._register_control(add_folder_btn)
        add_files_btn = ttk.Button(btn_frame, text="Add Files", command=self.add_files)
        add_files_btn.pack(side=tk.LEFT, padx=5)
        self._register_control(add_files_btn)
        remove_btn = ttk.Button(btn_frame, text="Remove Selected", command=self.remove_selected)
        remove_btn.pack(side=tk.LEFT)
        self._register_control(remove_btn)

        self.source_list = tk.Listbox(selection_frame, height=6)
        self.source_list.pack(fill=tk.BOTH, expand=True)

        target_frame = ttk.LabelFrame(source_target_frame, text="Target", padding=10)
        target_frame.grid(row=0, column=1, sticky=tk.NSEW)
        choose_target_btn = ttk.Button(target_frame, text="Choose Target Folder", command=self.choose_target_folder)
        choose_target_btn.pack(anchor=tk.W)
        self._register_control(choose_target_btn)
        self.target_label = ttk.Label(target_frame, text="No Folder Selected", foreground="gray", wraplength=280)
        self.target_label.pack(fill=tk.X, pady=(5, 0))

        source_target_frame.columnconfigure(0, weight=3)
        source_target_frame.columnconfigure(1, weight=2)
        source_target_frame.rowconfigure(0, weight=1)

        # Conversion options
        options_frame = ttk.Frame(main)
        options_frame.pack(fill=tk.X, pady=10)

        self.enable_image_var = tk.BooleanVar(value=True)
        self.enable_video_var = tk.BooleanVar(value=True)

        image_frame = ttk.LabelFrame(options_frame, text="Image Conversion", padding=10)
        image_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 10))
        image_check = ttk.Checkbutton(image_frame, text="Enable Image Conversion", variable=self.enable_image_var)
        image_check.grid(row=0, column=0, columnspan=2, sticky=tk.W)
        self._register_control(image_check)
        ttk.Label(image_frame, text="Original Format").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        image_source_options = ["All Images"] + [fmt.upper() for fmt in IMAGE_FORMAT_CHOICES]
        self.image_source_var = tk.StringVar(value=image_source_options[0])
        image_source_combo = ttk.Combobox(image_frame, textvariable=self.image_source_var, state="readonly", values=image_source_options)
        image_source_combo.grid(row=1, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        self._register_control(image_source_combo)
        ttk.Label(image_frame, text="Target Format").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        self.image_target_var = tk.StringVar(value="JPG")
        image_target_combo = ttk.Combobox(image_frame, textvariable=self.image_target_var, state="readonly", values=[fmt.upper() for fmt in IMAGE_FORMAT_CHOICES])
        image_target_combo.grid(row=2, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        self._register_control(image_target_combo)
        ttk.Label(image_frame, text="Target Long Side").grid(row=3, column=0, sticky=tk.W, pady=(5, 0))
        self.image_resolution_var = tk.StringVar(value=RESOLUTION_CHOICES[2])
        image_resolution_combo = ttk.Combobox(image_frame, textvariable=self.image_resolution_var, state="readonly", values=RESOLUTION_CHOICES)
        image_resolution_combo.grid(row=3, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        self._register_control(image_resolution_combo)
        for i in range(2):
            image_frame.columnconfigure(i, weight=1)

        video_frame = ttk.LabelFrame(options_frame, text="Video Conversion", padding=10)
        video_frame.grid(row=0, column=1, sticky=tk.NSEW)
        video_check = ttk.Checkbutton(video_frame, text="Enable Video Conversion", variable=self.enable_video_var)
        video_check.grid(row=0, column=0, columnspan=2, sticky=tk.W)
        self._register_control(video_check)
        ttk.Label(video_frame, text="Original Format").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        video_source_options = ["All Videos"] + [fmt.upper() for fmt in VIDEO_FORMAT_CHOICES]
        self.video_source_var = tk.StringVar(value=video_source_options[0])
        video_source_combo = ttk.Combobox(video_frame, textvariable=self.video_source_var, state="readonly", values=video_source_options)
        video_source_combo.grid(row=1, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        self._register_control(video_source_combo)
        ttk.Label(video_frame, text="Target Format").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        self.video_target_var = tk.StringVar(value="MP4")
        video_target_combo = ttk.Combobox(video_frame, textvariable=self.video_target_var, state="readonly", values=[fmt.upper() for fmt in VIDEO_FORMAT_CHOICES])
        video_target_combo.grid(row=2, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        self._register_control(video_target_combo)
        ttk.Label(video_frame, text="Target Long Side").grid(row=3, column=0, sticky=tk.W, pady=(5, 0))
        self.video_resolution_var = tk.StringVar(value=RESOLUTION_CHOICES[2])
        video_resolution_combo = ttk.Combobox(video_frame, textvariable=self.video_resolution_var, state="readonly", values=RESOLUTION_CHOICES)
        video_resolution_combo.grid(row=3, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        self._register_control(video_resolution_combo)
        for i in range(2):
            video_frame.columnconfigure(i, weight=1)

        options_frame.columnconfigure(0, weight=1)
        options_frame.columnconfigure(1, weight=1)

        self.small_file_action_var = tk.StringVar(value="Skip")
        small_other_container = ttk.Frame(main)
        small_other_container.pack(fill=tk.X, pady=5)

        small_frame = ttk.LabelFrame(
            small_other_container, text="Files That Are Smaller Than Long Side", padding=10
        )
        small_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 10))
        ttk.Label(small_frame, text="Action").grid(row=0, column=0, sticky=tk.W)
        small_action_combo = ttk.Combobox(
            small_frame,
            textvariable=self.small_file_action_var,
            state="readonly",
            values=["Skip", "Just Copy"],
        )
        small_action_combo.grid(row=0, column=1, sticky=tk.W, padx=(10, 0))
        self._register_control(small_action_combo)
        small_frame.columnconfigure(1, weight=1)

        other_frame = ttk.LabelFrame(small_other_container, text="Other Files", padding=10)
        other_frame.grid(row=0, column=1, sticky=tk.NSEW)
        other_check = ttk.Checkbutton(
            other_frame,
            text="Copy Non-Image And Non-Video Files",
            variable=self.copy_other_files_var,
        )
        other_check.pack(anchor=tk.W)
        self._register_control(other_check)

        small_other_container.columnconfigure(0, weight=1)
        small_other_container.columnconfigure(1, weight=1)

        # Action buttons
        action_frame = ttk.Frame(main)
        action_frame.pack(fill=tk.X, pady=5)
        self.convert_btn = ttk.Button(action_frame, text="Convert", command=self.start_conversion)
        self.convert_btn.pack(side=tk.LEFT)
        self._register_control(self.convert_btn)
        self.compare_btn = ttk.Button(
            action_frame, text="Open Comparison View", command=self.open_comparison, state=tk.DISABLED
        )
        self.compare_btn.pack(side=tk.LEFT, padx=10)
        self._register_control(self.compare_btn)
        load_mapping_btn = ttk.Button(action_frame, text="Load Mapping File", command=self.load_mapping_file)
        load_mapping_btn.pack(side=tk.LEFT)
        self._register_control(load_mapping_btn)
        ttk.Button(action_frame, text="Close", command=self.root.destroy).pack(side=tk.RIGHT)

        processing_frame = ttk.LabelFrame(main, text="Processing", padding=10)
        processing_frame.pack(fill=tk.X, pady=5)
        ttk.Label(processing_frame, text="Total Files").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(processing_frame, textvariable=self.total_files_var).grid(row=0, column=1, sticky=tk.W, padx=(5, 20))
        ttk.Label(processing_frame, text="Processed Image").grid(row=0, column=2, sticky=tk.W)
        ttk.Label(processing_frame, textvariable=self.image_total_var).grid(row=0, column=3, sticky=tk.W, padx=(5, 20))
        ttk.Label(processing_frame, text="Converted").grid(row=0, column=4, sticky=tk.W)
        ttk.Label(processing_frame, textvariable=self.processed_images_var).grid(
            row=0, column=5, sticky=tk.W, padx=(5, 20)
        )
        ttk.Label(processing_frame, text="Skipped/Just Copied").grid(row=0, column=6, sticky=tk.W)
        ttk.Label(processing_frame, textvariable=self.image_skipped_var).grid(
            row=0, column=7, sticky=tk.W, padx=(5, 0)
        )
        ttk.Label(processing_frame, text="Total Processed").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Label(processing_frame, textvariable=self.total_processed_var).grid(
            row=1, column=1, sticky=tk.W, padx=(5, 20), pady=(5, 0)
        )
        ttk.Label(processing_frame, text="Processed Video").grid(row=1, column=2, sticky=tk.W, pady=(5, 0))
        ttk.Label(processing_frame, textvariable=self.video_total_var).grid(
            row=1, column=3, sticky=tk.W, padx=(5, 20), pady=(5, 0)
        )
        ttk.Label(processing_frame, text="Converted").grid(row=1, column=4, sticky=tk.W, pady=(5, 0))
        ttk.Label(processing_frame, textvariable=self.processed_videos_var).grid(
            row=1, column=5, sticky=tk.W, padx=(5, 20), pady=(5, 0)
        )
        ttk.Label(processing_frame, text="Skipped/Just Copied").grid(row=1, column=6, sticky=tk.W, pady=(5, 0))
        ttk.Label(processing_frame, textvariable=self.video_skipped_var).grid(
            row=1, column=7, sticky=tk.W, padx=(5, 0), pady=(5, 0)
        )
        ttk.Label(processing_frame, text="Other Files Copied").grid(row=2, column=2, sticky=tk.W, pady=(5, 0))
        ttk.Label(processing_frame, textvariable=self.other_files_var).grid(
            row=2, column=3, sticky=tk.W, padx=(5, 0), pady=(5, 0)
        )
        for col in range(8):
            processing_frame.columnconfigure(col, weight=1)

        # Log window
        log_frame = ttk.LabelFrame(main, text="Messages", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_frame, height=18)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _register_control(self, widget: tk.Widget) -> None:
        self._control_widgets.append(widget)

    def _set_controls_enabled(self, enabled: bool) -> None:
        if enabled:
            for widget, previous in list(self._control_states.items()):
                try:
                    widget.configure(state=previous)
                except tk.TclError:
                    continue
            self._control_states.clear()
        else:
            self._control_states = {}
            for widget in self._control_widgets:
                try:
                    self._control_states[widget] = widget.cget("state")
                    widget.configure(state=tk.DISABLED)
                except tk.TclError:
                    continue

    def _update_compare_button_state(self) -> None:
        state = tk.NORMAL if self.mapping and not self._conversion_active else tk.DISABLED
        self.compare_btn.configure(state=state)

    # --- UI callbacks ----------------------------------------------------
    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select folder")
        if folder:
            self.sources.append(SourceItem(path=folder, kind="folder"))
            self.source_list.insert(tk.END, f"[Folder] {folder}")

    def add_files(self) -> None:
        files = filedialog.askopenfilenames(title="Select files")
        for file_path in files:
            self.sources.append(SourceItem(path=file_path, kind="file"))
            self.source_list.insert(tk.END, file_path)

    def remove_selected(self) -> None:
        selection = list(self.source_list.curselection())
        if not selection:
            return
        for index in reversed(selection):
            self.source_list.delete(index)
            self.sources.pop(index)

    def choose_target_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select target folder")
        if folder:
            self.target_folder = folder
            self.target_label.configure(text=folder, foreground="black")

    def start_conversion(self) -> None:
        if not self.sources:
            messagebox.showinfo("No sources", "Please add at least one folder or file.")
            return
        if not self.target_folder:
            messagebox.showinfo("No target", "Please choose a target folder.")
            return
        if not self.enable_image_var.get() and not self.enable_video_var.get():
            messagebox.showinfo("No media types", "Enable image and/or video conversion to continue.")
            return

        try:
            image_long_side = int(self.image_resolution_var.get())
            video_long_side = int(self.video_resolution_var.get())
        except ValueError:
            messagebox.showerror("Invalid resolution", "Please select a numeric long-side resolution.")
            return

        image_formats = self._selected_formats(self.image_source_var.get(), IMAGE_FORMATS, prefix="All") if self.enable_image_var.get() else []
        video_formats = self._selected_formats(self.video_source_var.get(), VIDEO_FORMATS, prefix="All") if self.enable_video_var.get() else []

        if self.enable_image_var.get() and not image_formats:
            messagebox.showerror("Invalid image format", "Choose at least one source image format.")
            return
        if self.enable_video_var.get() and not video_formats:
            messagebox.showerror("Invalid video format", "Choose at least one source video format.")
            return

        image_output = self._normalize_format(self.image_target_var.get())
        video_output = self._normalize_format(self.video_target_var.get())
        if self.enable_image_var.get() and not image_output:
            messagebox.showerror("Image format", "Select a target format for images.")
            return
        if self.enable_video_var.get() and not video_output:
            messagebox.showerror("Video format", "Select a target format for videos.")
            return

        job = {
            "image": {
                "enabled": self.enable_image_var.get(),
                "source_formats": image_formats,
                "output_format": image_output,
                "target_long_side": image_long_side,
            },
            "video": {
                "enabled": self.enable_video_var.get(),
                "source_formats": video_formats,
                "output_format": video_output,
                "target_long_side": video_long_side,
            },
            "small_file_action": self._small_file_action(),
            "copy_other_files": self.copy_other_files_var.get(),
        }
        if not self._ensure_required_tools(job):
            return
        self._conversion_active = True
        self._set_controls_enabled(False)
        try:
            threading.Thread(target=self._run_conversion, args=(job,), daemon=True).start()
        except Exception as exc:  # pylint: disable=broad-except
            self._conversion_active = False
            self._set_controls_enabled(True)
            messagebox.showerror("Conversion Error", f"Failed to start conversion: {exc}")

    def open_comparison(self) -> None:
        if not self.mapping:
            messagebox.showinfo("No mapping", "Run a conversion or load a mapping file first.")
            return
        ComparisonView(
            self.root,
            self.mapping,
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
        )

    def load_mapping_file(self) -> None:
        file_path = filedialog.askopenfilename(title="Select mapping file", filetypes=[("JSON", "*.json")])
        if not file_path:
            return
        try:
            data = json.loads(Path(file_path).read_text())
            entries = data.get("entries") or data
            if not isinstance(entries, list):
                raise ValueError("Invalid mapping format")
            self.mapping = [
                {"source": entry["source"], "dest": entry["dest"]}
                for entry in entries
                if "source" in entry and "dest" in entry
            ]
            self._update_compare_button_state()
            self._log(f"Loaded {len(self.mapping)} mapping entries from {file_path}")
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Failed", f"Could not load mapping file: {exc}")

    # --- Background tasks ------------------------------------------------
    def _check_dependencies_on_start(self) -> None:
        missing = self._missing_video_tools()
        if missing:
            message = (
                "Missing dependencies detected: "
                + ", ".join(missing)
                + ". Video conversions require these programs."
            )
            self._log(message)
            messagebox.showwarning("Missing program", message)
            self._prompt_dependency_install(missing)

    def _ensure_required_tools(self, job: Dict[str, object]) -> bool:
        video_cfg = job.get("video", {})
        if not isinstance(video_cfg, dict) or not video_cfg.get("enabled"):
            return True
        missing = self._missing_video_tools()
        if missing:
            self._log(
                "Missing dependencies: " + ", ".join(missing) + ". Please install them before converting videos."
            )
            self._prompt_dependency_install(missing)
            messagebox.showwarning(
                "Missing program",
                "The following program(s) are required for video conversion: " + ", ".join(missing),
            )
            return False
        return True

    def _missing_video_tools(self) -> List[str]:
        self.ffmpeg_path = self._find_binary("ffmpeg")
        self.ffprobe_path = self._find_binary("ffprobe")
        return [name for name, path in (("ffmpeg", self.ffmpeg_path), ("ffprobe", self.ffprobe_path)) if not path]

    def _prompt_dependency_install(self, missing: Sequence[str]) -> None:
        message = (
            "The following program(s) are required for video conversion: "
            + ", ".join(missing)
            + ". Click Download and Install to fetch the latest ffmpeg package."
        )
        if self._dependency_window and self._dependency_window.winfo_exists():
            if self._dependency_status:
                self._dependency_status.set(message)
            self._dependency_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.title("Install Required Program")
        window.geometry("420x220")
        ttk.Label(window, text="Missing Dependency", font=("TkDefaultFont", 12, "bold")).pack(pady=(10, 5))
        status = tk.StringVar(value=message)
        self._dependency_status = status
        ttk.Label(window, textvariable=status, wraplength=380).pack(padx=10)
        button = ttk.Button(window, text="Download and Install", command=self._start_dependency_install)
        button.pack(pady=10)
        self._dependency_button = button
        ttk.Button(window, text="Close", command=lambda: self._close_dependency_window(window)).pack(pady=(0, 10))
        window.transient(self.root)
        window.grab_set()
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_dependency_window(window))
        self._dependency_window = window

    def _close_dependency_window(self, window: tk.Toplevel) -> None:
        if window.winfo_exists():
            window.grab_release()
            window.destroy()
        self._dependency_window = None
        self._dependency_status = None
        self._dependency_button = None

    def _start_dependency_install(self) -> None:
        if not self._dependency_button:
            return
        self._dependency_button.configure(state=tk.DISABLED)
        threading.Thread(target=self._download_and_install_ffmpeg_suite, daemon=True).start()

    def _download_and_install_ffmpeg_suite(self) -> None:
        tmpdir: Optional[Path] = None
        try:
            info = self._ffmpeg_download_info()
            if not info:
                raise RuntimeError("Automatic installation is not supported on this platform.")
            tmpdir = Path(tempfile.mkdtemp(prefix="ffmpeg_dl_"))
            archive_path = tmpdir / f"ffmpeg_download{info['suffix']}"
            self._set_dependency_status("Downloading ffmpeg package...")
            urllib.request.urlretrieve(info["url"], archive_path)
            extract_dir = tmpdir / "extract"
            extract_dir.mkdir(exist_ok=True)
            self._set_dependency_status("Extracting package...")
            self._extract_archive(archive_path, extract_dir, info["archive"])
            binaries = self._collect_binaries(extract_dir)
            if not binaries:
                raise RuntimeError("Could not locate ffmpeg binaries in downloaded archive.")
            for _, src in binaries.items():
                dest = self.local_bin_dir / os.path.basename(src)
                shutil.copy2(src, dest)
                self._ensure_executable(dest)
            self._refresh_binary_paths()
            self._set_dependency_status(
                "Installation completed. Close this window and start conversion again."
            )
            self._log(f"Installed ffmpeg tools at {self.local_bin_dir}")
        except Exception as exc:  # pylint: disable=broad-except
            self._set_dependency_status(f"Installation failed: {exc}")
            if self._dependency_button:
                self.root.after(0, lambda: self._dependency_button.configure(state=tk.NORMAL))
        finally:
            if tmpdir and tmpdir.exists():
                shutil.rmtree(tmpdir, ignore_errors=True)

    def _ffmpeg_download_info(self) -> Optional[Dict[str, str]]:
        system = platform.system().lower()
        if system == "windows":
            return {
                "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
                "archive": "zip",
                "suffix": ".zip",
            }
        if system == "darwin":
            return {
                "url": "https://evermeet.cx/ffmpeg/getrelease/zip",
                "archive": "zip",
                "suffix": ".zip",
            }
        if system == "linux":
            return {
                "url": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
                "archive": "tar",
                "suffix": ".tar.xz",
            }
        return None

    def _extract_archive(self, archive_path: Path, extract_dir: Path, archive_type: str) -> None:
        if archive_type == "zip":
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(archive_path) as tf:
                tf.extractall(extract_dir)

    def _collect_binaries(self, folder: Path) -> Dict[str, Path]:
        names = {"ffmpeg", "ffprobe"}
        found: Dict[str, Path] = {}
        for root, _dirs, files in os.walk(folder):
            for filename in files:
                lower = filename.lower()
                for name in names:
                    if lower == name or lower == f"{name}.exe":
                        found[name] = Path(root) / filename
                if len(found) == len(names):
                    return found
        return found

    def _ensure_executable(self, path: Path) -> None:
        if os.name != "nt":
            mode = path.stat().st_mode
            path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _set_dependency_status(self, message: str) -> None:
        if self._dependency_status:
            self.root.after(0, lambda: self._dependency_status.set(message))

    def _refresh_binary_paths(self) -> None:
        self.ffmpeg_path = self._find_binary("ffmpeg")
        self.ffprobe_path = self._find_binary("ffprobe")

    def _run_conversion(self, job: Dict[str, object]) -> None:
        try:
            image_formats = job["image"]["source_formats"] if job["image"]["enabled"] else []
            video_formats = job["video"]["source_formats"] if job["video"]["enabled"] else []
            include_other = bool(job.get("copy_other_files"))
            media_files, other_files = self._gather_files(
                image_formats, video_formats, include_other=include_other
            )
            total_files = len(media_files) + len(other_files)
            self._update_processing_counts(
                total=total_files,
                image_converted=0,
                image_skipped=0,
                video_converted=0,
                video_skipped=0,
                other_copied=0,
            )
            if total_files == 0:
                self._log("No files matched the requested formats.")
                return
            mapping: List[Dict[str, str]] = []
            image_converted = 0
            video_converted = 0
            image_skipped = 0
            video_skipped = 0
            other_copied = 0
            small_action = job.get("small_file_action", "skip")
            for source, rel_path, media_type in media_files:
                config = job[media_type]
                dest_path = self._destination_path(rel_path, config["output_format"])
                try:
                    smaller = self._is_smaller_than_target(source, config["target_long_side"])
                    if smaller:
                        if small_action == "skip":
                            if media_type == "image":
                                image_skipped += 1
                            else:
                                video_skipped += 1
                            self._log(f"Skipping (smaller): {source}")
                            self._update_processing_counts(
                                total=total_files,
                                image_converted=image_converted,
                                image_skipped=image_skipped,
                                video_converted=video_converted,
                                video_skipped=video_skipped,
                                other_copied=other_copied,
                            )
                            continue
                        if small_action == "copy":
                            copy_dest = self._copy_destination_path(rel_path)
                            os.makedirs(os.path.dirname(copy_dest), exist_ok=True)
                            shutil.copy2(source, copy_dest)
                            mapping.append({"source": source, "dest": copy_dest})
                            self._log(f"Copied (smaller): {source} -> {copy_dest}")
                            if media_type == "image":
                                image_skipped += 1
                            else:
                                video_skipped += 1
                            self._update_processing_counts(
                                total=total_files,
                                image_converted=image_converted,
                                image_skipped=image_skipped,
                                video_converted=video_converted,
                                video_skipped=video_skipped,
                                other_copied=other_copied,
                            )
                            continue
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    self._convert_file(source, dest_path, media_type, config)
                    mapping.append({"source": source, "dest": dest_path})
                    self._log(f"Converted: {source} -> {dest_path}")
                    if media_type == "image":
                        image_converted += 1
                    else:
                        video_converted += 1
                    self._update_processing_counts(
                        total=total_files,
                        image_converted=image_converted,
                        image_skipped=image_skipped,
                        video_converted=video_converted,
                        video_skipped=video_skipped,
                        other_copied=other_copied,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    self._log(f"Failed to convert {source}: {exc}")
            if include_other and other_files:
                for source, rel_path in other_files:
                    try:
                        dest = self._copy_destination_path(rel_path)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        shutil.copy2(source, dest)
                        other_copied += 1
                        self._log(f"Copied other file: {source} -> {dest}")
                        self._update_processing_counts(
                            total=total_files,
                            image_converted=image_converted,
                            image_skipped=image_skipped,
                            video_converted=video_converted,
                            video_skipped=video_skipped,
                            other_copied=other_copied,
                        )
                    except Exception as exc:  # pylint: disable=broad-except
                        self._log(f"Failed to copy other file {source}: {exc}")
            if mapping:
                self.mapping = mapping
                self._save_mapping(mapping, job)
            skipped_or_copied = image_skipped + video_skipped
            converted_total = image_converted + video_converted
            self._log(
                "Done. Converted {converted_total} file(s). Skipped/Just Copied {skipped_or_copied}. Other files copied {other_copied}.".format(
                    converted_total=converted_total,
                    skipped_or_copied=skipped_or_copied,
                    other_copied=other_copied,
                )
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._log(f"Conversion failed: {exc}")
        finally:
            self._finish_conversion()

    def _finish_conversion(self) -> None:
        def restore() -> None:
            self._conversion_active = False
            self._set_controls_enabled(True)
            self._update_compare_button_state()

        self.root.after(0, restore)

    def _poll_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
        self.root.after(200, self._poll_log_queue)

    # --- Conversion helpers ----------------------------------------------
    def _gather_files(
        self,
        image_formats: Sequence[str],
        video_formats: Sequence[str],
        *,
        include_other: bool = False,
    ) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
        wanted_image = {self._normalize_format(fmt) for fmt in image_formats}
        wanted_video = {self._normalize_format(fmt) for fmt in video_formats}
        media_results: List[Tuple[str, str, str]] = []
        other_results: List[Tuple[str, str]] = []
        seen: set = set()
        for item in self.sources:
            if item.kind == "folder":
                root_name = os.path.basename(os.path.normpath(item.path)) or "source"
                for root, _, files in os.walk(item.path):
                    for filename in files:
                        ext = os.path.splitext(filename)[1].lower()
                        full_path = os.path.join(root, filename)
                        media_type = self._media_type_for_extension(ext, wanted_image, wanted_video)
                        if full_path in seen:
                            continue
                        rel_inside = os.path.relpath(full_path, item.path)
                        rel_path = os.path.join(root_name, rel_inside)
                        if media_type:
                            media_results.append((full_path, rel_path, media_type))
                            seen.add(full_path)
                        elif include_other:
                            other_results.append((full_path, rel_path))
                            seen.add(full_path)
            else:
                ext = os.path.splitext(item.path)[1].lower()
                media_type = self._media_type_for_extension(ext, wanted_image, wanted_video)
                if item.path in seen:
                    continue
                rel_path = os.path.basename(item.path)
                if media_type:
                    media_results.append((item.path, rel_path, media_type))
                    seen.add(item.path)
                elif include_other:
                    other_results.append((item.path, rel_path))
                    seen.add(item.path)
        return media_results, other_results

    def _find_binary(self, name: str) -> Optional[str]:
        candidate = shutil.which(name)
        if candidate:
            return candidate
        suffix = ".exe" if os.name == "nt" and not name.endswith(".exe") else ""
        local_path = self.local_bin_dir / f"{name}{suffix}"
        if local_path.exists():
            return str(local_path)
        alt_path = self.local_bin_dir / name
        return str(alt_path) if alt_path.exists() else None

    def _normalize_format(self, value: str) -> str:
        value = (value or "").strip().lower()
        if not value:
            return ""
        return value if value.startswith('.') else f'.{value}'

    def _selected_formats(self, selection: str, supported: Sequence[str], prefix: str) -> List[str]:
        selection = (selection or "").strip()
        if not selection:
            return []
        if prefix and selection.lower().startswith(prefix.lower()):
            return [fmt for fmt in supported]
        normalized = self._normalize_format(selection)
        return [normalized] if normalized else []

    def _media_type_for_extension(
        self, extension: str, image_formats: Sequence[str], video_formats: Sequence[str]
    ) -> Optional[str]:
        if extension in image_formats:
            return "image"
        if extension in video_formats:
            return "video"
        return None

    def _destination_path(self, relative_path: str, output_format: str) -> str:
        if not self.target_folder:
            raise RuntimeError("Target folder not set")
        rel_without_ext = os.path.splitext(relative_path)[0]
        extension = output_format if output_format.startswith('.') else f'.{output_format}'
        filename = f"{rel_without_ext}{extension}"
        return os.path.join(self.target_folder, filename)

    def _copy_destination_path(self, relative_path: str) -> str:
        if not self.target_folder:
            raise RuntimeError("Target folder not set")
        return os.path.join(self.target_folder, relative_path)

    def _is_smaller_than_target(self, path: str, target_long_side: int) -> bool:
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_FORMATS:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                width, height = img.size
        elif ext in VIDEO_FORMATS:
            width, height = self._probe_video_size(path)
        else:
            return False
        return max(width, height) < target_long_side

    def _probe_video_size(self, path: str) -> Tuple[int, int]:
        if not self.ffprobe_path:
            raise RuntimeError("ffprobe is not available.")
        cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            path,
        ]
        try:
            output = subprocess.check_output(cmd, text=True).strip()
            width_str, height_str = output.split("x")
            return int(width_str), int(height_str)
        except Exception as exc:  # pylint: disable=broad-except
            self._log(f"ffprobe failed for {path}: {exc}")
            return 0, 0

    def _convert_file(self, source: str, dest: str, media_type: str, config: Dict[str, object]) -> None:
        if media_type == "image":
            self._convert_image(source, dest, config)
        else:
            self._convert_with_ffmpeg(source, dest, config)

    def _convert_image(self, source: str, dest: str, config: Dict[str, object]) -> None:
        target_long_side = int(config["target_long_side"])
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            width, height = img.size
            if width >= height:
                new_width = target_long_side
                new_height = max(1, round(height * (target_long_side / width))) if width else height
            else:
                new_height = target_long_side
                new_width = max(1, round(width * (target_long_side / height))) if height else width
            resized = img.resize((max(1, new_width), max(1, new_height)), Image.LANCZOS)
            format_name = config["output_format"].lstrip('.').upper()
            if format_name == "JPG":
                format_name = "JPEG"
            resized.save(dest, format=format_name)

    def _convert_with_ffmpeg(self, source: str, dest: str, config: Dict[str, object]) -> None:
        if not self.ffmpeg_path:
            raise RuntimeError("ffmpeg is not available.")
        target_long_side = int(config["target_long_side"])
        scale_filter = (
            f"scale='if(gt(iw,ih),{target_long_side},-2)':'if(gt(iw,ih),-2,{target_long_side})'"
        )
        encoding_args = self._video_encoding_args(config.get("output_format"), dest)
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            source,
            "-vf",
            scale_filter,
        ]
        cmd.extend(encoding_args)
        cmd.append(dest)
        subprocess.run(cmd, check=True, capture_output=True)

    def _video_encoding_args(self, requested_format: Optional[str], dest: str) -> List[str]:
        ext = self._normalize_format(requested_format or os.path.splitext(dest)[1])
        preset = VIDEO_ENCODER_ARGS.get(ext)
        if not preset:
            preset = VIDEO_ENCODER_ARGS.get(".mp4", DEFAULT_VIDEO_ENCODER_ARGS)
        return list(preset)

    def _save_mapping(self, mapping: List[Dict[str, str]], job: Dict[str, object]) -> None:
        if not self.target_folder:
            return
        payload = {
            "created": time.time(),
            "target_folder": self.target_folder,
            "image_config": self._serialize_config(job["image"]),
            "video_config": self._serialize_config(job["video"]),
            "small_file_action": job["small_file_action"],
            "copy_other_files": job.get("copy_other_files", False),
            "entries": mapping,
        }
        path = Path(self.target_folder) / "conversion_map.json"
        path.write_text(json.dumps(payload, indent=2))
        self._log(f"Saved mapping file to {path}")

    def _serialize_config(self, config: Dict[str, object]) -> Dict[str, object]:
        return {
            "enabled": config.get("enabled", False),
            "source_formats": list(config.get("source_formats", [])),
            "output_format": config.get("output_format"),
            "target_long_side": config.get("target_long_side"),
        }

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def _update_processing_counts(
        self,
        *,
        total: Optional[int] = None,
        image_converted: Optional[int] = None,
        image_skipped: Optional[int] = None,
        video_converted: Optional[int] = None,
        video_skipped: Optional[int] = None,
        other_copied: Optional[int] = None,
    ) -> None:
        def update() -> None:
            if total is not None:
                self.total_files_var.set(total)
            if image_converted is not None:
                self.processed_images_var.set(image_converted)
            if image_skipped is not None:
                self.image_skipped_var.set(image_skipped)
            if video_converted is not None:
                self.processed_videos_var.set(video_converted)
            if video_skipped is not None:
                self.video_skipped_var.set(video_skipped)
            if other_copied is not None:
                self.other_files_var.set(other_copied)
            image_total = self.processed_images_var.get() + self.image_skipped_var.get()
            video_total = self.processed_videos_var.get() + self.video_skipped_var.get()
            self.image_total_var.set(image_total)
            self.video_total_var.set(video_total)
            total_processed = image_total + video_total + self.other_files_var.get()
            self.total_processed_var.set(total_processed)

        self.root.after(0, update)

    def _small_file_action(self) -> str:
        value = (self.small_file_action_var.get() or "").strip().lower()
        return "copy" if "copy" in value else "skip"


class ComparisonView(tk.Toplevel):
    def __init__(
        self,
        master: tk.Tk,
        mapping: Sequence[Dict[str, str]],
        *,
        ffmpeg_path: Optional[str] = None,
        ffprobe_path: Optional[str] = None,
    ):
        super().__init__(master)
        self.title("Comparison View")
        self.geometry("1200x650")
        self.mapping = list(mapping)
        self.current_index = 0
        self.offset_x = 0
        self.offset_y = 0
        self._drag_start: Optional[Tuple[int, int]] = None
        self._selection_start: Optional[Tuple[int, int]] = None
        self._selection_canvas: Optional[tk.Canvas] = None
        self._selection_rect: Optional[int] = None
        self.images: Dict[str, Optional[Image.Image]] = {"source": None, "dest": None}
        self.photo_images: Dict[str, Optional[ImageTk.PhotoImage]] = {"source": None, "dest": None}
        self.zoom_var = tk.DoubleVar(value=1.0)
        self._internal_zoom_update = False
        self._slider_value = self.zoom_var.get()
        self.scales: Dict[str, float] = {"source": 1.0, "dest": 1.0}
        self.fit_mode = False
        self.fit_scales: Dict[str, float] = {"source": 1.0, "dest": 1.0}
        self.base_dimensions: Dict[str, Tuple[int, int]] = {"source": (0, 0), "dest": (0, 0)}
        self.metadata: Dict[str, Dict[str, str]] = {"source": {}, "dest": {}}
        self.media_types: Dict[str, str] = {"source": "image", "dest": "image"}
        self.video_info: Dict[str, Dict[str, float]] = {}
        self.last_video_frame_time: Dict[str, Optional[float]] = {"source": None, "dest": None}
        self.playback_var = tk.DoubleVar(value=0.0)
        self.playback_duration = 0.0
        self._suppress_playback_callback = False
        self._playback_job: Optional[str] = None
        self._playback_active = False
        self.playback_fps = 5.0
        self.current_paths: Dict[str, str] = {"source": "", "dest": ""}
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg")
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe")

        self._build_ui()
        if self.mapping:
            self._load_pair(0)

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        paned = ttk.Panedwindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(paned)
        paned.add(list_frame, weight=1)
        ttk.Label(list_frame, text="Converted Files").pack(anchor=tk.W)
        listbox_container = ttk.Frame(list_frame)
        listbox_container.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.listbox = tk.Listbox(listbox_container, height=20, exportselection=False)
        self.listbox.grid(row=0, column=0, sticky=tk.NSEW)
        y_scroll = ttk.Scrollbar(listbox_container, orient=tk.VERTICAL, command=self.listbox.yview)
        y_scroll.grid(row=0, column=1, sticky=tk.NS)
        x_scroll = ttk.Scrollbar(listbox_container, orient=tk.HORIZONTAL, command=self.listbox.xview)
        x_scroll.grid(row=1, column=0, sticky=tk.EW)
        listbox_container.rowconfigure(0, weight=1)
        listbox_container.columnconfigure(0, weight=1)
        self.listbox.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        for entry in self.mapping:
            self.listbox.insert(tk.END, entry.get("source", ""))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        viewer_frame = ttk.Frame(paned)
        paned.add(viewer_frame, weight=4)

        canvas_frame = ttk.Frame(viewer_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(canvas_frame)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        right_panel = ttk.Frame(canvas_frame)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        self.left_canvas = tk.Canvas(left_panel, width=500, height=500, background="black")
        self.left_canvas.pack(fill=tk.BOTH, expand=True)
        self.left_info_label = ttk.Label(left_panel, text="", anchor=tk.W, justify=tk.LEFT, wraplength=400)
        self.left_info_label.pack(fill=tk.X, pady=(5, 0))

        self.right_canvas = tk.Canvas(right_panel, width=500, height=500, background="black")
        self.right_canvas.pack(fill=tk.BOTH, expand=True)
        self.right_info_label = ttk.Label(right_panel, text="", anchor=tk.W, justify=tk.LEFT, wraplength=400)
        self.right_info_label.pack(fill=tk.X, pady=(5, 0))

        for canvas in (self.left_canvas, self.right_canvas):
            canvas.bind("<ButtonPress-1>", self._on_drag_start)
            canvas.bind("<B1-Motion>", self._on_drag)
            canvas.bind("<ButtonRelease-1>", self._on_drag_end)
            canvas.bind("<Double-Button-1>", self._on_double_click)
            canvas.bind("<Configure>", self._on_canvas_configure)

        controls = ttk.Frame(viewer_frame)
        controls.pack(fill=tk.X, pady=5)
        ttk.Button(controls, text="Previous", command=self.show_previous).pack(side=tk.LEFT)
        ttk.Button(controls, text="Next", command=self.show_next).pack(side=tk.LEFT, padx=5)
        ttk.Label(controls, text="Zoom").pack(side=tk.LEFT, padx=(20, 5))
        ttk.Scale(
            controls,
            from_=0.25,
            to=4.0,
            orient=tk.HORIZONTAL,
            variable=self.zoom_var,
            command=self._on_zoom_change,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(controls, text="1:1", command=self._set_actual_size).pack(side=tk.LEFT, padx=5)
        ttk.Button(controls, text="Fit to Window", command=self._fit_to_window).pack(side=tk.LEFT)
        ttk.Button(controls, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        playback = ttk.Frame(viewer_frame)
        playback.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(playback, text="Playback").pack(side=tk.LEFT)
        self.play_button = ttk.Button(playback, text="Play", command=self._start_playback, state=tk.DISABLED)
        self.play_button.pack(side=tk.LEFT, padx=(10, 0))
        self.pause_button = ttk.Button(playback, text="Pause", command=self._pause_playback, state=tk.DISABLED)
        self.pause_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(playback, text="Stop", command=self._stop_playback, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT)
        self.playback_scale = ttk.Scale(
            playback,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            variable=self.playback_var,
            command=self._on_playback_scrub,
        )
        self.playback_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        self.playback_scale.state(["disabled"])
        self.playback_label = ttk.Label(playback, text="00:00 / 00:00")
        self.playback_label.pack(side=tk.LEFT)

    def _load_pair(self, index: int) -> None:
        if not (0 <= index < len(self.mapping)):
            return
        self.current_index = index
        entry = self.mapping[index]
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.see(index)
        self.offset_x = 0
        self.offset_y = 0
        self.fit_mode = False
        self.fit_scales = {"source": 1.0, "dest": 1.0}
        self.scales = {"source": 1.0, "dest": 1.0}
        self.base_dimensions = {"source": (0, 0), "dest": (0, 0)}
        self.current_paths = {"source": entry["source"], "dest": entry["dest"]}
        self.video_info.clear()
        self.media_types = {"source": "image", "dest": "image"}
        self.last_video_frame_time = {"source": None, "dest": None}
        self.playback_duration = 0.0
        self._cancel_playback()
        self._set_playback_time(0.0, update_frames=False)
        for key, path in self.current_paths.items():
            canvas_size = self._get_canvas_size_for_key(key)
            ext = os.path.splitext(path)[1].lower()
            if ext in VIDEO_FORMATS:
                self.media_types[key] = "video"
                info = self._probe_video_info(path)
                if info:
                    self.video_info[key] = info
                    duration = info.get("duration") or 0.0
                    self.playback_duration = max(self.playback_duration, duration)
                else:
                    duration = None
                self.images[key] = self._load_image(path, timestamp=0.0, target_size=canvas_size)
                width = info.get("width") if info else None
                height = info.get("height") if info else None
                dimensions = (int(width), int(height)) if width and height else None
                if dimensions:
                    self.base_dimensions[key] = dimensions
                elif self.images[key] is not None:
                    self.base_dimensions[key] = self.images[key].size
                else:
                    self.base_dimensions[key] = (0, 0)
                self.metadata[key] = self._build_metadata(
                    path,
                    self.images[key],
                    info.get("duration") if info else None,
                    video_dimensions=dimensions,
                )
            else:
                self.images[key] = self._load_image(path)
                if self.images[key] is not None:
                    self.base_dimensions[key] = self.images[key].size
                else:
                    self.base_dimensions[key] = (0, 0)
                self.metadata[key] = self._build_metadata(path, self.images[key])
        self._update_metadata_labels()
        self._configure_playback_controls()
        self._fit_to_window()

    def _build_metadata(
        self,
        path: str,
        img: Optional[Image.Image],
        video_duration: Optional[float] = None,
        video_dimensions: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, str]:
        if video_dimensions and all(video_dimensions):
            resolution = f"{video_dimensions[0]}x{video_dimensions[1]}"
        elif img:
            resolution = f"{img.width}x{img.height}"
        else:
            resolution = "N/A"
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = None
        meta = {
            "path": path,
            "resolution": resolution,
            "size": format_file_size(size_bytes),
        }
        if video_duration:
            meta["duration"] = format_duration(video_duration)
        return meta

    def _format_metadata_text(self, meta: Dict[str, str]) -> str:
        if not meta:
            return ""
        base = f"{meta.get('resolution', 'N/A')} | {meta.get('size', 'Unknown')}"
        duration = meta.get("duration")
        if duration and duration != "Unknown":
            base = f"{base} | {duration}"
        return f"{meta.get('path', '')}\n{base}"

    def _update_metadata_labels(self) -> None:
        self.left_info_label.config(text=self._format_metadata_text(self.metadata.get("source", {})))
        self.right_info_label.config(text=self._format_metadata_text(self.metadata.get("dest", {})))

    def _configure_playback_controls(self) -> None:
        has_video = self._has_video()
        if not has_video or self.playback_duration <= 0:
            self._enable_playback_controls(False)
            self.playback_label.config(text="00:00 / 00:00")
            self.playback_scale.configure(from_=0.0, to=1.0)
            return
        self.playback_scale.configure(from_=0.0, to=self.playback_duration)
        self._enable_playback_controls(True)
        self._set_playback_time(0.0, update_frames=False)
        self._update_playback_label()

    def _enable_playback_controls(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in (self.play_button, self.pause_button, self.stop_button):
            widget.config(state=state)
        if enabled:
            self.playback_scale.state(["!disabled"])
        else:
            self.playback_scale.state(["disabled"])

    def _update_playback_label(self) -> None:
        current = format_duration(self.playback_var.get(), allow_unknown=False)
        total = format_duration(self.playback_duration, allow_unknown=False)
        self.playback_label.config(text=f"{current} / {total}")

    def _set_playback_time(self, value: float, *, update_frames: bool = True) -> None:
        if not self._has_video():
            self.playback_var.set(0.0)
            self._update_playback_label()
            return
        max_value = self.playback_duration if self.playback_duration > 0 else value
        clamped = max(0.0, min(value, max_value))
        self._suppress_playback_callback = True
        self.playback_var.set(clamped)
        self._suppress_playback_callback = False
        self._update_playback_label()
        if update_frames:
            self._update_video_frames(clamped)

    def _start_playback(self) -> None:
        if not self._has_video() or self.playback_duration <= 0:
            return
        if self._playback_active:
            return
        self._playback_active = True
        self._advance_playback()

    def _pause_playback(self) -> None:
        self._playback_active = False
        self._cancel_playback()

    def _stop_playback(self) -> None:
        self._pause_playback()
        self._set_playback_time(0.0)

    def _advance_playback(self) -> None:
        if not self._playback_active:
            return
        step = 1.0 / self.playback_fps if self.playback_fps > 0 else 0.2
        new_time = self.playback_var.get() + step
        if new_time >= self.playback_duration:
            self._set_playback_time(self.playback_duration)
            self._playback_active = False
            return
        self._set_playback_time(new_time)
        interval = int(max(1, round(1000 / self.playback_fps)))
        self._playback_job = self.after(interval, self._advance_playback)

    def _on_playback_scrub(self, value: str) -> None:
        if self._suppress_playback_callback or not self._has_video():
            return
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return
        self._pause_playback()
        self._set_playback_time(timestamp)

    def _update_video_frames(self, timestamp: float) -> None:
        updated = False
        for key, media_type in self.media_types.items():
            if media_type != "video":
                continue
            path = self.current_paths.get(key)
            if not path:
                continue
            info = self.video_info.get(key, {})
            duration = info.get("duration")
            target_time = timestamp
            if duration is not None:
                target_time = max(0.0, min(timestamp, duration))
            last_time = self.last_video_frame_time.get(key)
            if last_time is not None and abs(last_time - target_time) < 1e-3:
                continue
            frame = self._extract_video_frame(
                path,
                target_time,
                target_size=self._get_canvas_size_for_key(key),
            )
            if frame is None:
                continue
            self.images[key] = frame
            self.last_video_frame_time[key] = target_time
            updated = True
        if updated:
            self._render()

    def _has_video(self) -> bool:
        return any(media_type == "video" for media_type in self.media_types.values())

    def _cancel_playback(self) -> None:
        if self._playback_job is not None:
            try:
                self.after_cancel(self._playback_job)
            except Exception:  # pylint: disable=broad-except
                pass
        self._playback_job = None
        self._playback_active = False

    def destroy(self) -> None:  # type: ignore[override]
        self._cancel_playback()
        super().destroy()

    def _probe_video_info(self, path: str) -> Dict[str, float]:
        ffprobe = self.ffprobe_path or shutil.which("ffprobe")
        if not ffprobe:
            return {}
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,r_frame_rate:format=duration",
            "-of",
            "json",
            path,
        ]
        try:
            result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            payload = result.stdout.decode("utf-8") if result.stdout else "{}"
            data = json.loads(payload)
        except Exception:  # pylint: disable=broad-except
            return {}
        info: Dict[str, float] = {}
        streams = data.get("streams") or []
        if streams:
            stream = streams[0]
            width = stream.get("width")
            height = stream.get("height")
            if isinstance(width, int):
                info["width"] = float(width)
            if isinstance(height, int):
                info["height"] = float(height)
            duration = stream.get("duration")
            if duration:
                try:
                    info["duration"] = float(duration)
                except (TypeError, ValueError):
                    info["duration"] = 0.0
            fps_value = stream.get("r_frame_rate")
            if isinstance(fps_value, str) and "/" in fps_value:
                num, denom = fps_value.split("/", maxsplit=1)
                try:
                    fps = float(num) / float(denom)
                    if fps > 0:
                        info["fps"] = fps
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        format_section = data.get("format")
        if format_section and not info.get("duration"):
            duration = format_section.get("duration")
            if duration:
                try:
                    info["duration"] = float(duration)
                except (TypeError, ValueError):
                    info["duration"] = 0.0
        return info

    def _load_image(
        self,
        path: str,
        timestamp: float = 0.0,
        target_size: Optional[Tuple[int, int]] = None,
    ) -> Optional[Image.Image]:
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_FORMATS:
            try:
                with Image.open(path) as img:
                    return img.convert("RGB")
            except Exception as exc:  # pylint: disable=broad-except
                messagebox.showerror("Preview", f"Could not open {path}: {exc}")
                return None
        if ext in VIDEO_FORMATS:
            return self._extract_video_frame(path, timestamp, target_size=target_size)
        messagebox.showinfo("Preview", f"Cannot preview this file type: {path}")
        return None

    def _extract_video_frame(
        self,
        path: str,
        timestamp: float = 0.0,
        target_size: Optional[Tuple[int, int]] = None,
    ) -> Optional[Image.Image]:
        ffmpeg = self.ffmpeg_path or shutil.which("ffmpeg")
        if not ffmpeg:
            messagebox.showinfo(
                "Preview",
                "Video preview requires ffmpeg. Please install it to compare video files.",
            )
            return None
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
        ]
        if timestamp > 0:
            cmd.extend(["-ss", f"{timestamp:.3f}"])
        cmd.extend(["-i", path])
        if target_size:
            width = max(1, int(target_size[0]))
            height = max(1, int(target_size[1]))
            cmd.extend(
                [
                    "-vf",
                    f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease",
                ]
            )
        cmd.extend(
            [
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                "2",
                "-",
            ]
        )
        try:
            result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if not result.stdout:
                raise RuntimeError("ffmpeg did not return frame data")
            with Image.open(io.BytesIO(result.stdout)) as img:
                return img.convert("RGB")
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Preview", f"Could not preview video {path}: {exc}")
            return None

    def _get_canvas_size_for_key(self, key: str) -> Tuple[int, int]:
        canvas = self.left_canvas if key == "source" else self.right_canvas
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            return (800, 600)
        return (width, height)

    def _render(self) -> None:
        for canvas, key in ((self.left_canvas, "source"), (self.right_canvas, "dest")):
            canvas.delete("all")
            img = self.images[key]
            if img is None:
                canvas.create_text(canvas.winfo_width() / 2, canvas.winfo_height() / 2, fill="white", text="Preview Not Available")
                continue
            scale = self.fit_scales[key] if self.fit_mode else self.scales.get(key, self.zoom_var.get())
            base_width, base_height = self.base_dimensions.get(key, img.size)
            base_width = base_width or img.width
            base_height = base_height or img.height
            width = int(base_width * scale)
            height = int(base_height * scale)
            resized = img.resize((max(1, width), max(1, height)), Image.LANCZOS)
            photo = ImageTk.PhotoImage(resized)
            self.photo_images[key] = photo
            center_x = canvas.winfo_width() / 2 + self.offset_x
            center_y = canvas.winfo_height() / 2 + self.offset_y
            canvas.create_image(center_x, center_y, image=photo)

    def _on_select(self, _event: tk.Event) -> None:  # type: ignore[override]
        if not self.listbox.curselection():
            return
        index = self.listbox.curselection()[0]
        self._load_pair(index)

    def show_previous(self) -> None:
        new_index = (self.current_index - 1) % len(self.mapping)
        self._load_pair(new_index)

    def show_next(self) -> None:
        new_index = (self.current_index + 1) % len(self.mapping)
        self._load_pair(new_index)

    def _on_zoom_change(self, _value: str = "") -> None:
        if self._internal_zoom_update:
            return
        new_value = self._clamp_scale(self.zoom_var.get())
        old_value = self._slider_value if self._slider_value else new_value
        if new_value == old_value:
            return
        if self.fit_mode:
            self.scales = dict(self.fit_scales)
            self.fit_mode = False
        ratio = new_value / old_value if old_value else 1.0
        for key in self.scales:
            self.scales[key] = self._clamp_scale(self.scales[key] * ratio)
        self._slider_value = new_value
        self._render()

    def _on_drag_start(self, event: tk.Event) -> None:  # type: ignore[override]
        canvas = event.widget
        if isinstance(canvas, tk.Canvas) and self._is_shift_pressed(event):
            self._start_selection(canvas, event.x, event.y)
            return
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:  # type: ignore[override]
        canvas = event.widget
        if isinstance(canvas, tk.Canvas) and self._selection_canvas is canvas and self._selection_start:
            self._update_selection(canvas, event.x, event.y)
            return
        if not self._drag_start:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self.offset_x += dx
        self.offset_y += dy
        self._drag_start = (event.x, event.y)
        self._render()

    def _on_drag_end(self, event: tk.Event) -> None:  # type: ignore[override]
        canvas = event.widget
        if self._selection_canvas is not None:
            if canvas is self._selection_canvas and isinstance(canvas, tk.Canvas):
                self._finish_selection(canvas, event.x, event.y)
            else:
                self._clear_selection_overlay()
                self._render()
            return
        self._drag_start = None

    def _set_actual_size(self) -> None:
        self.fit_mode = False
        self.scales = {"source": 1.0, "dest": 1.0}
        self._set_zoom_value(1.0)
        self._render()

    def _fit_to_window(self) -> None:
        self.offset_x = 0
        self.offset_y = 0
        self.update_idletasks()
        scales = self._compute_fit_scales()
        if not scales:
            self.fit_mode = False
            self._render()
            return
        self.fit_scales = scales
        self.fit_mode = True
        # Keep the shared slider roughly in sync by using the smallest fit scale.
        self._set_zoom_value(min(scales.values()))
        self._render()

    def _on_double_click(self, event: tk.Event) -> None:  # type: ignore[override]
        canvas = event.widget
        if isinstance(canvas, tk.Canvas):
            self._apply_zoom(canvas, event.x, event.y, 1.10)

    def _start_selection(self, canvas: tk.Canvas, x: int, y: int) -> None:
        self._clear_selection_overlay()
        self._selection_canvas = canvas
        self._selection_start = (x, y)
        self._selection_rect = canvas.create_rectangle(x, y, x, y, outline="white", dash=(4, 2))

    def _update_selection(self, canvas: tk.Canvas, x: int, y: int) -> None:
        if self._selection_rect is None or self._selection_start is None:
            return
        canvas.coords(self._selection_rect, self._selection_start[0], self._selection_start[1], x, y)

    def _finish_selection(self, canvas: tk.Canvas, x: int, y: int) -> None:
        if self._selection_start is None:
            self._clear_selection_overlay()
            return
        start_x, start_y = self._selection_start
        width = abs(x - start_x)
        height = abs(y - start_y)
        self._clear_selection_overlay()
        if width < 5 or height < 5:
            self._render()
            return
        canvas_width = max(canvas.winfo_width(), 1)
        canvas_height = max(canvas.winfo_height(), 1)
        ratio_w = canvas_width / width if width else 1.0
        ratio_h = canvas_height / height if height else 1.0
        factor = max(1.0, min(ratio_w, ratio_h))
        center_x = (start_x + x) / 2
        center_y = (start_y + y) / 2
        self._apply_zoom(canvas, center_x, center_y, factor)

    def _clear_selection_overlay(self) -> None:
        if self._selection_canvas is not None and self._selection_rect is not None:
            self._selection_canvas.delete(self._selection_rect)
        self._selection_rect = None
        self._selection_start = None
        self._selection_canvas = None

    def _apply_zoom(self, canvas: tk.Canvas, x: float, y: float, factor: float) -> None:
        key = self._canvas_key(canvas)
        if key is None:
            return
        if self.fit_mode:
            self.scales = dict(self.fit_scales)
            self.fit_mode = False
        current_scale = self.scales.get(key, 1.0)
        if current_scale <= 0:
            return
        target_scale = self._clamp_scale(current_scale * factor)
        ratio = target_scale / current_scale if current_scale else 1.0
        canvas_width = max(canvas.winfo_width(), 1)
        canvas_height = max(canvas.winfo_height(), 1)
        center_x = canvas_width / 2
        center_y = canvas_height / 2
        if ratio == 1.0:
            self.offset_x += center_x - x
            self.offset_y += center_y - y
            self._render()
            return
        delta_x = x - center_x - self.offset_x
        delta_y = y - center_y - self.offset_y
        self.offset_x = -(delta_x) * ratio
        self.offset_y = -(delta_y) * ratio
        for item in self.scales:
            self.scales[item] = self._clamp_scale(self.scales[item] * ratio)
        self._set_zoom_value(self._clamp_scale(self._slider_value * ratio))
        self._render()

    def _set_zoom_value(self, value: float) -> None:
        self._internal_zoom_update = True
        self.zoom_var.set(value)
        self._slider_value = value
        self._internal_zoom_update = False

    def _on_canvas_configure(self, _event: tk.Event) -> None:  # type: ignore[override]
        if self.fit_mode:
            scales = self._compute_fit_scales()
            if scales:
                self.fit_scales = scales
        self._render()

    def _compute_fit_scales(self) -> Dict[str, float]:
        scales: Dict[str, float] = {}
        for canvas, key in ((self.left_canvas, "source"), (self.right_canvas, "dest")):
            img = self.images.get(key)
            base_width, base_height = self.base_dimensions.get(key, (0, 0))
            if img is None and (base_width == 0 or base_height == 0):
                continue
            if base_width <= 0 or base_height <= 0:
                if img is None or img.width == 0 or img.height == 0:
                    continue
                base_width, base_height = img.width, img.height
            if base_width == 0 or base_height == 0:
                continue
            canvas_width = max(canvas.winfo_width(), 1)
            canvas_height = max(canvas.winfo_height(), 1)
            scale_w = canvas_width / base_width
            scale_h = canvas_height / base_height
            scales[key] = max(0.25, min(scale_w, scale_h, 4.0))
        if not scales:
            return {}
        # Ensure both keys exist so rendering logic has consistent values.
        first = next(iter(scales.values()))
        for key in ("source", "dest"):
            scales.setdefault(key, first)
        return scales

    def _clamp_scale(self, scale: float) -> float:
        return max(0.25, min(scale, 4.0))

    def _is_shift_pressed(self, event: tk.Event) -> bool:  # type: ignore[override]
        state = getattr(event, "state", 0)
        try:
            state_int = int(state)
        except (TypeError, ValueError):
            state_int = 0
        return bool(state_int & SHIFT_MASK)

    def _canvas_key(self, canvas: tk.Canvas) -> Optional[str]:
        if canvas is self.left_canvas:
            return "source"
        if canvas is self.right_canvas:
            return "dest"
        return None


def main() -> None:
    root = tk.Tk()
    app = MediaConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
