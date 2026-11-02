"""Cross-platform bootstrapper for the Mayfair WhatsApp Web Sender.

Usage:
    python3.15 run.py       # macOS with Python 3.15
    python3 run.py          # Linux
    python run.py           # Windows
    python3 run.py --install
    python3 run.py --test   # install dependencies and run unit tests
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parent
REQUIREMENTS_FILE = PROJECT_DIRECTORY / "requirements.txt"
MINIMUM_PYTHON = (3, 10)


def venv_directory(
    platform_name: str = sys.platform,
    python_version: tuple[int, int] | None = None,
) -> Path:
    """Use separate environments where copied projects may cross platforms."""

    if platform_name == "darwin":
        version = python_version or (sys.version_info.major, sys.version_info.minor)
        return PROJECT_DIRECTORY / f".venv-macos-{version[0]}.{version[1]}"
    if platform_name == "win32":
        return PROJECT_DIRECTORY / ".venv-windows"
    return PROJECT_DIRECTORY / ".venv"


def venv_python(directory: Path, platform_name: str = sys.platform) -> Path:
    if platform_name == "win32":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def requirements_fingerprint() -> str:
    return hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()


def ensure_runtime() -> Path:
    environment_directory = venv_directory()
    python_executable = venv_python(environment_directory)

    if not python_executable.exists():
        print(f"Creating Python environment: {environment_directory}", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment_directory)

    marker = environment_directory / ".requirements.sha256"
    expected_fingerprint = requirements_fingerprint()
    installed_fingerprint = ""
    try:
        installed_fingerprint = marker.read_text(encoding="utf-8").strip()
    except OSError:
        pass

    if installed_fingerprint != expected_fingerprint:
        print("Installing Python requirements…", flush=True)
        subprocess.run(
            [
                str(python_executable),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(REQUIREMENTS_FILE),
            ],
            cwd=PROJECT_DIRECTORY,
            check=True,
        )
        marker.write_text(expected_fingerprint, encoding="utf-8")

    return python_executable


def tkinter_is_available(python_executable: Path) -> bool:
    result = subprocess.run(
        [str(python_executable), "-c", "import tkinter"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(map(str, MINIMUM_PYTHON))
        current = ".".join(map(str, sys.version_info[:3]))
        print(
            f"Python {required}+ is required; found Python {current}.", file=sys.stderr
        )
        return 1

    try:
        python_executable = ensure_runtime()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Could not prepare the Python environment: {exc}", file=sys.stderr)
        return 1

    if arguments == ["--install"]:
        if not tkinter_is_available(python_executable):
            print(
                "Tkinter is missing from this Python installation. On macOS, "
                "install Python from python.org. On Linux, install your "
                "distribution's python3-tk package, then run the installer again.",
                file=sys.stderr,
            )
            return 1
        print(f"Python environment is ready: {venv_directory()}")
        return 0

    if arguments == ["--test"]:
        return subprocess.run(
            [
                str(python_executable),
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-v",
            ],
            cwd=PROJECT_DIRECTORY,
            check=False,
        ).returncode

    if arguments:
        print("Supported options: --install, --test", file=sys.stderr)
        return 2

    if not tkinter_is_available(python_executable):
        print(
            "Tkinter is missing from this Python installation. On macOS, install "
            "Python from python.org (it includes Tkinter), then run this command again.",
            file=sys.stderr,
        )
        return 1

    app_path = PROJECT_DIRECTORY / "app.py"
    os.execv(str(python_executable), [str(python_executable), str(app_path)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
