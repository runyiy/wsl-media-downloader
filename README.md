# WSL Media Downloader

A small Windows desktop app for downloading YouTube videos through your existing Ubuntu WSL tools. Paste a URL, choose a mode, and click **Add to Queue**. No command line is needed for everyday use.

Each user supplies their own WSL username and download folder in a private **`.env`** file. No personal username or machine-specific download path is built into the app. See **First-time configuration** below.

## Features

- **Best Video + Best Audio:** requests `bv+ba/b` and merges separate streams into MKV using stream copying. It does not transcode video or force MP4. A source with an already combined stream can retain its original container.
- **MP3 + Cover:** downloads the best available audio, converts with FFmpeg quality `0` (highest-quality VBR), embeds metadata and the thumbnail as JPEG cover art when available. MP3 is lossy; conversion cannot improve the original source.
- A sequential queue with **Type, Title, Progress, Status**. Failed items do not block later items.
- Waiting, Downloading, Converting, Completed, Failed, and Cancelled states. Converting also covers merging and metadata/cover processing.
- Percentage only, with no speed or remaining-time display. The percentage describes the current stream, so it can restart when audio follows video. Unknown totals show a dash. Only a verified final file earns **Completed / 100%**; processing stays below 100%.
- Cancel selected, retry failed/cancelled items, clear completed rows, open the WSL folder, and view/copy diagnostic logs.
- Title-based duplicate warnings across both the queue and the destination folder.

## Prerequisites

1. Windows 10/11 with WSL and Ubuntu installed, and your own Linux user available.
2. Python 3, a current **yt-dlp**, **ffmpeg**, and **ffprobe** inside Ubuntu. Windows copies of these tools are not used.
3. For source use/builds: Windows Python 3.11 or newer with Tcl/Tk (included by the standard python.org installer). The packaged EXE includes its own Python/Tk runtime.

If WSL is not installed, run this once in an administrator PowerShell, restart if prompted, and complete Ubuntu's initial user setup:

```powershell
wsl --install -d Ubuntu
```

Example tool setup **inside Ubuntu**:

```bash
sudo apt update
sudo apt install -y python3 python3-venv pipx ffmpeg
pipx install 'yt-dlp[default]'
pipx ensurepath
mkdir -p "$HOME/Downloads/music"
```

If yt-dlp is already managed by pipx, update it with `pipx upgrade yt-dlp`. Use the appropriate update method for other installations. The app also searches `~/.local/bin`, `~/.deno/bin`, and `~/bin`, so it does not depend on interactive shell startup files.

