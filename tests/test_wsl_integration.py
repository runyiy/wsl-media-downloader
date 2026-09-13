"""Opt-in checks: WMD_WSL_TESTS=1. No network or existing media needed."""
import json
import os
from pathlib import Path
import threading
import time
import unittest

from wsl_media_downloader.core import Mode, Task
from wsl_media_downloader.runner import Cancelled, RunnerError, WslRunner, download_args


@unittest.skipUnless(os.environ.get("WMD_WSL_TESTS") == "1", "Set WMD_WSL_TESTS=1 to test local Ubuntu")
class WslTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = WslRunner()
        cls.environment = cls.runner.check_environment()

    def test_argument_roundtrip(self):
        args = ['https://www.youtube.com/watch?v=a&list=b&index=2', 'a "quote" % & $() ; 音楽', 'C:\\a b\\']
        result = self.runner.execute("python3", ["-c", "import json,sys; print(json.dumps(sys.argv[1:]))", *args], timeout=15)
        self.assertEqual(json.loads(result), args)

    def test_cancel_descendants(self):
        cancel = threading.Event()
        child_pid = []
        def line(value):
            if value.startswith("CHILD:"):
                child_pid.append(int(value.split(":")[1]))
                cancel.set()
        script = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('CHILD:'+str(p.pid),flush=True); p.wait()"
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            self.runner.execute("python3", ["-u", "-c", script], cancel, timeout=20, on_line=line)
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue(child_pid)
        # A transient zombie is already stopped and will be reaped by init.
        check = "import pathlib,sys; p=pathlib.Path('/proc')/sys.argv[1]/'stat'; print('gone' if not p.exists() else p.read_text().split()[2])"
        state = self.runner.execute("python3", ["-c", check, str(child_pid[0])], timeout=15)
        self.assertIn(state, {"gone", "Z"})

    def test_timeout(self):
        with self.assertRaisesRegex(RunnerError, "timed out"):
            self.runner.execute("python3", ["-c", "import time; time.sleep(60)"], timeout=0.5)

    def test_pre_cancelled_never_starts(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.runner.execute("python3", ["-c", "raise Exception('must not run')"], cancel)

    def test_synthetic_video_and_mp3_with_cover(self):
        script = Path(__file__).with_name("synthetic_pipeline.py").read_text(encoding="utf-8")
        stem = "Synthetic 100% %(id)s & 音楽"
        commands = {mode.value: download_args(Task("https://youtu.be/test", mode, "Synthetic test", stem), self.runner.download_dir) for mode in Mode}
        config = {"commands": commands, "ytdlp": self.runner.ytdlp, "stem": stem}
        result = json.loads(self.runner.execute("python3", ["-c", script, json.dumps(config)], timeout=90))
        self.assertTrue(result["VIDEO"]["verified"])
        self.assertTrue(result["MP3"]["verified"])


if __name__ == "__main__":
    unittest.main()
