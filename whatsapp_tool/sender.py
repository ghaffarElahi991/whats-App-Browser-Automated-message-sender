from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from .diagnostics import get_logger
from .models import Contact, ContactState, SendResult
from .templates import render_message

logger = get_logger("sender")


_SNAP_GTK_VARIABLES = (
    "GSETTINGS_SCHEMA_DIR",
    "GTK_EXE_PREFIX",
    "GTK_PATH",
    "GTK_IM_MODULE_FILE",
)

_UNAVAILABLE_DIALOG_PHRASES = (
    "phone number shared via url is invalid",
    "phone number isn't on whatsapp",
    "phone number is not on whatsapp",
    "number isn't on whatsapp",
    "number is not on whatsapp",
    "invalid phone number",
    "not a valid whatsapp number",
    "couldn't find this phone number",
    "could not find this phone number",
)

_SAFE_DIALOG_ACTIONS = (
    "ok",
    "okay",
    "close",
    "cancel",
    "not now",
    "got it",
    "dismiss",
    "continue to chat",
    "continue",
)


def build_chrome_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Remove inherited Snap/VS Code GTK paths that can crash system Chrome."""

    environment = dict(os.environ if source is None else source)
    snap_gtk_environment = any(
        "/snap/" in environment.get(name, "") for name in _SNAP_GTK_VARIABLES
    )
    if not snap_gtk_environment:
        return environment

    for name in _SNAP_GTK_VARIABLES:
        environment.pop(name, None)

    original_xdg_data = environment.get("XDG_DATA_DIRS_VSCODE_SNAP_ORIG")
    if original_xdg_data:
        environment["XDG_DATA_DIRS"] = original_xdg_data
    return environment


def normalize_dialog_text(text: str) -> str:
    return " ".join(text.lower().split())


def dialog_indicates_unavailable_contact(text: str) -> bool:
    normalized = normalize_dialog_text(text)
    return any(phrase in normalized for phrase in _UNAVAILABLE_DIALOG_PHRASES)


def summarize_browser_error(error: BaseException, limit: int = 240) -> str:
    summary = (
        str(error).strip().splitlines()[0]
        if str(error).strip()
        else type(error).__name__
    )
    if summary.startswith("Message: "):
        summary = summary.removeprefix("Message: ")
    return summary if len(summary) <= limit else f"{summary[: limit - 1]}…"


class BrowserAutomationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SenderSettings:
    profile_directory: Path
    delay_seconds: int = 30
    login_timeout_seconds: int = 180
    chat_timeout_seconds: int = 45

    def validate(self) -> None:
        if not 15 <= self.delay_seconds <= 300:
            raise ValueError("Delay must be between 15 and 300 seconds")
        if not 30 <= self.login_timeout_seconds <= 600:
            raise ValueError("Login timeout must be between 30 and 600 seconds")


StatusCallback = Callable[[SendResult], None]
LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]


class WhatsAppWebSender:
    """Operator-controlled Selenium workflow for WhatsApp Web.

    This class does not hide automation, bypass challenges, or try to evade
    WhatsApp limits. It sends sequentially and requires the UI's consent check.
    """

    def __init__(
        self,
        settings: SenderSettings,
        *,
        stop_event: threading.Event,
        run_event: threading.Event,
        on_status: StatusCallback,
        on_log: LogCallback,
        on_progress: ProgressCallback,
        on_fatal: LogCallback,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.stop_event = stop_event
        self.run_event = run_event
        self.on_status = on_status
        self.on_log = on_log
        self.on_progress = on_progress
        self.on_fatal = on_fatal
        self.driver = None

    @staticmethod
    def _visible_dialogs(driver: object, by: object) -> list[object]:
        dialogs = driver.find_elements(  # type: ignore[attr-defined]
            by.CSS_SELECTOR,
            "div[role='dialog'][aria-modal='true']",  # type: ignore[attr-defined]
        )
        visible: list[object] = []
        for dialog in dialogs:
            try:
                if dialog.is_displayed():  # type: ignore[attr-defined]
                    visible.append(dialog)
            except Exception:  # noqa: BLE001 - stale DOM nodes are ignored
                continue
        return visible

    @staticmethod
    def _dialog_text(dialog: object) -> str:
        try:
            return " ".join(str(dialog.text).split())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - a rerendered dialog may be stale
            return ""

    def _dismiss_dialog(self, dialog: object, by: object, keys: object) -> bool:
        assert self.driver is not None
        from selenium.common.exceptions import WebDriverException

        try:
            buttons = dialog.find_elements(  # type: ignore[attr-defined]
                by.CSS_SELECTOR,
                "button, [role='button']",  # type: ignore[attr-defined]
            )
            for action in _SAFE_DIALOG_ACTIONS:
                for button in buttons:
                    label = normalize_dialog_text(
                        " ".join(
                            filter(
                                None,
                                (
                                    getattr(button, "text", ""),
                                    button.get_attribute("aria-label"),  # type: ignore[attr-defined]
                                    button.get_attribute("title"),  # type: ignore[attr-defined]
                                ),
                            )
                        )
                    )
                    if label != action:
                        continue
                    try:
                        button.click()  # type: ignore[attr-defined]
                    except WebDriverException:
                        self.driver.execute_script("arguments[0].click();", button)
                    return True

            active_element = self.driver.switch_to.active_element
            active_element.send_keys(keys.ESCAPE)  # type: ignore[attr-defined]
            return True
        except WebDriverException:
            logger.exception("Could not dismiss a WhatsApp dialog")
            return False

    def _clear_stale_dialogs(self, by: object, keys: object) -> None:
        assert self.driver is not None
        for _attempt in range(3):
            dialogs = self._visible_dialogs(self.driver, by)
            if not dialogs:
                return
            text = self._dialog_text(dialogs[0])
            logger.info("Dismissing stale WhatsApp dialog: %s", text or "<no text>")
            if not self._dismiss_dialog(dialogs[0], by, keys):
                raise BrowserAutomationError(
                    "A WhatsApp dialog from the previous contact could not be closed."
                )
            time.sleep(0.5)

        if self._visible_dialogs(self.driver, by):
            raise BrowserAutomationError(
                "A WhatsApp dialog from the previous contact is still blocking the page."
            )

    def _start_browser(self) -> None:
        logger.info(
            "Starting Chrome with dedicated profile: %s",
            self.settings.profile_directory,
        )
        try:
            from selenium import webdriver
            from selenium.common.exceptions import WebDriverException
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError as exc:
            raise BrowserAutomationError(
                "Selenium is not installed. Run: pip install -r requirements.txt"
            ) from exc

        try:
            profile = self.settings.profile_directory.expanduser().resolve()
            profile.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BrowserAutomationError(
                f"Chrome session folder could not be created: {exc}"
            ) from exc

        options = Options()
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--remote-debugging-pipe")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        chrome_environment = build_chrome_environment()
        if chrome_environment != dict(os.environ):
            logger.info(
                "Removed inherited Snap/VS Code GTK settings from Chrome environment"
            )
        service = Service(env=chrome_environment)

        try:
            self.driver = webdriver.Chrome(service=service, options=options)
        except (WebDriverException, RuntimeError, OSError) as exc:
            logger.exception("Chrome launch failed")
            raise BrowserAutomationError(
                "Chrome could not start. Close other windows using this sender profile, "
                "confirm Google Chrome is installed, and try again. "
                f"Details: {exc}"
            ) from exc
        logger.info(
            "Chrome started (browser=%s, driver=%s)",
            self.driver.capabilities.get("browserVersion", "unknown"),
            self.driver.capabilities.get("chrome", {}).get(
                "chromedriverVersion", "unknown"
            ),
        )

    def _wait_for_login(self) -> None:
        assert self.driver is not None
        from selenium.common.exceptions import TimeoutException, WebDriverException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        self.on_log("Opening WhatsApp Web. Scan the QR code if asked.")
        selectors = (
            (By.CSS_SELECTOR, "#pane-side"),
            (By.CSS_SELECTOR, "div[aria-label='Chat list']"),
        )

        def logged_in(driver: object) -> bool:
            return any(driver.find_elements(by, selector) for by, selector in selectors)  # type: ignore[attr-defined]

        try:
            self.driver.get("https://web.whatsapp.com/")
            WebDriverWait(self.driver, self.settings.login_timeout_seconds).until(
                logged_in
            )
        except TimeoutException as exc:
            logger.exception("WhatsApp Web login timed out")
            raise BrowserAutomationError(
                "WhatsApp Web login timed out. Scan the QR code and try again."
            ) from exc
        except WebDriverException as exc:
            logger.exception("WhatsApp Web login page failed")
            raise BrowserAutomationError(
                f"WhatsApp Web could not be opened: {exc}"
            ) from exc
        self.on_log("WhatsApp Web is ready.")

    def _open_and_send(self, contact: Contact, message: str) -> None:
        assert self.driver is not None
        from selenium.common.exceptions import TimeoutException, WebDriverException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait

        self._clear_stale_dialogs(By, Keys)

        query = urlencode(
            {
                "phone": contact.phone,
                "text": message,
                "type": "phone_number",
                "app_absent": "0",
            }
        )
        try:
            self.driver.get(f"https://web.whatsapp.com/send?{query}")
        except WebDriverException as exc:
            logger.exception(
                "Opening WhatsApp chat failed for row %s", contact.row_number
            )
            raise BrowserAutomationError(
                f"The WhatsApp chat could not be opened: {exc}"
            ) from exc

        def composer_dialog_or_error(driver: object):
            dialogs = self._visible_dialogs(driver, By)
            if dialogs:
                return "dialog", dialogs[0]
            composers = driver.find_elements(  # type: ignore[attr-defined]
                By.CSS_SELECTOR,
                "footer div[contenteditable='true'][role='textbox']",
            )
            visible_composers = []
            for composer in composers:
                try:
                    if composer.is_displayed():
                        visible_composers.append(composer)
                except WebDriverException:
                    continue
            if visible_composers:
                return "composer", visible_composers[-1]

            bodies = driver.find_elements(By.TAG_NAME, "body")  # type: ignore[attr-defined]
            page_text = bodies[0].text if bodies else ""  # type: ignore[attr-defined]
            return (
                ("invalid", None)
                if dialog_indicates_unavailable_contact(page_text)
                else False
            )

        deadline = time.monotonic() + self.settings.chat_timeout_seconds
        composer = None
        try:
            for _attempt in range(4):
                remaining = max(0.5, deadline - time.monotonic())
                state, element = WebDriverWait(self.driver, remaining).until(
                    composer_dialog_or_error
                )
                if state == "composer":
                    composer = element
                    break
                if state == "invalid":
                    raise BrowserAutomationError(
                        "The number is not available on WhatsApp."
                    )

                dialog_text = self._dialog_text(element)
                logger.info(
                    "WhatsApp dialog for row %s: %s",
                    contact.row_number,
                    dialog_text or "<no text>",
                )
                unavailable = dialog_indicates_unavailable_contact(dialog_text)
                dismissed = self._dismiss_dialog(element, By, Keys)
                if unavailable:
                    raise BrowserAutomationError(
                        "The number is not available on WhatsApp."
                    )
                if not dismissed:
                    summary = dialog_text or "unknown dialog"
                    raise BrowserAutomationError(
                        f"WhatsApp displayed a blocking dialog: {summary[:180]}"
                    )
                time.sleep(0.5)
        except TimeoutException as exc:
            dialogs = self._visible_dialogs(self.driver, By)
            if dialogs:
                dialog_text = self._dialog_text(dialogs[0])
                self._dismiss_dialog(dialogs[0], By, Keys)
                if dialog_indicates_unavailable_contact(dialog_text):
                    raise BrowserAutomationError(
                        "The number is not available on WhatsApp."
                    ) from exc
                raise BrowserAutomationError(
                    f"WhatsApp displayed a blocking dialog: "
                    f"{(dialog_text or 'unknown dialog')[:180]}"
                ) from exc
            logger.exception("Chat timed out for row %s", contact.row_number)
            raise BrowserAutomationError(
                "Timed out while opening the chat. The number may be unavailable, "
                "or WhatsApp Web may have changed."
            ) from exc
        except WebDriverException as exc:
            logger.exception("Chat stopped responding for row %s", contact.row_number)
            raise BrowserAutomationError(
                "WhatsApp Web stopped responding while opening the chat: "
                f"{summarize_browser_error(exc)}"
            ) from exc

        if composer is None:
            raise BrowserAutomationError(
                "The message composer did not become available after closing a dialog."
            )

        # The URL has already filled the editor. Enter performs the explicit send.
        try:
            composer_text = (composer.get_attribute("textContent") or "").strip()
            if not composer_text:
                raise BrowserAutomationError(
                    "WhatsApp opened the chat but did not load the message text."
                )
            composer.send_keys(Keys.ENTER)

            def message_was_sent(driver: object) -> bool:
                dialogs = self._visible_dialogs(driver, By)
                if dialogs:
                    return False
                try:
                    return not (composer.get_attribute("textContent") or "").strip()
                except WebDriverException:
                    return True

            WebDriverWait(self.driver, 10).until(message_was_sent)
        except BrowserAutomationError:
            raise
        except TimeoutException as exc:
            dialogs = self._visible_dialogs(self.driver, By)
            if dialogs:
                dialog_text = self._dialog_text(dialogs[0])
                self._dismiss_dialog(dialogs[0], By, Keys)
                raise BrowserAutomationError(
                    f"WhatsApp blocked the send action: "
                    f"{(dialog_text or 'unknown dialog')[:180]}"
                ) from exc
            raise BrowserAutomationError(
                "WhatsApp did not confirm the send action; the message remained in "
                "the composer."
            ) from exc
        except WebDriverException as exc:
            logger.exception("Send action failed for row %s", contact.row_number)
            raise BrowserAutomationError(
                f"The send action failed: {summarize_browser_error(exc)}"
            ) from exc

    def _wait_between_messages(self, seconds: int) -> bool:
        remaining = seconds
        while remaining > 0:
            if self.stop_event.is_set():
                return False
            while not self.run_event.is_set():
                if self.stop_event.wait(0.25):
                    return False
            if remaining == seconds or remaining <= 5 or remaining % 10 == 0:
                self.on_log(
                    f"Next message in {remaining} "
                    f"second{'s' if remaining != 1 else ''}…"
                )
            if self.stop_event.wait(1):
                return False
            remaining -= 1
        return True

    def run(self, contacts: Iterable[Contact], message_template: str) -> None:
        ready_contacts = [contact for contact in contacts if contact.is_ready]
        total = len(ready_contacts)
        completed = 0
        if not total:
            self.on_log("There are no valid contacts to send.")
            return

        logger.info(
            "Sender run started: contacts=%s, delay=%ss, login_timeout=%ss",
            total,
            self.settings.delay_seconds,
            self.settings.login_timeout_seconds,
        )

        try:
            self._start_browser()
            self._wait_for_login()

            for index, contact in enumerate(ready_contacts):
                if self.stop_event.is_set():
                    break
                while not self.run_event.is_set():
                    if self.stop_event.wait(0.25):
                        break
                if self.stop_event.is_set():
                    break

                self.on_status(
                    SendResult(contact, ContactState.SENDING, "Opening chat")
                )
                personalized = render_message(
                    message_template,
                    name=contact.name,
                    phone=f"+{contact.phone}",
                )
                try:
                    self._open_and_send(contact, personalized)
                except BrowserAutomationError as exc:
                    logger.warning(
                        "Row %s failed: %s", contact.row_number, exc, exc_info=True
                    )
                    detail = str(exc)
                    self.on_status(SendResult(contact, ContactState.FAILED, detail))
                    self.on_log(f"Row {contact.row_number} failed: {detail}")
                else:
                    self.on_status(
                        SendResult(contact, ContactState.SENT, "Send action completed")
                    )
                    self.on_log(f"Sent to row {contact.row_number}.")

                completed += 1
                self.on_progress(completed, total)
                if index < total - 1 and not self._wait_between_messages(
                    self.settings.delay_seconds
                ):
                    break
        except (BrowserAutomationError, ValueError) as exc:
            logger.exception("Sender stopped before completion")
            self.on_log(f"Sender stopped: {exc}")
            self.on_fatal(str(exc))
        finally:
            if self.stop_event.is_set():
                for contact in ready_contacts[completed:]:
                    self.on_status(
                        SendResult(contact, ContactState.STOPPED, "Stopped by operator")
                    )
            if self.driver is not None:
                from selenium.common.exceptions import WebDriverException

                try:
                    self.driver.quit()
                except WebDriverException as exc:
                    logger.exception("Chrome did not close cleanly")
                    self.on_log(f"Chrome did not close cleanly: {exc}")
            logger.info("Sender session finished: completed=%s/%s", completed, total)
            self.on_log("Sender session finished.")
