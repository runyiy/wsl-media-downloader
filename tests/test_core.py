import unittest
from functools import partial
from wsl_media_downloader.config import Config

from wsl_media_downloader.core import DownloadQueue, Mode, Status, file_stems, name_key, safe_stem, unique_stem, validate_url
from wsl_media_downloader.runner import WslRunner, choose_distro, decode_wsl, download_args, metadata_args, parse_progress as _parse_progress

TEST_DIR = "/tmp/wmd-test-media"
TEST_CONFIG = Config("testuser", TEST_DIR, "Ubuntu")
parse_progress = partial(_parse_progress, download_dir=TEST_DIR)

URL = "https://www.youtube.com/watch?v=test123&list=PL123&index=2"


class NameTests(unittest.TestCase):
    def test_sanitization(self):
        self.assertEqual(safe_stem(' A/B:C?*<>|"\\. '), "A_B_C_______")
        self.assertEqual(safe_stem("NUL.txt"), "_NUL.txt")
        self.assertEqual(safe_stem(".."), "Untitled")
        self.assertEqual(safe_stem("曲目 🎵"), "曲目 🎵")
        self.assertLessEqual(len(safe_stem("音" * 200).encode()), 180)

    def test_normalization_and_stems(self):
        self.assertEqual(name_key("CAFE\u0301"), name_key("café"))
        self.assertEqual(file_stems(["a.b.mp3", "a.part", "b.ytdl", "c.part-Frag3"]), ["a.b"])
        self.assertEqual(unique_stem("Song", ["song", "Song (2)"]), "Song (3)")

    def test_duplicates_ignore_extension_and_check_queue(self):
        q = DownloadQueue()
        self.assertTrue(q.duplicates("Song", ["Song.webm"]))
        t = q.add(URL, Mode.MP3, "Song", ["Song.webm"])
        self.assertEqual(t.stem, "Song (2)")
        self.assertTrue(q.duplicates("Song", []))
        self.assertEqual(q.add(URL, Mode.VIDEO, "Song", ["Song.mp3"]).stem, "Song (3)")


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.q = DownloadQueue()
        self.a = self.q.add(URL, Mode.VIDEO, "A", [])
        self.b = self.q.add(URL, Mode.MP3, "B", [])

    def test_failure_advances_without_parallel_downloads(self):
        self.assertIs(self.q.next_task(), self.a)
        self.assertIsNone(self.q.next_task())
        self.a.status = Status.CONVERTING
        self.assertIsNone(self.q.next_task())
        self.a.status = Status.FAILED
        self.assertIs(self.q.next_task(), self.b)

    def test_cancel_and_retry(self):
        self.q.cancel_task(self.a)
        self.assertEqual(self.a.status, Status.CANCELLED)
        self.assertIs(self.q.next_task(), self.b)
        self.q.cancel_task(self.b)
        self.assertTrue(self.b.cancel.is_set())
        self.assertEqual(self.b.status, Status.DOWNLOADING)
        self.q.retry(self.a)
        self.assertFalse(self.a.cancel.is_set())
        self.assertEqual(self.a.status, Status.WAITING)

    def test_clear_only_completed(self):
        self.a.status = Status.COMPLETED
        self.b.status = Status.FAILED
        self.q.clear_completed()
        self.assertEqual(self.q.tasks, [self.b])


class CommandTests(unittest.TestCase):
    def test_urls_are_single_arguments(self):
        self.assertEqual(validate_url(URL), URL)
        args = metadata_args(URL)
        self.assertEqual(args[-2:], ["--", URL])
        command = WslRunner(config=TEST_CONFIG).command("yt-dlp", *args)
        self.assertEqual(command[-1], URL)
        self.assertIn("--exec", command)
        self.assertNotIn("bash", command)
        for url in ["--exec=evil", "https://youtube.com.evil.test/watch?v=x", "file:///etc/passwd", "https://user@youtube.com/x", "https://youtube.com/x\nwhoami"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_mode_flags_and_literal_percent(self):
        task = DownloadQueue().add(URL, Mode.VIDEO, "100% %(id)s & Music", [])
        args = download_args(task, TEST_DIR)
        self.assertEqual(args[args.index("--output")+1], "100%% %%(id)s & Music.%(ext)s")
        self.assertEqual(args[args.index("--format")+1], "bv+ba/b")
        self.assertIn("mkv", args)
        self.assertNotIn("--recode-video", args)
        self.assertIn("--no-playlist", args)
        self.assertIn("--no-overwrites", args)
        self.assertNotIn("eta", " ".join(args).lower())
        task.mode = Mode.MP3
        args = download_args(task, TEST_DIR)
        self.assertEqual(args[args.index("--audio-quality")+1], "0")
        self.assertIn("--embed-thumbnail", args)
        self.assertIn("--embed-metadata", args)

    def test_machine_progress(self):
        self.assertEqual(parse_progress('__WMD_PROGRESS__:{"downloaded_bytes":50,"total_bytes":200}'), ("progress", 25.0))
        self.assertEqual(parse_progress('__WMD_PROGRESS__:{"downloaded_bytes":100,"total_bytes":100}'), ("progress", 99.0))
        self.assertIsNone(parse_progress('__WMD_PROGRESS__:{"total_bytes":"NA"}'))
        self.assertIsNone(parse_progress('__WMD_PROGRESS__:oops'))
        self.assertEqual(parse_progress("[Merger] Merging formats"), ("status", Status.CONVERTING))
        self.assertEqual(parse_progress('__WMD_FILE__:"/tmp/wmd-test-media/a.mp3"'), ("output", "/tmp/wmd-test-media/a.mp3"))
        self.assertIsNone(parse_progress('__WMD_FILE__:"/etc/passwd"'))

    def test_distro_and_explorer(self):
        self.assertEqual(choose_distro(["docker-desktop", "Ubuntu"]), "Ubuntu")
        self.assertEqual(choose_distro(["Ubuntu-24.04"]), "Ubuntu-24.04")
        self.assertEqual(decode_wsl("Ubuntu\r\n".encode("utf-16-le")), "Ubuntu\r\n")
        self.assertEqual(WslRunner(config=TEST_CONFIG).explorer_path(), r"\\wsl.localhost\Ubuntu\tmp\wmd-test-media")


if __name__ == "__main__":
    unittest.main()
