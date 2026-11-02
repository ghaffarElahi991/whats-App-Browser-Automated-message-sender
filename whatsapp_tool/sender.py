from __future__ import annotations

import os
import threading
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

        invalid_phrases = (
            "phone number shared via url is invalid",
            "phone number isn't on whatsapp",
            "phone number is not on whatsapp",
        )

        def composer_or_error(driver: object):
            page_text = driver.find_element(By.TAG_NAME, "body").text.lower()  # type: ignore[attr-defined]
            if any(phrase in page_text for phrase in invalid_phrases):
                return "invalid"
            composers = driver.find_elements(  # type: ignore[attr-defined]
                By.CSS_SELECTOR,
                "footer div[contenteditable='true'][role='textbox']",
            )
            return composers[-1] if composers else False

        try:
            result = WebDriverWait(
                self.driver, self.settings.chat_timeout_seconds
            ).until(composer_or_error)
        except TimeoutException as exc:
            logger.exception("Chat timed out for row %s", contact.row_number)
            raise BrowserAutomationError(
                "Timed out while opening the chat. The number may be unavailable, "
                "or WhatsApp Web may have changed."
            ) from exc
        except WebDriverException as exc:
            logger.exception("Chat stopped responding for row %s", contact.row_number)
            raise BrowserAutomationError(
                f"WhatsApp Web stopped responding while opening the chat: {exc}"
            ) from exc

        if result == "invalid":
            raise BrowserAutomationError("The number is not available on WhatsApp")

        # The URL has already filled the editor. Enter performs the explicit send.
        try:
            result.click()
            result.send_keys(Keys.ENTER)
        except WebDriverException as exc:
            logger.exception("Send action failed for row %s", contact.row_number)
            raise BrowserAutomationError(f"The send action failed: {exc}") from exc

    def _wait_between_messages(self, seconds: int) -> bool:
        remaining = seconds
        while remaining > 0:
            if self.stop_event.is_set():
                return False
            while not self.run_event.is_set():
                if self.stop_event.wait(0.25):
                    return False
            self.on_log(
                f"Next message in {remaining} second{'s' if remaining != 1 else ''}…"
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
