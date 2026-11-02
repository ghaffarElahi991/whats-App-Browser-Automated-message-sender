import threading
import unittest
from pathlib import Path

from whatsapp_tool.models import Contact, ContactState
from whatsapp_tool.sender import (
    BrowserAutomationError,
    SenderSettings,
    WhatsAppWebSender,
    build_chrome_environment,
)


class FakeSender(WhatsAppWebSender):
    def _start_browser(self) -> None:
        return

    def _wait_for_login(self) -> None:
        return

    def _open_and_send(self, contact: Contact, message: str) -> None:
        if contact.phone.endswith("2"):
            raise BrowserAutomationError("Test failure")

    def _wait_between_messages(self, seconds: int) -> bool:
        return True


class StartFailureSender(FakeSender):
    def _start_browser(self) -> None:
        raise BrowserAutomationError("Chrome test failure")


class SenderTests(unittest.TestCase):
    def test_chrome_environment_removes_snap_gtk_paths(self) -> None:
        original = {
            "PATH": "/usr/bin",
            "GSETTINGS_SCHEMA_DIR": "/home/test/snap/code/1/schemas",
            "GTK_PATH": "/snap/code/1/usr/lib/gtk-3.0",
            "XDG_DATA_DIRS": "/snap/code/1/usr/share:/usr/share",
            "XDG_DATA_DIRS_VSCODE_SNAP_ORIG": "/usr/share/ubuntu:/usr/share",
        }

        cleaned = build_chrome_environment(original)

        self.assertNotIn("GSETTINGS_SCHEMA_DIR", cleaned)
        self.assertNotIn("GTK_PATH", cleaned)
        self.assertEqual(cleaned["XDG_DATA_DIRS"], "/usr/share/ubuntu:/usr/share")
        self.assertIn("GSETTINGS_SCHEMA_DIR", original)

    def test_regular_chrome_environment_is_unchanged(self) -> None:
        original = {"PATH": "/usr/bin", "XDG_DATA_DIRS": "/usr/share"}
        self.assertEqual(build_chrome_environment(original), original)

    def test_settings_reject_unsafe_delay(self) -> None:
        with self.assertRaisesRegex(ValueError, "15 and 300"):
            SenderSettings(Path("profile"), delay_seconds=5).validate()

    def test_run_continues_after_one_contact_fails(self) -> None:
        contacts = [
            Contact(2, "+601", phone="60111111111"),
            Contact(3, "+602", phone="60111111112"),
        ]
        statuses = []
        progress = []
        run_event = threading.Event()
        run_event.set()
        sender = FakeSender(
            SenderSettings(Path("profile"), delay_seconds=15),
            stop_event=threading.Event(),
            run_event=run_event,
            on_status=statuses.append,
            on_log=lambda _message: None,
            on_progress=lambda done, total: progress.append((done, total)),
            on_fatal=lambda _message: None,
        )
        with self.assertLogs("mayfair_whatsapp.sender", level="WARNING"):
            sender.run(contacts, "Hello")

        final_states = [
            result.state for result in statuses if result.state != ContactState.SENDING
        ]
        self.assertEqual(final_states, [ContactState.SENT, ContactState.FAILED])
        self.assertEqual(progress, [(1, 2), (2, 2)])

    def test_browser_start_failure_is_reported_as_fatal(self) -> None:
        run_event = threading.Event()
        run_event.set()
        fatal_messages = []
        sender = StartFailureSender(
            SenderSettings(Path("profile"), delay_seconds=15),
            stop_event=threading.Event(),
            run_event=run_event,
            on_status=lambda _result: None,
            on_log=lambda _message: None,
            on_progress=lambda _done, _total: None,
            on_fatal=fatal_messages.append,
        )

        with self.assertLogs("mayfair_whatsapp.sender", level="ERROR"):
            sender.run([Contact(2, "+601", phone="60111111111")], "Hello")

        self.assertEqual(fatal_messages, ["Chrome test failure"])


if __name__ == "__main__":
    unittest.main()
