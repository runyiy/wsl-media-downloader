# Validation: version 1.1.0

- **25 tests passed**, including a real Windows Tk event loop and opt-in Ubuntu WSL checks. No tests were skipped in the final suite.
- The packaged EXE smoke test opened and closed successfully with exit code 0.
- Configuration tests cover missing settings, unchanged placeholders, invalid paths/usernames, quoted values, environment overrides, literal parsing, and configuration lookup beside the packaged EXE.
- Configured usernames and directories are passed as individual arguments to WSL. File listing, media output, final-file validation, the GUI folder display, and Explorer use the selected directory.
- Argument round-tripping preserved URL parameters, quotes, Unicode, spaces, percent signs, and shell-looking text as literal data.
- Sequential processing, failure continuation, cancellation of descendants, timeouts, and retry behavior passed.
- One-second synthetic media verified MKV merging with preserved video/audio codecs, MP3 conversion, JPEG cover embedding, and title metadata. Test media were created in temporary folders and removed automatically.
- A privacy scan examined source files and expanded executable entries, including compressed Python bytecode and bundled runtime archives. No project owner's personal username, local account identifier, email, or workspace path markers were found.
- Actual `.env` files, logs, and local configuration are excluded from source control and release packaging. The release includes only `.env.example` with placeholders.

The Windows x64 executable is unsigned and uses existing WSL media tools. Its SHA-256 is:

```text
837d8da7948549ea8c1399f2f532c461459fa3210cd5f7f6657383e03ed20282
```

These checks establish local behavior and processing correctness. Individual video availability and future YouTube compatibility depend on the source service and the installed yt-dlp/JavaScript runtime.
