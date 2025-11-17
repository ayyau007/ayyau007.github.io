#!/usr/bin/env python3
"""Media conversion GUI with comparison view."""
import json
import os
import queue
import subprocess
import threading
import time
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

        self._build_ui()
        self._poll_log_queue()

    # --- UI construction -------------------------------------------------
    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        selection_frame = ttk.LabelFrame(main, text="Sources", padding=10)
        selection_frame.pack(fill=tk.X)

        btn_frame = ttk.Frame(selection_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="Add Folder", command=self.add_folder).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Add Files", command=self.add_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove Selected", command=self.remove_selected).pack(side=tk.LEFT)

        self.source_list = tk.Listbox(selection_frame, height=6)
        self.source_list.pack(fill=tk.X)

        # Conversion options
        options_frame = ttk.Frame(main)
        options_frame.pack(fill=tk.X, pady=10)

        self.enable_image_var = tk.BooleanVar(value=True)
        self.enable_video_var = tk.BooleanVar(value=True)

        image_frame = ttk.LabelFrame(options_frame, text="Image Conversion", padding=10)
        image_frame.pack(fill=tk.X, pady=5)
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
        video_frame.pack(fill=tk.X, pady=5)
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

        self.skip_smaller_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Skip files that are smaller than the target long side", variable=self.skip_smaller_var).pack(anchor=tk.W, pady=5)

        # Target folder
        target_frame = ttk.LabelFrame(main, text="Target", padding=10)
        target_frame.pack(fill=tk.X)
        ttk.Button(target_frame, text="Choose target folder", command=self.choose_target_folder).pack(side=tk.LEFT)
        self.target_label = ttk.Label(target_frame, text="No folder selected", foreground="gray")
        self.target_label.pack(side=tk.LEFT, padx=10)

        # Action buttons
        action_frame = ttk.Frame(main)
        action_frame.pack(fill=tk.X, pady=5)
        self.convert_btn = ttk.Button(action_frame, text="Convert", command=self.start_conversion)
        self.convert_btn.pack(side=tk.LEFT)
        self.compare_btn = ttk.Button(action_frame, text="Open Comparison View", command=self.open_comparison, state=tk.DISABLED)
        self.compare_btn.pack(side=tk.LEFT, padx=10)
        ttk.Button(action_frame, text="Load mapping file", command=self.load_mapping_file).pack(side=tk.LEFT)

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
            "skip_smaller": self.skip_smaller_var.get(),
        }
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
    def _run_conversion(self, job: Dict[str, object]) -> None:
        image_formats = job["image"]["source_formats"] if job["image"]["enabled"] else []
        video_formats = job["video"]["source_formats"] if job["video"]["enabled"] else []
        files = self._gather_files(image_formats, video_formats)
        if not files:
            self._log("No files matched the requested formats.")
            self._finish_conversion()
            return
        mapping: List[Dict[str, str]] = []
        skipped = 0
        converted = 0
        for source, base_dir, media_type in files:
            rel_path = os.path.relpath(source, base_dir)
            config = job[media_type]
            dest_path = self._destination_path(rel_path, config["output_format"])
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            try:
                if job["skip_smaller"] and self._is_smaller_than_target(source, config["target_long_side"]):
                    skipped += 1
                    self._log(f"Skipping (smaller): {source}")
                    continue
                self._convert_file(source, dest_path, media_type, config)
                converted += 1
                mapping.append({"source": source, "dest": dest_path})
                self._log(f"Converted: {source} -> {dest_path}")
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
            base_dir = item.path if item.kind == "folder" else os.path.dirname(item.path)
            if item.kind == "folder":
                for root, _, files in os.walk(item.path):
                    for filename in files:
                        ext = os.path.splitext(filename)[1].lower()
                        full_path = os.path.join(root, filename)
                        media_type = self._media_type_for_extension(ext, wanted_image, wanted_video)
                        if media_type and full_path not in seen:
                            results.append((full_path, base_dir, media_type))
                            seen.add(full_path)
            else:
                ext = os.path.splitext(item.path)[1].lower()
                media_type = self._media_type_for_extension(ext, wanted_image, wanted_video)
                if media_type and item.path not in seen:
                    results.append((item.path, base_dir, media_type))
                    seen.add(item.path)
        return results

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
        cmd = [
            "ffprobe",
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
        target_long_side = int(config["target_long_side"])
        scale_filter = f"scale=if(gt(iw,ih),{target_long_side},-2):if(gt(iw,ih),-2,{target_long_side})"
        cmd = [
            "ffmpeg",
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
            "skip_smaller": job["skip_smaller"],
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


class ComparisonView(tk.Toplevel):
    def __init__(self, master: tk.Tk, mapping: Sequence[Dict[str, str]]):
        super().__init__(master)
        self.title("Comparison View")
        self.geometry("1200x650")
        self.mapping = list(mapping)
        self.current_index = 0
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self._drag_start: Optional[Tuple[int, int]] = None
        self.images: Dict[str, Optional[Image.Image]] = {"source": None, "dest": None}
        self.photo_images: Dict[str, Optional[ImageTk.PhotoImage]] = {"source": None, "dest": None}

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

        controls = ttk.Frame(viewer_frame)
        controls.pack(fill=tk.X, pady=5)
        ttk.Button(controls, text="Previous", command=self.show_previous).pack(side=tk.LEFT)
        ttk.Button(controls, text="Next", command=self.show_next).pack(side=tk.LEFT, padx=5)
        ttk.Label(controls, text="Zoom").pack(side=tk.LEFT, padx=(20, 5))
        self.zoom_var = tk.DoubleVar(value=1.0)
        zoom_slider = ttk.Scale(controls, from_=0.25, to=4.0, orient=tk.HORIZONTAL, variable=self.zoom_var, command=self._on_zoom_change)
        zoom_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _load_pair(self, index: int) -> None:
        if not (0 <= index < len(self.mapping)):
            return
        self.current_index = index
        entry = self.mapping[index]
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.see(index)
        self.scale = self.zoom_var.get()
        self.offset_x = 0
        self.offset_y = 0
        for key, path in (("source", entry["source"]), ("dest", entry["dest"])):
            self.images[key] = self._load_image(path)
        self._render()

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
            width = int(img.width * self.scale)
            height = int(img.height * self.scale)
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

    def _on_zoom_change(self, _value: str) -> None:
        self.scale = self.zoom_var.get()
        self._render()

    def _on_drag_start(self, event: tk.Event) -> None:  # type: ignore[override]
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:  # type: ignore[override]
        if not self._drag_start:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self.offset_x += dx
        self.offset_y += dy
        self._drag_start = (event.x, event.y)
        self._render()


def main() -> None:
    root = tk.Tk()
    app = MediaConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
