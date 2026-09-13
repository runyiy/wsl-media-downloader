"""Small, literal .env reader. Personal settings never belong in source control."""
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import sys

KEYS = {"WSL_DISTRO", "WSL_USER", "DOWNLOAD_DIR"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    user: str
    download_dir: str
    distro: str | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", self.user) or self.user == "your-linux-username":
            raise ConfigError("Set WSL_USER to your actual Ubuntu username in .env.")
        if self.distro and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.distro):
            raise ConfigError("WSL_DISTRO must be an exact installed distribution name, such as Ubuntu.")
        path = PurePosixPath(self.download_dir)
        if (not self.download_dir.startswith("/") or self.download_dir.startswith("//")
                or str(path) == "/" or ".." in path.parts
                or any(c in self.download_dir for c in '\\<>:"|?*')
                or any(ord(c) < 32 for c in self.download_dir)
                or "your-linux-username" in self.download_dir):
            raise ConfigError("Set DOWNLOAD_DIR to your own absolute Linux folder path; replace the example placeholder.")
        object.__setattr__(self, "download_dir", str(path))


def default_config_path() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return base / ".env"


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Invalid .env line {number}: expected KEY=value.")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in KEYS or key in values:
            raise ConfigError(f"Unknown or repeated setting on .env line {number}.")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError(f"Unclosed quote on .env line {number}.")
            value = value[1:-1]
        # No evaluation, shell expansion, variable interpolation, or inline comments.
        values[key] = value
    return values


def load_config(path: Path | None = None, environ=None) -> Config:
    path = path or default_config_path()
    values = parse_env(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    environ = os.environ if environ is None else environ
    values.update({key: environ[key] for key in KEYS if key in environ})
    if not values.get("WSL_USER") or not values.get("DOWNLOAD_DIR"):
        raise ConfigError("Configuration needed. Copy .env.example to .env beside the EXE "
                          "(or in the source project), set WSL_USER and DOWNLOAD_DIR, then click Check Environment. "
                          "You can also supply these environment variables.")
    return Config(values["WSL_USER"], values["DOWNLOAD_DIR"], values.get("WSL_DISTRO") or None)
