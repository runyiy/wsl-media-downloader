"""Shell-free WSL integration. Fixed Python snippets receive data as arguments."""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import PurePosixPath
import queue
import re
import subprocess
import threading
import time
import uuid

from .config import Config, load_config
from .core import Mode, Status, Task, validate_url

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PID_PREFIX = "__WMD_PID__:"

# The group leader keeps the token in /proc to prevent signalling reused PIDs.
SUPERVISOR = r'''
import os, signal, subprocess, sys
cancelled = False
def stop(*_):
    global cancelled
    cancelled = True
signal.signal(signal.SIGTERM, stop)
print("__WMD_PID__:" + str(os.getpid()), flush=True)
child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
code = child.wait()
if cancelled:
    os.killpg(os.getpgrp(), signal.SIGKILL)
raise SystemExit(code)
'''
BRIDGE = r'''
import os, subprocess, sys
token, supervisor, executable, *args = sys.argv[1:]
env = os.environ.copy()
home = os.path.expanduser("~")
env["PATH"] = os.pathsep.join([home+"/.local/bin", home+"/.deno/bin", home+"/bin", env.get("PATH", "")])
env["PYTHONUNBUFFERED"] = "1"
env["WMD_TOKEN"] = token
result = subprocess.run([sys.executable, "-u", "-c", supervisor, executable, *args], env=env, start_new_session=True)
raise SystemExit(result.returncode)
'''
SIGNAL_GROUP = r'''
import os, signal, sys
pid, token, sig = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
try:
    with open(f"/proc/{pid}/environ", "rb") as f:
        entries = f.read().split(b"\0")
    if ("WMD_TOKEN=" + token).encode() not in entries or os.getpgid(pid) != pid:
        raise SystemExit("Refusing to signal an unrecognized process")
    os.killpg(pid, sig)
except ProcessLookupError:
    pass
except FileNotFoundError:
    pass
'''
ENV_CHECK = r'''
import json, os, shutil, subprocess, sys
from pathlib import Path
tools = {name: shutil.which(name) for name in ["yt-dlp", "ffmpeg", "ffprobe", "deno"]}
directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
    raise SystemExit("Download folder is not readable and writable: " + str(directory))
versions = {}
for name in ["yt-dlp", "ffmpeg", "ffprobe"]:
    if tools[name]:
        result = subprocess.run([tools[name], "--version" if name == "yt-dlp" else "-version"], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise SystemExit(name + " could not start: " + result.stderr)
        versions[name] = result.stdout.splitlines()[0]
print(json.dumps({"tools": tools, "versions": versions}))
'''
LIST_FILES = r'''
import json, os, sys
with os.scandir(sys.argv[1]) as entries:
    print(json.dumps([p.name for p in entries if p.is_file()]))
'''
CHECK_OUTPUT = r'''
import json, os, sys
path = sys.argv[1]
print(json.dumps(os.path.isfile(path) and os.path.getsize(path) > 0))
'''


class RunnerError(RuntimeError):
    pass


class Cancelled(RunnerError):
    pass


def decode_wsl(data: bytes) -> str:
    return data.decode("utf-16-le" if b"\x00" in data else "utf-8", errors="replace").lstrip("\ufeff")


def choose_distro(names: list[str]) -> str:
    if "Ubuntu" in names:
        return "Ubuntu"
    matches = [n for n in names if n.lower().startswith("ubuntu")]
    if len(matches) == 1:
        return matches[0]
    raise RunnerError("Ubuntu WSL was not found unambiguously. Use --distro with its exact name from wsl --list --quiet.")


def metadata_args(url: str) -> list[str]:
    return ["--ignore-config", "--no-playlist", "--no-colors", "--dump-single-json",
            "--skip-download", "--retries", "2", "--extractor-retries", "2",
            "--socket-timeout", "20", "--", validate_url(url)]


