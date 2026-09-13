"""Tk widgets are accessed only on the main thread."""

from __future__ import annotations

import argparse
from collections import deque
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .core import DownloadQueue, Mode, Status, validate_url
from .runner import Cancelled, CREATE_NO_WINDOW, WslRunner


class App:
    def __init__(self, root: tk.Tk, runner=None, *, auto_check=True):
        self.root = root
        self.runner = runner or WslRunner()
        self.downloads = DownloadQueue()
        self.events = queue.Queue()
        self.logs = deque(maxlen=1500)
        self.workers: list[threading.Thread] = []
        self.ready = False
        self.probing = False
        self.checking = False
        self.closing = False
        self.active_id = None
        self.probe_cancel = threading.Event()
        self.log_window = None
        self.log_text = None
        self.url = tk.StringVar()
        self.mode = tk.StringVar(value=Mode.VIDEO.value)
        self.banner = tk.StringVar(value="Checking Ubuntu WSL…")
        self.hint = tk.StringVar(value="Paste a video URL to get started.")
        self.folder_label = tk.StringVar(value=getattr(self.runner, "download_dir", "") or "Set your download folder in .env")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self.poll)
        if auto_check:
            self.check_environment()

    def _build(self):
        root = self.root
        root.title("WSL Media Downloader")
        root.geometry("980x650")
        root.minsize(800, 540)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=30)
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Muted.TLabel", foreground="#526171")
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="WSL Media Downloader", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Highest quality, downloaded straight to Ubuntu.", style="Muted.TLabel").pack(anchor="w", pady=(3, 16))
        ttk.Label(frame, textvariable=self.banner, wraplength=880).pack(anchor="w")
        ttk.Label(frame, text="YouTube URL").pack(anchor="w", pady=(18, 5))
        url_row = ttk.Frame(frame)
        url_row.pack(fill="x")
        self.entry = ttk.Entry(url_row, textvariable=self.url, font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.bind("<Return>", lambda _: self.add_url())
        self.add_button = ttk.Button(url_row, text="Add to Queue", command=self.add_url, state="disabled")
        self.add_button.pack(side="left", padx=(10, 0), ipady=5)
        modes = ttk.Frame(frame)
        modes.pack(fill="x", pady=(12, 8))
        ttk.Radiobutton(modes, text="Best Video + Best Audio", variable=self.mode, value="VIDEO").pack(side="left")
        ttk.Radiobutton(modes, text="MP3 + Cover", variable=self.mode, value="MP3").pack(side="left", padx=24)
        ttk.Label(frame, textvariable=self.hint, style="Muted.TLabel").pack(anchor="w", pady=(0, 14))
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("type", "title", "progress", "status"), show="headings", selectmode="extended")
        for key, label, width, stretch in [("type", "Type", 72, False), ("title", "Title", 490, True), ("progress", "Progress", 90, False), ("status", "Status", 115, False)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=width if not stretch else 180, stretch=stretch, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _: self.show_logs())
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(14, 10))
        for label, command in [("Cancel Selected", self.cancel_selected), ("Retry Failed", self.retry_failed), ("Clear Completed", self.clear_completed), ("Open Folder", self.open_folder)]:
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Details / Logs", command=self.show_logs).pack(side="right")
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.folder_label, style="Muted.TLabel", wraplength=570).pack(side="left")
        self.check_button = ttk.Button(bottom, text="Check Environment", command=self.check_environment)
        self.check_button.pack(side="right")
        self.entry.focus_set()

    def spawn(self, target):
        worker = threading.Thread(target=target, daemon=True)
        self.workers.append(worker)
        worker.start()

    def log(self, message: str):
        self.logs.append(message)
        logging.getLogger("wmd").info(message)
        if self.log_text and self.log_text.winfo_exists():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            if int(self.log_text.index("end-1c").split(".")[0]) > 1800:
                self.log_text.delete("1.0", "301.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def check_environment(self):
        if self.checking or self.probing or self.active_id or self.closing:
            return
        self.checking, self.ready = True, False
        self.add_button.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self.banner.set("Checking Ubuntu WSL, yt-dlp and ffmpeg…")

        def work():
            try:
                self.events.put(("environment", self.runner.check_environment()))
            except Exception as exc:
                self.events.put(("environment_error", str(exc)))
        self.spawn(work)

    def add_url(self):
        if not self.ready or self.probing or self.closing:
            return
        try:
            url = validate_url(self.url.get())
        except ValueError as exc:
            messagebox.showerror("Invalid URL", str(exc), parent=self.root)
            return
        mode = Mode(self.mode.get())
        self.probing = True
        self.probe_cancel = threading.Event()
        self.add_button.configure(state="disabled")
        self.hint.set("Reading the title and checking for duplicates…")

        def work():
            try:
                metadata = self.runner.metadata(url, self.probe_cancel)
                files = self.runner.list_files(self.probe_cancel)
                self.events.put(("metadata", (url, mode, metadata["title"], files)))
            except Exception as exc:
                self.events.put(("metadata_error", str(exc)))
        self.spawn(work)

    def redraw(self):
        ids = {t.id for t in self.downloads.tasks}
        for item in self.tree.get_children():
            if item not in ids:
                self.tree.delete(item)
        for task in self.downloads.tasks:
            percentage = "—" if task.progress is None else f"{task.progress:.0f}%"
            values = (task.mode.value, task.title, percentage, task.status.value)
            if self.tree.exists(task.id):
                self.tree.item(task.id, values=values)
            else:
                self.tree.insert("", "end", iid=task.id, values=values)

    def start_next(self):
        if self.closing or not self.ready or self.active_id:
            return
        task = self.downloads.next_task()
        if not task:
            return
        self.active_id = task.id
        self.log(f"[{task.title}] Starting {task.mode.value}; output name: {task.stem}")

        def work():
            def emit(kind, value):
                self.events.put(("task_event", (task.id, kind, value)))
            try:
                output = self.runner.download(task, emit)
                self.events.put(("finished", (task.id, Status.COMPLETED, output)))
            except Cancelled as exc:
                self.events.put(("finished", (task.id, Status.CANCELLED, str(exc))))
            except Exception as exc:
                self.events.put(("finished", (task.id, Status.FAILED, str(exc))))
        self.spawn(work)

    def handle(self, kind, payload):
        if kind in {"environment", "environment_error"}:
            self.checking = False
            self.check_button.configure(state="normal")
            if kind == "environment":
                self.ready = True
                self.folder_label.set(self.runner.download_dir)
                warning = " • Deno not found; some YouTube videos may need it." if not payload["tools"].get("deno") else ""
                self.banner.set(f"Ready • {payload['distro']} • yt-dlp + ffmpeg" + warning)
                self.add_button.configure(state="normal")
                self.log("Environment OK: " + str(payload))
            else:
                self.banner.set("Environment check failed. Open Details / Logs, fix the issue, then check again.")
                self.log("Environment error: " + payload)
                messagebox.showerror("WSL setup needed", payload + "\n\nSee README for setup instructions.", parent=self.root)
        elif kind in {"metadata", "metadata_error"}:
            self.probing = False
            self.add_button.configure(state="normal" if self.ready else "disabled")
            self.hint.set("Paste another video URL to add it to the queue.")
            if kind == "metadata_error":
                self.log("Metadata error: " + payload)
                messagebox.showerror("Could not read video", payload, parent=self.root)
                return
            url, mode, title, files = payload
            from .core import safe_stem
            if self.downloads.duplicates(safe_stem(title), files):
                if not self.duplicate_dialog(title):
                    self.hint.set("Duplicate skipped.")
                    return
            task = self.downloads.add(url, mode, title, files)
            self.log(f"[{title}] Added to queue as {task.stem}")
            if self.url.get().strip() == url:
                self.url.set("")
            self.entry.focus_set()
        elif kind == "task_event":
            task_id, event, value = payload
            task = self.downloads.get(task_id)
            if task:
                if event == "progress":
                    task.progress = value
                    task.status = Status.DOWNLOADING
                elif event == "status":
                    task.status = value
                elif event == "output":
                    task.output_path = value
                elif event == "log":
                    self.log(f"[{task.title}] {value}")
        elif kind == "finished":
            task_id, status, detail = payload
            task = self.downloads.get(task_id)
            self.active_id = None
            if task:
                task.status = status
                if status == Status.COMPLETED:
                    task.progress, task.output_path = 100.0, detail
                else:
                    task.error = detail
                self.log(f"[{task.title}] {status.value}: {detail}")

    def duplicate_dialog(self, title: str) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("Possible Duplicate")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        result = [False]
        frame = ttk.Frame(dialog, padding=22)
        frame.pack()
        ttk.Label(frame, text="This name already exists in the folder or queue:", wraplength=500).pack(anchor="w")
        ttk.Label(frame, text=title, wraplength=500, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=12)
        ttk.Label(frame, text="Download Anyway saves a separate copy with a numbered suffix.", wraplength=500).pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e", pady=(20, 0))
        def accept():
            result[0] = True
            dialog.destroy()
        cancel = ttk.Button(buttons, text="Cancel", command=dialog.destroy)
        cancel.pack(side="left", padx=8)
        ttk.Button(buttons, text="Download Anyway", command=accept).pack(side="left")
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.grab_set()
        cancel.focus_set()
        self.root.wait_window(dialog)
        return result[0]

    def poll(self):
        for _ in range(300):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if not self.closing:
                self.handle(kind, payload)
        self.workers[:] = [w for w in self.workers if w.is_alive()]
        if self.closing:
            if not self.workers:
                self.root.destroy()
                return
        else:
            self.start_next()
            self.redraw()
        self.root.after(80, self.poll)

    def selected(self):
        return [t for item in self.tree.selection() if (t := self.downloads.get(item))]

    def cancel_selected(self):
        for task in self.selected():
            self.downloads.cancel_task(task)
        self.hint.set("Cancellation requested. Partial downloads are kept for Retry.")
        self.redraw()

    def retry_failed(self):
        selected = self.selected()
        for task in selected or self.downloads.tasks:
            self.downloads.retry(task)
        self.redraw()

    def clear_completed(self):
        self.downloads.clear_completed()
        self.redraw()

    def open_folder(self):
        try:
            subprocess.Popen(["explorer.exe", self.runner.explorer_path()], creationflags=CREATE_NO_WINDOW)
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self.root)

    def show_logs(self):
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        window = self.log_window = tk.Toplevel(self.root)
        window.title("Details / Logs")
        window.geometry("860x480")
        text = self.log_text = ScrolledText(window, wrap="word", font=("Consolas", 10))
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("end", "\n".join(self.logs))
        text.configure(state="disabled")
        ttk.Label(window, text="Select text and press Ctrl+C to copy. Logs may contain media titles and URLs.").pack(anchor="w", padx=12, pady=(0, 12))

    def close(self):
        self.closing = True
        self.ready = False
        self.probe_cancel.set()
        for task in self.downloads.tasks:
            self.downloads.cancel_task(task)
        self.add_button.configure(state="disabled")
        self.banner.set("Closing: stopping active WSL processes…")


def configure_logging():
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WSLMediaDownloader"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger = logging.getLogger("wmd")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def main():
    parser = argparse.ArgumentParser(description="Windows GUI for downloads in Ubuntu WSL")
    parser.add_argument("--distro", help="Exact Ubuntu distribution name; auto-detected by default")
    parser.add_argument("--config", type=Path, help="Optional path to a private .env configuration file")
    parser.add_argument("--smoke-test", action="store_true", help="Open and close the GUI without contacting WSL")
    args = parser.parse_args()
    root = tk.Tk()
    try:
        configure_logging()
    except OSError:
        pass  # UI logs still work if LocalAppData cannot be written.
    app = App(root, WslRunner(args.distro, config_path=args.config), auto_check=not args.smoke_test)
    if args.smoke_test:
        app.banner.set("GUI smoke test • no downloads")
        root.after(600, app.close)
    root.mainloop()
