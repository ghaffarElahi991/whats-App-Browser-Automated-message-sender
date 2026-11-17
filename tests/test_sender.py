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
    composer_contains_complete_message,
    contact_result_matches,
    dialog_indicates_unavailable_contact,
    digits_only,
    normalize_composer_text,
    normalize_dialog_text,
    search_text_indicates_no_result,
    summarize_browser_error,
    text_looks_like_contact_result,
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


class StaleButton(FakeButton):
    def __init__(self, driver, _label: str) -> None:
        self.driver = driver

    @property
    def text(self):
        from selenium.common.exceptions import StaleElementReferenceException

        raise StaleElementReferenceException("React replaced the button")


class StaleDialog(FakeDialog):
    def find_elements(self, _by, _selector):
        return [StaleButton(self.driver, "unused")]


class FakeSwitchTo:
    def __init__(self) -> None:
        self.active_element = self

    def send_keys(self, _keys) -> None:
        return


class DismissingSwitchTo(FakeSwitchTo):
    def __init__(self, driver) -> None:
        super().__init__()
        self.driver = driver

    def send_keys(self, _keys) -> None:
        self.driver.dialog = None
        self.driver.dismissed_dialogs += 1


class FakeWhatsAppDriver:
    def __init__(self) -> None:
        self.dialog = None
        self.dismissed_dialogs = 0
        self.switch_to = FakeSwitchTo()

    def find_elements(self, _by, selector: str):
        if "role='dialog'" in selector:
            return [self.dialog] if self.dialog is not None else []
        return []

    def execute_script(self, _script: str, button: FakeButton) -> None:
        button.click()


class FakeContactResult:
    def __init__(self, text: str) -> None:
        self.text = text

    def is_displayed(self) -> bool:
        return True


class FakeResultDriver:
    def __init__(self, list_items: list[FakeContactResult]) -> None:
        self.list_items = list_items

    def find_elements(self, _by, selector: str):
        if selector == "div[role='listitem']":
            return self.list_items
        return []


class ImmediateWait:
    def __init__(self, driver, _timeout: float) -> None:
        self.driver = driver

    def until(self, condition):
        result = condition(self.driver)
        if not result:
            raise AssertionError("Immediate wait condition was not satisfied")
        return result


class FakeKeys:
    COMMAND = "COMMAND"
    CONTROL = "CONTROL"
    BACKSPACE = "BACKSPACE"
    SHIFT = "SHIFT"
    ENTER = "ENTER"


class FakeComposerDriver:
    def __init__(self) -> None:
        self.composer = None

    def find_elements(self, _by, selector: str):
        if selector.startswith("footer div[contenteditable") and self.composer:
            return [self.composer]
        return []