def download_args(task: Task, download_dir: str) -> list[str]:
    # Escape '%' in literal titles so yt-dlp cannot interpret title text as a template.
    template = task.stem.replace("%", "%%") + ".%(ext)s"
    args = ["--ignore-config", "--no-playlist", "--newline", "--no-colors", "--progress",
            "--progress-delta", "0.3", "--continue", "--no-overwrites",
            "--retries", "infinite", "--fragment-retries", "infinite", "--retry-sleep", "5",
            "--socket-timeout", "30", "--paths", download_dir, "--output", template,
            "--progress-template", 'download:__WMD_PROGRESS__:{"downloaded_bytes":%(progress.downloaded_bytes)j,"total_bytes":%(progress.total_bytes)j,"total_bytes_estimate":%(progress.total_bytes_estimate)j}',
            "--progress-template", 'postprocess:__WMD_POST__:%(progress.status)s',
            "--print", 'after_move:__WMD_FILE__:%(filepath)j']
    if task.mode == Mode.VIDEO:
        args += ["--format", "bv+ba/b", "--merge-output-format", "mkv"]
    else:
        args += ["--format", "ba/b", "--extract-audio", "--audio-format", "mp3",
                 "--audio-quality", "0", "--embed-thumbnail", "--convert-thumbnails", "jpg",
                 "--embed-metadata"]
    return args + ["--", validate_url(task.url)]


def parse_progress(line: str, download_dir: str) -> tuple[str, object] | None:
    if line.startswith("__WMD_PROGRESS__:"):
        try:
            data = json.loads(line.split(":", 1)[1])
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            done = data.get("downloaded_bytes")
            if isinstance(total, (int, float)) and total > 0 and isinstance(done, (int, float)):
                return "progress", max(0.0, min(99.0, 100.0 * done / total))
        except (ValueError, TypeError, AttributeError):
            pass
    elif line.startswith("__WMD_POST__:") or re.match(r"\[(ExtractAudio|Merger|Metadata|EmbedThumbnail|ThumbnailsConvertor)\]", line):
        return "status", Status.CONVERTING
    elif line.startswith("__WMD_FILE__:"):
        try:
            path = json.loads(line.split(":", 1)[1])
            if isinstance(path, str) and PurePosixPath(path).parent == PurePosixPath(download_dir):
                return "output", path
        except ValueError:
            pass
    return None


