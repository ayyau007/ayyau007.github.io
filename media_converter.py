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

from PIL import Image, ImageTk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


@dataclass
class SourceItem:
    path: str
    kind: str  # "file" or "folder"


IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp", ".heic"}
VIDEO_FORMATS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}


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
        options_frame = ttk.LabelFrame(main, text="Conversion Options", padding=10)
        options_frame.pack(fill=tk.X, pady=10)

        ttk.Label(options_frame, text="Original formats (comma separated, e.g. jpg,png,mp4)").grid(row=0, column=0, sticky=tk.W)
        self.original_formats_var = tk.StringVar(value="jpg,jpeg,png,webp,mp4,mov,mkv")
        ttk.Entry(options_frame, textvariable=self.original_formats_var).grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=2)

        ttk.Label(options_frame, text="Target format (e.g. jpg or mp4)").grid(row=2, column=0, sticky=tk.W)
        self.target_format_var = tk.StringVar(value="jpg")
        ttk.Entry(options_frame, textvariable=self.target_format_var, width=10).grid(row=2, column=1, sticky=tk.W)

        ttk.Label(options_frame, text="Target width").grid(row=3, column=0, sticky=tk.W)
        self.target_width_var = tk.StringVar(value="1920")
        ttk.Entry(options_frame, textvariable=self.target_width_var, width=8).grid(row=3, column=1, sticky=tk.W)

        ttk.Label(options_frame, text="Target height").grid(row=3, column=2, sticky=tk.W, padx=(10, 0))
        self.target_height_var = tk.StringVar(value="1080")
        ttk.Entry(options_frame, textvariable=self.target_height_var, width=8).grid(row=3, column=3, sticky=tk.W)

        self.skip_smaller_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Skip files that are smaller than the target resolution", variable=self.skip_smaller_var).grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=5)

        for i in range(4):
            options_frame.columnconfigure(i, weight=1)

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

        try:
            target_width = int(self.target_width_var.get())
            target_height = int(self.target_height_var.get())
        except ValueError:
            messagebox.showerror("Invalid resolution", "Width and height must be integers.")
            return

        original_formats = [f".{fmt.strip().lower()}" if not fmt.strip().startswith('.') else fmt.strip().lower() for fmt in self.original_formats_var.get().split(',') if fmt.strip()]
        if not original_formats:
            messagebox.showerror("No formats", "Please specify at least one original format.")
            return

        output_format = self.target_format_var.get().strip().lower()
        if not output_format:
            messagebox.showerror("No target format", "Please specify a target format.")
            return

        job = {
            "target_width": target_width,
            "target_height": target_height,
            "original_formats": original_formats,
            "output_format": output_format,
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
        files = self._gather_files(job["original_formats"])
        if not files:
            self._log("No files matched the requested formats.")
            self._finish_conversion()
            return
        mapping: List[Dict[str, str]] = []
        skipped = 0
        converted = 0
        for source, base_dir in files:
            rel_path = os.path.relpath(source, base_dir)
            dest_path = self._destination_path(rel_path, job["output_format"])
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            try:
                if job["skip_smaller"] and self._is_smaller_than_target(source, job["target_width"], job["target_height"]):
                    skipped += 1
                    self._log(f"Skipping (smaller): {source}")
                    continue
                self._convert_file(source, dest_path, job)
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
    def _gather_files(self, formats: Sequence[str]) -> List[Tuple[str, str]]:
        wanted = {fmt.lower() if fmt.startswith('.') else f'.{fmt.lower()}' for fmt in formats}
        results: List[Tuple[str, str]] = []
        seen: set = set()
        for item in self.sources:
            if item.kind == "folder":
                for root, _, files in os.walk(item.path):
                    for filename in files:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in wanted:
                            full_path = os.path.join(root, filename)
                            if full_path not in seen:
                                results.append((full_path, item.path))
                                seen.add(full_path)
            else:
                ext = os.path.splitext(item.path)[1].lower()
                if ext in wanted and item.path not in seen:
                    results.append((item.path, os.path.dirname(item.path)))
                    seen.add(item.path)
        return results

    def _destination_path(self, relative_path: str, output_format: str) -> str:
        if not self.target_folder:
            raise RuntimeError("Target folder not set")
        rel_without_ext = os.path.splitext(relative_path)[0]
        filename = f"{rel_without_ext}.{output_format.lstrip('.')}"
        return os.path.join(self.target_folder, filename)

    def _is_smaller_than_target(self, path: str, target_w: int, target_h: int) -> bool:
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_FORMATS:
            with Image.open(path) as img:
                width, height = img.size
        elif ext in VIDEO_FORMATS:
            width, height = self._probe_video_size(path)
        else:
            return False
        return width < target_w or height < target_h

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

    def _convert_file(self, source: str, dest: str, job: Dict[str, object]) -> None:
        ext = os.path.splitext(source)[1].lower()
        output_ext = f".{job['output_format']}" if not str(job['output_format']).startswith('.') else str(job['output_format'])
        if ext in IMAGE_FORMATS and output_ext in IMAGE_FORMATS:
            self._convert_image(source, dest, job)
        else:
            self._convert_with_ffmpeg(source, dest, job)

    def _convert_image(self, source: str, dest: str, job: Dict[str, object]) -> None:
        with Image.open(source) as img:
            img = img.convert("RGB")
            resized = img.resize((job["target_width"], job["target_height"]), Image.LANCZOS)
            resized.save(dest)

    def _convert_with_ffmpeg(self, source: str, dest: str, job: Dict[str, object]) -> None:
        scale_filter = f"scale={job['target_width']}:{job['target_height']}"
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
            "target_format": job["output_format"],
            "resolution": [job["target_width"], job["target_height"]],
            "entries": mapping,
        }
        path = Path(self.target_folder) / "conversion_map.json"
        path.write_text(json.dumps(payload, indent=2))
        self._log(f"Saved mapping file to {path}")

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
