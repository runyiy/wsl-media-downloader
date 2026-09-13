"""Real Tk event-loop test, using a fake runner instead of internet access."""
import threading
import time
import tkinter as tk
import unittest

from wsl_media_downloader.core import Mode, Status
from wsl_media_downloader.gui import App
from wsl_media_downloader.runner import Cancelled, RunnerError


class FakeRunner:
    def __init__(self):
        self.active = 0
        self.maximum = 0

    def download(self, task, emit):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            emit("progress", 50)
            if task.title == "Fail":
                raise RunnerError("Synthetic failure")
            if task.title == "Cancel":
                if not task.cancel.wait(3):
                    raise RunnerError("Cancellation not received")
                raise Cancelled("Synthetic cancellation")
            emit("status", Status.CONVERTING)
            return "/tmp/wmd-test-media/Success.mp3"
        finally:
            self.active -= 1


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.runner = FakeRunner()
        self.app = App(self.root, self.runner, auto_check=False)
        self.app.ready = True

    def pump_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
            self.pump_until(lambda: not any(t.is_alive() for t in self.app.workers))
            self.root.destroy()

    def test_failure_does_not_block_next_and_progress_completes(self):
        q = self.app.downloads
        a = q.add("https://youtu.be/test", Mode.MP3, "Fail", [])
        b = q.add("https://youtu.be/test", Mode.MP3, "Success", [])
        self.pump_until(lambda: b.status == Status.COMPLETED)
        self.assertEqual(a.status, Status.FAILED)
        self.assertEqual(b.progress, 100)
        self.assertEqual(self.runner.maximum, 1)
        self.assertEqual(self.app.tree.item(b.id, "values")[2], "100%")

    def test_active_cancel_then_retry(self):
        task = self.app.downloads.add("https://youtu.be/test", Mode.VIDEO, "Cancel", [])
        self.pump_until(lambda: task.status == Status.DOWNLOADING)
        self.app.tree.selection_set(task.id)
        self.app.cancel_selected()
        self.pump_until(lambda: task.status == Status.CANCELLED)
        task.title = "Success"
        self.app.retry_failed()
        self.pump_until(lambda: task.status == Status.COMPLETED)


if __name__ == "__main__":
    unittest.main()
