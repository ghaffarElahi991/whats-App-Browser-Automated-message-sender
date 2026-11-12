import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from whatsapp_tool.models import Contact, ContactState
from whatsapp_tool.sender import (
    BrowserAutomationError,
    SenderSettings,
    WhatsAppWebSender,
    build_chrome_environment,
    dialog_indicates_unavailable_contact,
    normalize_dialog_text,
    summarize_browser_error,
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


class FakeButton:
    def __init__(self, driver, label: str) -> None:
        self.driver = driver
        self.text = label

    def get_attribute(self, _name: str):
        return None

    def click(self) -> None:
        self.driver.dialog = None
        self.driver.dismissed_dialogs += 1


class FakeDialog:
    def __init__(self, driver, text: str) -> None:
        self.driver = driver
        self.text = text

    def is_displayed(self) -> bool:
        return True

    def find_elements(self, _by, _selector):
        return [FakeButton(self.driver, "OK")]


class FakeComposer:
    def __init__(self) -> None:
        self.content = "Hello"
        self.sent = False

    def is_displayed(self) -> bool:
        return True

    def get_attribute(self, name: str):
        return self.content if name == "textContent" else None

    def send_keys(self, _keys) -> None:
        self.content = ""
        self.sent = True

    def click(self) -> None:
        raise AssertionError("The composer must not be clicked through an overlay")


class FakeBody:
    def __init__(self, text: str = "") -> None:
        self.text = text


class FakeSwitchTo:
    def __init__(self) -> None:
        self.active_element = self

    def send_keys(self, _keys) -> None:
        return


class FakeWhatsAppDriver:
    def __init__(self, current_dialog: str | None = None) -> None:
        self.current_dialog = current_dialog
        self.dialog = None
        self.composer = FakeComposer()
        self.dismissed_dialogs = 0
        self.switch_to = FakeSwitchTo()

    def get(self, _url: str) -> None:
        if self.current_dialog:
            self.dialog = FakeDialog(self, self.current_dialog)
        else:
            self.dialog = None

    def find_elements(self, _by, selector: str):
        if "role='dialog'" in selector:
            return [self.dialog] if self.dialog is not None else []
        if selector == "footer div[contenteditable='true'][role='textbox']":
            return [] if self.dialog is not None else [self.composer]
        if selector == "body":
            return [FakeBody()]
        return []

    def execute_script(self, _script: str, button: FakeButton) -> None:
        button.click()


class SenderTests(unittest.TestCase):
    @staticmethod
    def make_sender() -> WhatsAppWebSender:
        run_event = threading.Event()
        run_event.set()
        return WhatsAppWebSender(
            SenderSettings(Path("profile"), delay_seconds=15),
            stop_event=threading.Event(),
            run_event=run_event,
            on_status=lambda _result: None,
            on_log=lambda _message: None,
            on_progress=lambda _done, _total: None,
            on_fatal=lambda _message: None,
        )

    def test_dialog_text_is_normalized(self) -> None:
        self.assertEqual(
            normalize_dialog_text("  Phone NUMBER\n is  not on WhatsApp "),
            "phone number is not on whatsapp",
        )

    def test_unavailable_number_dialog_is_recognized(self) -> None:
        messages = (
            "Phone number shared via URL is invalid",
            "This phone number isn't on WhatsApp",
            "Could not find this phone number",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(dialog_indicates_unavailable_contact(message))

    def test_unrelated_dialog_is_not_marked_as_unavailable(self) -> None:
        self.assertFalse(dialog_indicates_unavailable_contact("Turn on notifications?"))

    def test_browser_error_summary_removes_stacktrace(self) -> None:
        error = RuntimeError("Message: element click intercepted\nStacktrace:\nlong")
        self.assertEqual(summarize_browser_error(error), "element click intercepted")

    def test_stale_dialog_is_closed_before_next_contact(self) -> None:
        sender = self.make_sender()
        driver = FakeWhatsAppDriver()
        driver.dialog = FakeDialog(driver, "Previous number is not on WhatsApp")
        sender.driver = driver

        with patch("whatsapp_tool.sender.time.sleep", return_value=None):
            sender._open_and_send(Contact(3, "+601", phone="60111111111"), "Hello")

        self.assertEqual(driver.dismissed_dialogs, 1)
        self.assertTrue(driver.composer.sent)

    def test_unavailable_dialog_is_closed_and_contact_fails_cleanly(self) -> None:
        sender = self.make_sender()
        driver = FakeWhatsAppDriver("This phone number isn't on WhatsApp")
        sender.driver = driver

        with (
            patch("whatsapp_tool.sender.time.sleep", return_value=None),
            self.assertRaisesRegex(BrowserAutomationError, "not available on WhatsApp"),
        ):
            sender._open_and_send(Contact(4, "+602", phone="60111111112"), "Hello")

        self.assertEqual(driver.dismissed_dialogs, 1)
        self.assertFalse(driver.composer.sent)

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
