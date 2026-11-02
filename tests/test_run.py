import unittest
from pathlib import Path

import run


class BootstrapTests(unittest.TestCase):
    def test_mac_uses_separate_environment(self) -> None:
        self.assertEqual(
            run.venv_directory("darwin", (3, 15)),
            run.PROJECT_DIRECTORY / ".venv-macos-3.15",
        )

    def test_windows_python_path(self) -> None:
        directory = Path("environment")
        self.assertEqual(
            run.venv_python(directory, "win32"),
            directory / "Scripts" / "python.exe",
        )

    def test_linux_keeps_existing_environment_name(self) -> None:
        self.assertEqual(
            run.venv_directory("linux"),
            run.PROJECT_DIRECTORY / ".venv",
        )


if __name__ == "__main__":
    unittest.main()
