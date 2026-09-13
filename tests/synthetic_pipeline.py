"""Runs inside Ubuntu via the integration tests; creates disposable synthetic media."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def main():
    config = json.loads(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="wmd-pipeline-") as temporary:
        folder = Path(temporary)
        video, audio, cover = folder / "v.mp4", folder / "a.m4a", folder / "cover.jpg"
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=1", "-an", "-c:v", "mpeg4", str(video)])
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(audio)])
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1", "-frames:v", "1", str(cover)])
        info = {"id": "synthetic", "title": "Synthetic test", "extractor": "generic", "extractor_key": "Generic",
                "webpage_url": "https://example.invalid/synthetic", "duration": 1,
                "thumbnail": cover.as_uri(), "thumbnails": [{"url": cover.as_uri()}],
                "formats": [
                    {"format_id": "v", "url": video.as_uri(), "ext": "mp4", "vcodec": "mpeg4", "acodec": "none", "width": 160, "height": 90, "fps": 10},
                    {"format_id": "a", "url": audio.as_uri(), "ext": "m4a", "vcodec": "none", "acodec": "aac", "abr": 128}]}
        infofile = folder / "info.json"
        infofile.write_text(json.dumps(info), encoding="utf-8")
        results = {}
        for mode, args in config["commands"].items():
            output = folder / mode
            output.mkdir()
            args = list(args[:-2])  # Remove the public URL; consume only our local info JSON.
            args[args.index("--paths") + 1] = str(output)
            args += ["--enable-file-urls", "--load-info-json", str(infofile)]
            stdout = run([config["ytdlp"], *args])
            final_lines = [line.split(":", 1)[1] for line in stdout.splitlines() if line.startswith("__WMD_FILE__:")]
            assert final_lines, stdout
            final = Path(json.loads(final_lines[-1]))
            assert final.is_file() and final.stat().st_size > 0
            assert final.stem == config["stem"], final.name
            details = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(final)]))
            streams = details["streams"]
            if mode == "VIDEO":
                assert final.suffix == ".mkv"
                assert {s["codec_name"] for s in streams} == {"mpeg4", "aac"}, details
            else:
                assert final.suffix == ".mp3"
                assert any(s["codec_name"] == "mp3" for s in streams), details
                assert any(s.get("disposition", {}).get("attached_pic") for s in streams), details
                assert details["format"]["tags"]["title"] == "Synthetic test", details
            assert "__WMD_PROGRESS__:" in stdout, stdout
            results[mode] = {"file": final.name, "codecs": [s["codec_name"] for s in streams], "verified": True}
        print(json.dumps(results))


if __name__ == "__main__":
    main()