Modern YouTube extraction can require a JavaScript runtime. Install [Deno using its official instructions](https://docs.deno.com/runtime/getting_started/installation/) **inside Ubuntu** and keep yt-dlp and its JavaScript support current. Missing Deno produces a warning rather than blocking the entire app. Some videos may still fail without it.

References: [yt-dlp installation and options](https://github.com/yt-dlp/yt-dlp#installation), [YouTube JavaScript support](https://github.com/yt-dlp/yt-dlp/wiki/EJS), and [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).

## First-time configuration

Copy the included `.env.example` to `.env`, then edit the two placeholder values:

```dotenv
WSL_DISTRO=Ubuntu
WSL_USER=your-linux-username
DOWNLOAD_DIR=/home/your-linux-username/Downloads/music
```

Replace `your-linux-username` in **both** settings with your own Linux username, or choose a different absolute Linux download directory. Run `whoami` and `echo "$HOME"` inside Ubuntu if you need to check your local values. The example intentionally cannot be used unchanged.

- **Packaged EXE:** keep your `.env` beside `WSLMediaDownloader.exe`. The Windows ZIP includes `.env.example`; extract the ZIP before editing or running it.
- **Source:** keep `.env` in the project root, beside `.env.example`.
- **Custom location:** pass `--config 'C:\Path\To\private.env'` when starting the EXE or source app.
- Real environment variables with the same names override file values. `--distro` overrides `WSL_DISTRO`. A blank `WSL_DISTRO` enables Ubuntu auto-detection.

The file uses literal `KEY=value` lines. Optional matching quotes, Unicode, and spaces in folder names are supported. Use full-line `#` comments. Values are never executed or expanded: do not use `~`, `$HOME`, or `${OTHER_VARIABLE}` as shortcuts.

Without valid configuration, the GUI displays setup instructions and disables downloads. After editing `.env`, click **Check Environment** while the queue is idle to reload it.

**Keep `.env` private.** `.gitignore` excludes `.env`, `.env.*`, local settings, logs, and build output; only `.env.example` is tracked. The build copies only the example configuration into release output. Do not put actual settings in the example, source, screenshots, or release notes. Runtime logs may contain configured paths, titles, and URLs; they remain local and should be reviewed before sharing.

## Run

**EXE:** extract the Windows ZIP and double-click `WSLMediaDownloader.exe` (`dist\WSLMediaDownloader.exe` in a source/build folder). It can be moved elsewhere together with your private `.env`; no sibling source files are required. Ubuntu and its tools must remain installed. The executable is unsigned.

**From source**, in a Windows terminal opened in this project:

```powershell
py -m wsl_media_downloader
```

Or double-click `launcher.pyw` when `.pyw` is associated with Windows Python. No third-party Python package is required to run the source app.

The app prefers an exact `Ubuntu` distribution, otherwise it accepts a single installed Ubuntu variant. If you have several variants:

```powershell
py -m wsl_media_downloader --distro Ubuntu-24.04
```

The EXE supports the same `--distro` option. **Open Folder** uses `\\wsl.localhost\<distribution>\<configured Linux directory>`.

## Everyday use

1. Complete the one-time `.env` setup and wait for the environment status to say **Ready**.
2. Paste an individual YouTube video URL.
3. Select **Best Video + Best Audio** or **MP3 + Cover**.
4. Click **Add to Queue**, or press Enter in the URL field.
5. Add more URLs while the queue downloads. Metadata lookup runs in the background.

**Cancel Selected** works on waiting and active rows. **Retry Failed** retries selected failed/cancelled rows; with no selection, it retries all failed/cancelled rows. **Clear Completed** removes only completed queue rows, never files. Double-click a row or choose **Details / Logs** to view diagnostics.

## Duplicate names and safe files

Before adding an item, the app fetches its title without downloading media, sanitizes it, and compares filename stems while ignoring extensions. Names are Unicode-normalized and compared case-insensitively. For example, `Song.mp3` conflicts with a new video titled `Song`. Dots inside a title are preserved. Windows-invalid characters become underscores, reserved device names are prefixed, and long names are limited to 180 UTF-8 bytes, leaving room for extensions and temporary suffixes.

The duplicate dialog offers **Cancel** or **Download Anyway**. Continuing chooses a free name such as `Song (2).mp3`; it preserves existing files. YouTube IDs are not appended. Partial `.part` / `.ytdl` files are excluded from completed-file duplicate checks, allowing resume. Queue names are reserved even for failed/cancelled rows. `%` in titles is escaped before passing the output template to yt-dlp.

The queue lives in memory and is not restored when the app closes. Closing cancels current work and waits for its worker to stop. Partial files remain in Ubuntu; Retry within the same session reuses the chosen filename. Adding the same URL later can also resume matching partial files. Run one app instance per destination to avoid races with other downloaders creating the same names. `--no-overwrites` provides an additional protection against overwriting existing files.

## Download and cancellation behavior

Every media request includes `--no-playlist`, so a watch URL with `&list=...` downloads only that video. Playlist-only URLs and ongoing live streams are rejected. Network transfers use continuation, unlimited download/fragment retries, a five-second retry delay, and a 30-second socket timeout. A stalled network can therefore keep the active row retrying until cancelled. Metadata requests use bounded retries and a 90-second deadline.

All subprocesses receive argument lists, never shell-concatenated URLs. WSL uses `--exec`. A small fixed Python supervisor in Ubuntu launches yt-dlp in its own process group. Cancellation signals that group, including ffmpeg, and escalates when necessary; it never shuts down the WSL distribution. A per-run token guards against signalling an unrelated reused process ID. Severe WSL service failures can prevent confirmed cleanup and are reported in the logs.

The app ignores yt-dlp configuration files to keep output paths, queue behavior and duplicate checks predictable. Cookies, authentication settings and custom format rules from those files therefore do not apply. Account-restricted videos are outside this first version. Cover art and metadata depend on what the source provides. Download only media you are permitted to save.

## Build the Windows EXE

Build **on Windows**:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

For a specific Python executable:

```powershell
.\build.ps1 -Python 'C:\Path\To\python.exe'
```

The script creates `.venv`, installs the pinned PyInstaller version, runs tests, and creates `dist\WSLMediaDownloader.exe`, `.env.example`, and English documentation. Your actual `.env` is never copied. Equivalent packaging command after installing `requirements-build.txt`:

```powershell
py -m PyInstaller --noconfirm --clean --onefile --windowed --name WSLMediaDownloader launcher.pyw
```

yt-dlp, ffmpeg, ffprobe, and Deno are **not bundled**. Build output and local logs are excluded from Git.

## Tests

```powershell
py -m unittest discover -s tests -v
py -m wsl_media_downloader --smoke-test
```

Unit tests cover filename handling, URL boundaries and quoting, mode flags, progress parsing, queue sequencing, retry, and cancellation. Tk tests run a real event loop with a fake downloader; they skip if no display is available.

Optional integration tests use the local Ubuntu installation, check argument round-tripping and cancellation of descendant processes, and verify both download modes with one-second synthetic video/audio and cover art. They check preserved video/audio codecs, MP3 audio, embedded cover and title metadata, then automatically remove their temporary files. They do not contact YouTube:

```powershell
$env:WMD_WSL_TESTS = '1'
py -m unittest discover -s tests -v
Remove-Item Env:WMD_WSL_TESTS
```

## Troubleshooting

- **WSL setup needed:** read the exact error in **Details / Logs**, confirm Ubuntu starts normally, and use **Check Environment** after fixing it. Python 3 is needed in Ubuntu as well as the media tools.
- **Download failed:** inspect the last error, update yt-dlp in Ubuntu, check Deno/JavaScript support and your network, then retry. One failed row will not stop the next row.
- **Progress stays at 99%:** merging, MP3 conversion or embedding may still be running; use the status column and logs.
- **Explorer cannot open the folder:** start Ubuntu and check the distribution name with `wsl --list --quiet`.
- **Logs:** rotating diagnostic files are stored in `%LOCALAPPDATA%\WSLMediaDownloader\app.log` (up to three approximately 1 MB files). They may contain media titles and URLs; review them before sharing.

## Project layout

```text
wsl_media_downloader/
  config.py     # Private configuration loading and validation
  core.py       # Names, URLs, task model, sequential queue
  runner.py     # WSL commands, environment checks, progress, cancellation
  gui.py        # Tkinter UI and background-worker event handling
tests/          # Unit, GUI, and opt-in local WSL checks
launcher.pyw    # Windowed entry point
build.ps1       # Reproducible Windows packaging workflow
```

MIT licensed. See [LICENSE](LICENSE).