class WslRunner:
    def __init__(self, distro: str | None = None, config: Config | None = None, config_path=None):
        self.config = config
        self._explicit_config = config is not None
        self._distro_override = distro
        self.config_path = config_path
        self.distro = distro or (config.distro if config else None)
        self.ytdlp = "yt-dlp"

    @property
    def download_dir(self) -> str:
        return self.config.download_dir if self.config else ""

    def command(self, executable: str, *args: str) -> list[str]:
        if not self.distro or not self.config:
            raise RunnerError("Check the WSL environment first.")
        return ["wsl.exe", "--distribution", self.distro, "--user", self.config.user,
                "--cd", "~", "--exec", executable, *args]

    def _signal(self, pid: int, token: str, sig: int):
        result = subprocess.run(self.command("python3", "-c", SIGNAL_GROUP, str(pid), token, str(sig)),
                                capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW)
        if result.returncode:
            raise RunnerError("WSL could not stop its process: " + decode_wsl(result.stderr or result.stdout))

    def execute(self, executable: str, args: list[str], cancel: threading.Event | None = None,
                timeout: float | None = None, on_line=None) -> str:
        cancel = cancel or threading.Event()
        if cancel.is_set():
            raise Cancelled("Cancelled")
        token = uuid.uuid4().hex
        command = self.command("python3", "-u", "-c", BRIDGE, token, SUPERVISOR, executable, *args)
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                       creationflags=CREATE_NO_WINDOW, bufsize=1)
        except OSError as exc:
            raise RunnerError("Could not start WSL: " + str(exc)) from exc
        lines: queue.Queue = queue.Queue()

        def read():
            try:
                for line in process.stdout:
                    lines.put(line.rstrip("\r\n"))
            finally:
                lines.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        output = deque(maxlen=250)
        pid = None
        start = time.monotonic()
        stopping = None
        timed_out = False
        stop_error = ""
        killed = False
        try:
            while True:
                now = time.monotonic()
                timed_out = timed_out or (timeout is not None and now - start > timeout)
                if (cancel.is_set() or timed_out) and stopping is None and pid is not None:
                    stopping = now
                    try:
                        self._signal(pid, token, 15)
                    except (OSError, subprocess.SubprocessError, RunnerError) as exc:
                        stop_error = str(exc)
                if stopping is not None and now - stopping > 3 and not killed:
                    killed = True
                    try:
                        self._signal(pid, token, 9)
                    except (OSError, subprocess.SubprocessError, RunnerError) as exc:
                        stop_error = str(exc)
                if (stopping is not None and now - stopping > 13) or ((cancel.is_set() or timed_out) and pid is None and now - start > 15):
                    process.kill()
                    stop_error = stop_error or "WSL did not confirm process cleanup. Check Ubuntu before retrying."
                    break
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                if line.startswith(PID_PREFIX):
                    pid = int(line[len(PID_PREFIX):])
                    continue
                output.append(line)
                if on_line:
                    on_line(line)
            code = process.wait(timeout=5)
        finally:
            if process.poll() is None:
                if pid is not None:
                    try:
                        self._signal(pid, token, 9)
                    except (OSError, subprocess.SubprocessError, RunnerError):
                        pass
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=1)
            if not reader.is_alive():
                process.stdout.close()
        if stop_error:
            raise RunnerError(stop_error)
        if cancel.is_set():
            raise Cancelled("Cancelled; partial files are kept for Retry.")
        if timed_out:
            raise RunnerError("WSL operation timed out. Check Ubuntu and your network, then retry.")
        if code:
            raise RunnerError("\n".join(output)[-12000:] or f"WSL exited with code {code}.")
        return "\n".join(output)

    def check_environment(self) -> dict:
        if not self._explicit_config:
            self.config = load_config(self.config_path)
            self.distro = self._distro_override or self.config.distro
        if os.name != "nt":
            raise RunnerError("Run this desktop application on Windows, not inside WSL.")
        if not self.distro:
            try:
                result = subprocess.run(["wsl.exe", "--list", "--quiet"], capture_output=True,
                                        timeout=25, creationflags=CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RunnerError("WSL is unavailable. Install/start Ubuntu WSL, then click Check Environment.") from exc
            if result.returncode:
                raise RunnerError(decode_wsl(result.stderr or result.stdout))
            self.distro = choose_distro([n.strip() for n in decode_wsl(result.stdout).splitlines() if n.strip()])
        result = self.execute("python3", ["-c", ENV_CHECK, self.download_dir], timeout=60)
        data = json.loads(result.splitlines()[-1])
        missing = [n for n in ["yt-dlp", "ffmpeg", "ffprobe"] if not data["tools"].get(n)]
        if missing:
            raise RunnerError("Missing for the configured Ubuntu user: " + ", ".join(missing) + ". See README setup instructions. Python 3 is also required.")
        self.ytdlp = data["tools"]["yt-dlp"]
        data["distro"] = self.distro
        return data

    def list_files(self, cancel: threading.Event | None = None) -> list[str]:
        result = self.execute("python3", ["-c", LIST_FILES, self.download_dir], cancel, timeout=25)
        return json.loads(result.splitlines()[-1])

    def metadata(self, url: str, cancel: threading.Event) -> dict:
        result = self.execute(self.ytdlp, metadata_args(url), cancel, timeout=90)
        for line in reversed(result.splitlines()):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("title"):
                if data.get("_type") in {"playlist", "multi_video"}:
                    raise RunnerError("Paste an individual video URL. Playlists are not supported.")
                if data.get("is_live"):
                    raise RunnerError("This video is currently live. Try again after the stream ends.")
                return {"title": data["title"]}
        raise RunnerError("yt-dlp did not return a media title. See Details / Logs.")

    def download(self, task: Task, emit):
        output_path = ""

        def line_received(line: str):
            nonlocal output_path
            parsed = parse_progress(line, self.download_dir)
            if parsed:
                kind, value = parsed
                if kind == "output":
                    output_path = value
                emit(kind, value)
            elif line:
                emit("log", line)

        self.execute(self.ytdlp, download_args(task, self.download_dir), task.cancel, on_line=line_received)
        if not output_path:
            raise RunnerError("yt-dlp exited without confirming the final file. Review the log before retrying.")
        verified = self.execute("python3", ["-c", CHECK_OUTPUT, output_path], task.cancel, timeout=20)
        if not json.loads(verified.splitlines()[-1]):
            raise RunnerError("The final output file is missing or empty: " + output_path)
        return output_path

    def explorer_path(self) -> str:
        if not self.distro or not self.config:
            raise RunnerError("Check the environment before opening the folder.")
        return "\\\\wsl.localhost\\" + self.distro + self.download_dir.replace("/", "\\")
