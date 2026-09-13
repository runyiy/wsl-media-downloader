"""Queue and filename rules; no GUI or subprocess dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
import re
import threading
import unicodedata
from urllib.parse import urlsplit
import uuid

class Mode(str, Enum):
    VIDEO = "VIDEO"
    MP3 = "MP3"


class Status(str, Enum):
    WAITING = "Waiting"
    DOWNLOADING = "Downloading"
    CONVERTING = "Converting"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


def validate_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid YouTube URL.") from exc
    if (parsed.scheme not in {"https", "http"}
            or not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"))
            or parsed.username or parsed.password or port not in {None, 80, 443}
            or any(ord(c) < 32 or c.isspace() for c in value)):
        raise ValueError("Paste a full YouTube URL, such as https://www.youtube.com/watch?v=...")
    return value


def safe_stem(title: str) -> str:
    """Use the same literal name for checks and output, leaving room for suffixes."""
    name = unicodedata.normalize("NFC", title)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Cf")
    name = name.strip(" .") or "Untitled"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", name, re.I):
        name = "_" + name
    name = name.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")
    return name or "Untitled"


def name_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).rstrip(" .").casefold()


def file_stems(names: list[str]) -> list[str]:
    return [PurePosixPath(n).stem for n in names
            if not n.endswith((".part", ".ytdl", ".tmp")) and not re.search(r"\.part-Frag\d+$", n)]


def unique_stem(base: str, occupied: list[str]) -> str:
    keys = {name_key(n) for n in occupied}
    candidate, number = base, 2
    while name_key(candidate) in keys:
        candidate = f"{base} ({number})"
        number += 1
    return candidate


@dataclass
class Task:
    url: str
    mode: Mode
    title: str
    stem: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: Status = Status.WAITING
    progress: float | None = None
    output_path: str = ""
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)


class DownloadQueue:
    """All queue state belongs to the Tk thread. Workers send immutable events."""

    def __init__(self):
        self.tasks: list[Task] = []

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def duplicates(self, base: str, disk_names: list[str]) -> list[str]:
        key = name_key(base)
        matches = [n for n in file_stems(disk_names) if name_key(n) == key]
        matches.extend(t.title for t in self.tasks
                       if name_key(safe_stem(t.title)) == key or name_key(t.stem) == key)
        return matches

    def add(self, url: str, mode: Mode, title: str, disk_names: list[str]) -> Task:
        occupied = file_stems(disk_names) + [t.stem for t in self.tasks]
        task = Task(url, mode, title, unique_stem(safe_stem(title), occupied))
        self.tasks.append(task)
        return task

    def next_task(self) -> Task | None:
        if any(t.status in {Status.DOWNLOADING, Status.CONVERTING} for t in self.tasks):
            return None
        task = next((t for t in self.tasks if t.status == Status.WAITING), None)
        if task:
            task.status = Status.DOWNLOADING
        return task

    def cancel_task(self, task: Task):
        if task.status == Status.WAITING:
            task.cancel.set()
            task.status = Status.CANCELLED
        elif task.status in {Status.DOWNLOADING, Status.CONVERTING}:
            task.cancel.set()

    def retry(self, task: Task):
        if task.status in {Status.FAILED, Status.CANCELLED}:
            task.cancel = threading.Event()
            task.status, task.progress, task.error = Status.WAITING, None, ""
            task.output_path = ""

    def clear_completed(self):
        self.tasks[:] = [t for t in self.tasks if t.status != Status.COMPLETED]