class FakeComposer:
    def __init__(
        self,
        driver: FakeComposerDriver,
        text: str = "",
        *,
        stale_on_click: bool = False,
        stale_on_enter: bool = False,
        replacement=None,
    ) -> None:
        self.driver = driver
        self.text = text
        self.stale_on_click = stale_on_click
        self.stale_on_enter = stale_on_enter
        self.replacement = replacement
        self.enter_attempts = 0
        self.selected_all = False

    def is_displayed(self) -> bool:
        return True

    def click(self) -> None:
        if self.stale_on_click:
            from selenium.common.exceptions import StaleElementReferenceException

            self.driver.composer = self.replacement
            raise StaleElementReferenceException("Composer replaced after click")

    def get_attribute(self, name: str):
        return self.text if name in {"innerText", "textContent"} else None

    def send_keys(self, *values) -> None:
        if values == (FakeKeys.CONTROL, "a") or values == (
            FakeKeys.COMMAND,
            "a",
        ):
            self.selected_all = True
            return
        if values == (FakeKeys.BACKSPACE,):
            if self.selected_all:
                self.text = ""
                self.selected_all = False
            return
        if values == (FakeKeys.SHIFT, FakeKeys.ENTER):
            self.text += "\n"
            return
        if values == (FakeKeys.ENTER,):
            from selenium.common.exceptions import StaleElementReferenceException

            self.enter_attempts += 1
            if self.stale_on_enter:
                self.driver.composer = self.replacement
                raise StaleElementReferenceException("Composer replaced at Enter")
            self.text = ""
            return
        self.text += "".join(str(value) for value in values)


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

    def test_phone_search_matches_formatted_result(self) -> None:
        self.assertEqual(digits_only("+60 18-905 2999"), "60189052999")
        self.assertTrue(
            contact_result_matches("Workshop\n+60 18-905 2999", "60189052999")
        )
        self.assertFalse(contact_result_matches("+60 19-770 4052", "60189052999"))

    def test_composer_message_comparison_ignores_rendering_characters(self) -> None:
        self.assertEqual(normalize_composer_text("Hello\u200b\n world"), "Hello world")
        self.assertTrue(
            composer_contains_complete_message("Hello\nworld", "Hello\nworld")
        )
        self.assertFalse(composer_contains_complete_message("Hello", "Hello world"))

    def test_no_contact_search_result_is_recognized(self) -> None:
        self.assertTrue(search_text_indicates_no_result("No contacts found"))
        self.assertTrue(search_text_indicates_no_result("Invite to WhatsApp"))
        self.assertFalse(search_text_indicates_no_result("Workshop +60 18-905 2999"))

    def test_action_row_is_not_treated_as_contact(self) -> None:
        self.assertFalse(text_looks_like_contact_result("New contact"))
        self.assertFalse(text_looks_like_contact_result("Invite to WhatsApp"))
        self.assertTrue(text_looks_like_contact_result("Saved workshop name"))

    def test_single_saved_name_result_is_selected(self) -> None:
        from selenium.webdriver.common.by import By

        sender = self.make_sender()
        result = FakeContactResult("Saved workshop name")
        driver = FakeResultDriver([result])

        self.assertIs(
            sender._matching_contact_result(driver, By, "60189052999"), result
        )

    def test_multiple_name_only_results_are_not_guessed(self) -> None:
        from selenium.webdriver.common.by import By

        sender = self.make_sender()
        driver = FakeResultDriver(
            [FakeContactResult("Workshop one"), FakeContactResult("Workshop two")]
        )

        self.assertIsNone(sender._matching_contact_result(driver, By, "60189052999"))

    def test_stale_composer_is_reacquired_while_building_draft(self) -> None:
        from selenium.webdriver.common.by import By

        sender = self.make_sender()
        driver = FakeComposerDriver()
        fresh_composer = FakeComposer(driver)
        stale_composer = FakeComposer(
            driver,
            stale_on_click=True,
            replacement=fresh_composer,
        )
        driver.composer = stale_composer
        sender.driver = driver

        with patch("whatsapp_tool.sender.time.sleep", return_value=None):
            result = sender._prepare_message_draft(
                Contact(4, "+601", phone="60111111111"),
                "Hello\nworld",
                By,
                FakeKeys,
                ImmediateWait,
            )

        self.assertIs(result, fresh_composer)
        self.assertEqual(fresh_composer.text, "Hello\nworld")

    def test_stale_enter_does_not_duplicate_send(self) -> None:
        from selenium.webdriver.common.by import By

        sender = self.make_sender()
        driver = FakeComposerDriver()
        cleared_composer = FakeComposer(driver)
        stale_composer = FakeComposer(
            driver,
            text="Hello world",
            stale_on_enter=True,
            replacement=cleared_composer,
        )
        driver.composer = stale_composer
        sender.driver = driver

        with patch("whatsapp_tool.sender.time.sleep", return_value=None):
            sender._press_send_once(
                Contact(4, "+601", phone="60111111111"),
                "Hello world",
                By,
                FakeKeys,
                ImmediateWait,
            )

        self.assertEqual(stale_composer.enter_attempts, 1)
        self.assertEqual(cleared_composer.enter_attempts, 0)

    def test_empty_draft_before_enter_is_not_reported_as_sent(self) -> None:
        from selenium.webdriver.common.by import By

        sender = self.make_sender()
        driver = FakeComposerDriver()
        driver.composer = FakeComposer(driver)
        sender.driver = driver

        with self.assertRaisesRegex(BrowserAutomationError, "draft disappeared"):
            sender._press_send_once(
                Contact(4, "+601", phone="60111111111"),
                "Hello world",
                By,
                FakeKeys,
                ImmediateWait,
            )

        self.assertEqual(driver.composer.enter_attempts, 0)

    def test_stale_dialog_is_closed_before_next_contact(self) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        sender = self.make_sender()
        driver = FakeWhatsAppDriver()
        driver.dialog = FakeDialog(driver, "Previous number is not on WhatsApp")
        sender.driver = driver

        with patch("whatsapp_tool.sender.time.sleep", return_value=None):
            sender._clear_stale_dialogs(By, Keys)

        self.assertEqual(driver.dismissed_dialogs, 1)
        self.assertIsNone(driver.dialog)

    def test_unavailable_dialog_can_be_closed_cleanly(self) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        sender = self.make_sender()
        driver = FakeWhatsAppDriver()
        driver.dialog = FakeDialog(driver, "This phone number isn't on WhatsApp")
        sender.driver = driver

        self.assertTrue(
            dialog_indicates_unavailable_contact(sender._dialog_text(driver.dialog))
        )
        self.assertTrue(sender._dismiss_dialog(driver.dialog, By, Keys))

        self.assertEqual(driver.dismissed_dialogs, 1)
        self.assertIsNone(driver.dialog)

    def test_rerendered_dialog_falls_back_to_escape(self) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        sender = self.make_sender()
        driver = FakeWhatsAppDriver()
        driver.switch_to = DismissingSwitchTo(driver)
        driver.dialog = StaleDialog(driver, "")
        sender.driver = driver

        self.assertTrue(sender._dismiss_dialog(driver.dialog, By, Keys))
        self.assertEqual(driver.dismissed_dialogs, 1)
        self.assertIsNone(driver.dialog)

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
