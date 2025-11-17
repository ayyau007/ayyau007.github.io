#!/usr/bin/env python3
"""Media conversion GUI with comparison view."""
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
VIDEO_FORMATS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}
SHIFT_MASK = 0x0001
IMAGE_FORMAT_CHOICES = sorted({fmt.lstrip(".") for fmt in IMAGE_FORMATS})
VIDEO_FORMAT_CHOICES = sorted({fmt.lstrip(".") for fmt in VIDEO_FORMATS})
RESOLUTION_CHOICES = ["480", "720", "1080", "1440", "2160", "4320"]


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
        self.processed_images_var = tk.IntVar(value=0)
        self.processed_videos_var = tk.IntVar(value=0)
        self.total_processed_var = tk.IntVar(value=0)

        self.local_bin_dir = Path.home() / ".media_converter" / "bin"
        self.local_bin_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_path = self._find_binary("ffmpeg")
        self.ffprobe_path = self._find_binary("ffprobe")
        self._dependency_window: Optional[tk.Toplevel] = None
        self._dependency_status: Optional[tk.StringVar] = None
        self._dependency_button: Optional[ttk.Button] = None

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
        ttk.Button(btn_frame, text="Add Folder", command=self.add_folder).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Add Files", command=self.add_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove Selected", command=self.remove_selected).pack(side=tk.LEFT)

        self.source_list = tk.Listbox(selection_frame, height=6)
        self.source_list.pack(fill=tk.BOTH, expand=True)

        target_frame = ttk.LabelFrame(source_target_frame, text="Target", padding=10)
        target_frame.grid(row=0, column=1, sticky=tk.NSEW)
        ttk.Button(target_frame, text="Choose target folder", command=self.choose_target_folder).pack(anchor=tk.W)
        self.target_label = ttk.Label(target_frame, text="No folder selected", foreground="gray", wraplength=280)
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
        ttk.Checkbutton(image_frame, text="Enable image conversion", variable=self.enable_image_var).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(image_frame, text="Original format").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        image_source_options = ["All Images"] + [fmt.upper() for fmt in IMAGE_FORMAT_CHOICES]
        self.image_source_var = tk.StringVar(value=image_source_options[0])
        ttk.Combobox(image_frame, textvariable=self.image_source_var, state="readonly", values=image_source_options).grid(row=1, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        ttk.Label(image_frame, text="Target format").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        self.image_target_var = tk.StringVar(value="JPG")
        ttk.Combobox(image_frame, textvariable=self.image_target_var, state="readonly", values=[fmt.upper() for fmt in IMAGE_FORMAT_CHOICES]).grid(row=2, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        ttk.Label(image_frame, text="Target long side").grid(row=3, column=0, sticky=tk.W, pady=(5, 0))
        self.image_resolution_var = tk.StringVar(value=RESOLUTION_CHOICES[2])
        ttk.Combobox(image_frame, textvariable=self.image_resolution_var, state="readonly", values=RESOLUTION_CHOICES).grid(row=3, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        for i in range(2):
            image_frame.columnconfigure(i, weight=1)

        video_frame = ttk.LabelFrame(options_frame, text="Video Conversion", padding=10)
        video_frame.grid(row=0, column=1, sticky=tk.NSEW)
        ttk.Checkbutton(video_frame, text="Enable video conversion", variable=self.enable_video_var).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(video_frame, text="Original format").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        video_source_options = ["All Videos"] + [fmt.upper() for fmt in VIDEO_FORMAT_CHOICES]
        self.video_source_var = tk.StringVar(value=video_source_options[0])
        ttk.Combobox(video_frame, textvariable=self.video_source_var, state="readonly", values=video_source_options).grid(row=1, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        ttk.Label(video_frame, text="Target format").grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        self.video_target_var = tk.StringVar(value="MP4")
        ttk.Combobox(video_frame, textvariable=self.video_target_var, state="readonly", values=[fmt.upper() for fmt in VIDEO_FORMAT_CHOICES]).grid(row=2, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        ttk.Label(video_frame, text="Target long side").grid(row=3, column=0, sticky=tk.W, pady=(5, 0))
        self.video_resolution_var = tk.StringVar(value=RESOLUTION_CHOICES[2])
        ttk.Combobox(video_frame, textvariable=self.video_resolution_var, state="readonly", values=RESOLUTION_CHOICES).grid(row=3, column=1, sticky=tk.EW, padx=(10, 0), pady=(5, 0))
        for i in range(2):
            video_frame.columnconfigure(i, weight=1)

        options_frame.columnconfigure(0, weight=1)
        options_frame.columnconfigure(1, weight=1)

        self.small_file_action_var = tk.StringVar(value="Skip")
        small_frame = ttk.LabelFrame(main, text="Files that are smaller than long side", padding=10)
        small_frame.pack(fill=tk.X, pady=5)
        ttk.Label(small_frame, text="Action").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(
            small_frame,
            textvariable=self.small_file_action_var,
            state="readonly",
            values=["Skip", "Just Copy"],
        ).grid(row=0, column=1, sticky=tk.W, padx=(10, 0))
        small_frame.columnconfigure(1, weight=1)

        # Action buttons
        action_frame = ttk.Frame(main)
        action_frame.pack(fill=tk.X, pady=5)
        self.convert_btn = ttk.Button(action_frame, text="Convert", command=self.start_conversion)
        self.convert_btn.pack(side=tk.LEFT)
        self.compare_btn = ttk.Button(action_frame, text="Open Comparison View", command=self.open_comparison, state=tk.DISABLED)
        self.compare_btn.pack(side=tk.LEFT, padx=10)
        ttk.Button(action_frame, text="Load mapping file", command=self.load_mapping_file).pack(side=tk.LEFT)
        ttk.Button(action_frame, text="Close", command=self.root.destroy).pack(side=tk.RIGHT)

        processing_frame = ttk.LabelFrame(main, text="Processing", padding=10)
        processing_frame.pack(fill=tk.X, pady=5)
        ttk.Label(processing_frame, text="Total files").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(processing_frame, textvariable=self.total_files_var).grid(row=0, column=1, sticky=tk.W, padx=(5, 20))
        ttk.Label(processing_frame, text="Processed Image").grid(row=0, column=2, sticky=tk.W)
        ttk.Label(processing_frame, textvariable=self.processed_images_var).grid(row=0, column=3, sticky=tk.W, padx=(5, 0))
        ttk.Label(processing_frame, text="Total Processed").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Label(processing_frame, textvariable=self.total_processed_var).grid(row=1, column=1, sticky=tk.W, padx=(5, 20), pady=(5, 0))
        ttk.Label(processing_frame, text="Processed Video").grid(row=1, column=2, sticky=tk.W, pady=(5, 0))
        ttk.Label(processing_frame, textvariable=self.processed_videos_var).grid(row=1, column=3, sticky=tk.W, padx=(5, 0), pady=(5, 0))
        for col in range(4):
            processing_frame.columnconfigure(col, weight=1)

        # Log window
        log_frame = ttk.LabelFrame(main, text="Messages", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_frame, height=18)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)

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
        }
        if not self._ensure_required_tools(job):
            return
        self.convert_btn.configure(state=tk.DISABLED)
        threading.Thread(target=self._run_conversion, args=(job,), daemon=True).start()

    def open_comparison(self) -> None:
        if not self.mapping:
            messagebox.showinfo("No mapping", "Run a conversion or load a mapping file first.")
            return
        ComparisonView(self.root, self.mapping)

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
            if self.mapping:
                self.compare_btn.configure(state=tk.NORMAL)
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
        ttk.Label(window, text="Missing dependency", font=("TkDefaultFont", 12, "bold")).pack(pady=(10, 5))
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
        image_formats = job["image"]["source_formats"] if job["image"]["enabled"] else []
        video_formats = job["video"]["source_formats"] if job["video"]["enabled"] else []
        files = self._gather_files(image_formats, video_formats)
        self._update_processing_counts(total=len(files), processed_image=0, processed_video=0)
        if not files:
            self._log("No files matched the requested formats.")
            self._finish_conversion()
            return
        mapping: List[Dict[str, str]] = []
        skipped = 0
        converted = 0
        processed_images = 0
        processed_videos = 0
        total_files = len(files)
        for source, rel_path, media_type in files:
            config = job[media_type]
            dest_path = self._destination_path(rel_path, config["output_format"])
            try:
                smaller = self._is_smaller_than_target(source, config["target_long_side"])
                if smaller:
                    action = job.get("small_file_action", "skip")
                    if action == "skip":
                        skipped += 1
                        self._log(f"Skipping (smaller): {source}")
                        continue
                    if action == "copy":
                        copy_dest = self._copy_destination_path(rel_path)
                        os.makedirs(os.path.dirname(copy_dest), exist_ok=True)
                        shutil.copy2(source, copy_dest)
                        mapping.append({"source": source, "dest": copy_dest})
                        self._log(f"Copied (smaller): {source} -> {copy_dest}")
                        converted += 1
                        if media_type == "image":
                            processed_images += 1
                        else:
                            processed_videos += 1
                        self._update_processing_counts(
                            total=total_files,
                            processed_image=processed_images,
                            processed_video=processed_videos,
                        )
                        continue
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                self._convert_file(source, dest_path, media_type, config)
                converted += 1
                mapping.append({"source": source, "dest": dest_path})
                self._log(f"Converted: {source} -> {dest_path}")
                if media_type == "image":
                    processed_images += 1
                else:
                    processed_videos += 1
                self._update_processing_counts(
                    total=total_files,
                    processed_image=processed_images,
                    processed_video=processed_videos,
                )
            except Exception as exc:  # pylint: disable=broad-except
                self._log(f"Failed to convert {source}: {exc}")
        if mapping:
            self.mapping = mapping
            self.compare_btn.configure(state=tk.NORMAL)
            self._save_mapping(mapping, job)
        self._log(f"Done. Converted {converted} file(s). Skipped {skipped}.")
        self._finish_conversion()

    def _finish_conversion(self) -> None:
        self.root.after(0, lambda: self.convert_btn.configure(state=tk.NORMAL))

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
    def _gather_files(self, image_formats: Sequence[str], video_formats: Sequence[str]) -> List[Tuple[str, str, str]]:
        wanted_image = {self._normalize_format(fmt) for fmt in image_formats}
        wanted_video = {self._normalize_format(fmt) for fmt in video_formats}
        results: List[Tuple[str, str, str]] = []
        seen: set = set()
        for item in self.sources:
            if item.kind == "folder":
                root_name = os.path.basename(os.path.normpath(item.path)) or "source"
                for root, _, files in os.walk(item.path):
                    for filename in files:
                        ext = os.path.splitext(filename)[1].lower()
                        full_path = os.path.join(root, filename)
                        media_type = self._media_type_for_extension(ext, wanted_image, wanted_video)
                        if media_type and full_path not in seen:
                            rel_inside = os.path.relpath(full_path, item.path)
                            rel_path = os.path.join(root_name, rel_inside)
                            results.append((full_path, rel_path, media_type))
                            seen.add(full_path)
            else:
                ext = os.path.splitext(item.path)[1].lower()
                media_type = self._media_type_for_extension(ext, wanted_image, wanted_video)
                if media_type and item.path not in seen:
                    rel_path = os.path.basename(item.path)
                    results.append((item.path, rel_path, media_type))
                    seen.add(item.path)
        return results

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
        scale_filter = f"scale=if(gt(iw,ih),{target_long_side},-2):if(gt(iw,ih),-2,{target_long_side})"
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i",
            source,
            "-vf",
            scale_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-c:a",
            "copy",
            dest,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    def _save_mapping(self, mapping: List[Dict[str, str]], job: Dict[str, object]) -> None:
        if not self.target_folder:
            return
        payload = {
            "created": time.time(),
            "target_folder": self.target_folder,
            "image_config": self._serialize_config(job["image"]),
            "video_config": self._serialize_config(job["video"]),
            "small_file_action": job["small_file_action"],
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
        processed_image: Optional[int] = None,
        processed_video: Optional[int] = None,
    ) -> None:
        def update() -> None:
            if total is not None:
                self.total_files_var.set(total)
            if processed_image is not None:
                self.processed_images_var.set(processed_image)
            if processed_video is not None:
                self.processed_videos_var.set(processed_video)
            total_processed = self.processed_images_var.get() + self.processed_videos_var.get()
            self.total_processed_var.set(total_processed)

        self.root.after(0, update)

    def _small_file_action(self) -> str:
        value = (self.small_file_action_var.get() or "").strip().lower()
        return "copy" if "copy" in value else "skip"


class ComparisonView(tk.Toplevel):
    def __init__(self, master: tk.Tk, mapping: Sequence[Dict[str, str]]):
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

        self._build_ui()
        if self.mapping:
            self._load_pair(0)

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(main)
        list_frame.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(list_frame, text="Converted files").pack()
        self.listbox = tk.Listbox(list_frame, height=20)
        self.listbox.pack(fill=tk.Y, expand=True)
        for entry in self.mapping:
            self.listbox.insert(tk.END, os.path.basename(entry["source"]))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        viewer_frame = ttk.Frame(main)
        viewer_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        canvas_frame = ttk.Frame(viewer_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.left_canvas = tk.Canvas(canvas_frame, width=500, height=500, background="black")
        self.left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        self.right_canvas = tk.Canvas(canvas_frame, width=500, height=500, background="black")
        self.right_canvas.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

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
        for key, path in (("source", entry["source"]), ("dest", entry["dest"])):
            self.images[key] = self._load_image(path)
        self._fit_to_window()

    def _load_image(self, path: str) -> Optional[Image.Image]:
        ext = os.path.splitext(path)[1].lower()
        if ext not in IMAGE_FORMATS:
            messagebox.showinfo("Preview", f"Cannot preview non-image file: {path}")
            return None
        try:
            img = Image.open(path)
            return img.convert("RGB")
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Preview", f"Could not open {path}: {exc}")
            return None

    def _render(self) -> None:
        for canvas, key in ((self.left_canvas, "source"), (self.right_canvas, "dest")):
            canvas.delete("all")
            img = self.images[key]
            if img is None:
                canvas.create_text(canvas.winfo_width() / 2, canvas.winfo_height() / 2, fill="white", text="Preview not available")
                continue
            scale = self.fit_scales[key] if self.fit_mode else self.scales.get(key, self.zoom_var.get())
            width = int(img.width * scale)
            height = int(img.height * scale)
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
            if img is None or img.width == 0 or img.height == 0:
                continue
            canvas_width = max(canvas.winfo_width(), 1)
            canvas_height = max(canvas.winfo_height(), 1)
            scale_w = canvas_width / img.width
            scale_h = canvas_height / img.height
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
