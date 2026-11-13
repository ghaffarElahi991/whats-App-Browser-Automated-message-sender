from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

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

_NEW_CHAT_SELECTORS = (
    "button[aria-label='New chat']",
    "[role='button'][aria-label='New chat']",
    "[title='New chat']",
    "span[data-icon='new-chat-outline']",
    "span[data-icon='new-chat']",
)

_SEARCH_BOX_SELECTORS = (
    "input[role='textbox'][aria-label*='Search name' i]",
    "input[role='textbox'][placeholder*='Search name' i]",
    "div[contenteditable='true'][role='textbox'][aria-label*='Search name or number']",
    "div[contenteditable='true'][role='textbox'][aria-placeholder*='Search name or number']",
    "input[placeholder*='Search name or number']",
    "[role='textbox'][aria-label*='number' i][data-tab='3']",
    "[role='textbox'][placeholder*='number' i][data-tab='3']",
)

_GENERIC_SEARCH_BOX_SELECTOR = (
    "input[role='textbox'][data-tab='3'], "
    "div[contenteditable='true'][role='textbox'][data-tab='3']"
)

_CONTACT_RESULT_SELECTORS = (
    "div[role='listitem']",
    "div[data-testid='cell-frame-container']",
    "div[role='button'][tabindex='0']",
)

_NO_RESULTS_PHRASES = (
    "no contacts found",
    "no results found",
    "no chats, contacts or messages found",
    "invite to whatsapp",
)

_NON_CONTACT_RESULT_PHRASES = (
    "new group",
    "new contact",
    "new community",
    "invite to whatsapp",
    "no contacts found",
    "no results found",
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


def digits_only(text: str) -> str:
    return "".join(character for character in text if character.isdigit())


def contact_result_matches(candidate_text: str, phone: str) -> bool:
    candidate_digits = digits_only(candidate_text)
    phone_digits = digits_only(phone)
    return bool(phone_digits and phone_digits in candidate_digits)


def search_text_indicates_no_result(text: str) -> bool:
    normalized = normalize_dialog_text(text)
    return any(phrase in normalized for phrase in _NO_RESULTS_PHRASES)


def text_looks_like_contact_result(text: str) -> bool:
    normalized = normalize_dialog_text(text)
    return bool(
        normalized
        and not any(
            phrase == normalized or normalized.startswith(f"{phrase} ")
            for phrase in _NON_CONTACT_RESULT_PHRASES
        )
    )


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
        except WebDriverException:
            # WhatsApp frequently replaces a transient dialog while it is being
            # inspected. Escape still works against the newly rendered dialog.
            buttons = []

        for action in _SAFE_DIALOG_ACTIONS:
            for button in buttons:
                try:
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
                except WebDriverException:
                    continue
                if label != action:
                    continue
                try:
                    button.click()  # type: ignore[attr-defined]
                except WebDriverException:
                    try:
                        self.driver.execute_script("arguments[0].click();", button)
                    except WebDriverException:
                        continue
                return True

        try:
            active_element = self.driver.switch_to.active_element
            active_element.send_keys(keys.ESCAPE)  # type: ignore[attr-defined]
            return True
        except WebDriverException:
            try:
                body = self.driver.find_element(by.TAG_NAME, "body")
                body.send_keys(keys.ESCAPE)
                return True
            except WebDriverException:
                logger.exception("Could not dismiss a WhatsApp dialog")
                return False

    def _clear_stale_dialogs(self, by: object, keys: object) -> None:
        assert self.driver is not None
        for _attempt in range(5):
            dialogs = self._visible_dialogs(self.driver, by)
            if not dialogs:
                return
            text = self._dialog_text(dialogs[0])
            logger.info("Dismissing stale WhatsApp dialog: %s", text or "<no text>")
            if not text:
                # Empty accessibility dialogs are commonly transient loading
                # overlays. Let React finish replacing/removing them first.
                time.sleep(0.5)
                dialogs = self._visible_dialogs(self.driver, by)
                if not dialogs:
                    return
            self._dismiss_dialog(dialogs[0], by, keys)
            time.sleep(0.5)

        remaining = self._visible_dialogs(self.driver, by)
        if remaining:
            dialog_text = self._dialog_text(remaining[0])
            logger.warning(
                "WhatsApp dialog remained after cleanup: %s",
                dialog_text or "<no text>",
            )
            raise BrowserAutomationError(
                "A WhatsApp popup is blocking the page and could not be closed"
                + (f": {dialog_text[:180]}" if dialog_text else ".")
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

    @staticmethod
    def _is_visible(element: object) -> bool:
        try:
            return bool(element.is_displayed())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - WhatsApp frequently rerenders its DOM
            return False

    def _click_element(self, element: object, by: object) -> None:
        assert self.driver is not None
        from selenium.common.exceptions import WebDriverException

        target = element
        try:
            role = element.get_attribute("role")  # type: ignore[attr-defined]
            tag_name = str(element.tag_name).lower()  # type: ignore[attr-defined]
            if role != "button" and tag_name != "button":
                ancestors = element.find_elements(  # type: ignore[attr-defined]
                    by.XPATH,
                    "./ancestor::*[@role='button' or self::button][1]",  # type: ignore[attr-defined]
                )
                if ancestors:
                    target = ancestors[0]
            target.click()  # type: ignore[attr-defined]
        except WebDriverException:
            self.driver.execute_script("arguments[0].click();", target)

    def _find_visible_by_selectors(
        self, driver: object, by: object, selectors: tuple[str, ...]
    ) -> object | None:
        for selector in selectors:
            elements = driver.find_elements(  # type: ignore[attr-defined]
                by.CSS_SELECTOR,
                selector,  # type: ignore[attr-defined]
            )
            for element in elements:
                if self._is_visible(element):
                    return element
        return None

    def _find_new_chat_search_box(
        self, driver: object, by: object, existing_element_ids: set[str]
    ) -> object | None:
        search_box = self._find_visible_by_selectors(driver, by, _SEARCH_BOX_SELECTORS)
        if search_box is not None:
            return search_box

        # Language-independent fallback: after New chat is clicked, WhatsApp
        # adds a second data-tab=3 textbox. Prefer the newly created element and
        # never choose a footer message composer.
        candidates = driver.find_elements(  # type: ignore[attr-defined]
            by.CSS_SELECTOR,
            _GENERIC_SEARCH_BOX_SELECTOR,  # type: ignore[attr-defined]
        )
        fallback = None
        for candidate in candidates:
            if not self._is_visible(candidate):
                continue
            try:
                if candidate.find_elements(  # type: ignore[attr-defined]
                    by.XPATH,
                    "./ancestor::footer[1]",  # type: ignore[attr-defined]
                ):
                    continue
                element_id = str(candidate.id)  # type: ignore[attr-defined]
                label = normalize_dialog_text(
                    " ".join(
                        filter(
                            None,
                            (
                                candidate.get_attribute("aria-label"),  # type: ignore[attr-defined]
                                candidate.get_attribute("placeholder"),  # type: ignore[attr-defined]
                            ),
                        )
                    )
                )
            except Exception:  # noqa: BLE001 - stale textboxes are ignored
                continue
            if "start a new chat" in label:
                fallback = fallback or candidate
                continue
            if element_id not in existing_element_ids:
                return candidate
            fallback = fallback or candidate
        return fallback

    def _log_visible_textboxes(self, driver: object, by: object) -> None:
        descriptions = []
        elements = driver.find_elements(  # type: ignore[attr-defined]
            by.CSS_SELECTOR,
            "input[role='textbox'], [contenteditable='true'][role='textbox']",  # type: ignore[attr-defined]
        )
        for element in elements:
            if not self._is_visible(element):
                continue
            try:
                attributes = {
                    name: element.get_attribute(name)  # type: ignore[attr-defined]
                    for name in (
                        "aria-label",
                        "aria-placeholder",
                        "placeholder",
                        "data-tab",
                    )
                    if element.get_attribute(name)  # type: ignore[attr-defined]
                }
            except Exception:  # noqa: BLE001 - diagnostic only
                continue
            descriptions.append(attributes)
        logger.warning("Visible WhatsApp textboxes: %s", descriptions or "none")

    def _open_new_chat_search(self, by: object, keys: object, wait: object) -> object:
        assert self.driver is not None
        from selenium.common.exceptions import TimeoutException, WebDriverException

        self._clear_stale_dialogs(by, keys)

        existing_element_ids = set()
        for element in self.driver.find_elements(  # type: ignore[attr-defined]
            by.CSS_SELECTOR,
            _GENERIC_SEARCH_BOX_SELECTOR,  # type: ignore[attr-defined]
        ):
            if self._is_visible(element):
                try:
                    existing_element_ids.add(str(element.id))  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - stale textboxes are ignored
                    pass

        button = self._find_visible_by_selectors(self.driver, by, _NEW_CHAT_SELECTORS)
        if button is None:
            try:
                self.driver.switch_to.active_element.send_keys(keys.ESCAPE)
                time.sleep(0.3)
            except WebDriverException:
                pass

        stage = "button"
        try:
            button = wait(self.driver, 15).until(
                lambda driver: self._find_visible_by_selectors(
                    driver, by, _NEW_CHAT_SELECTORS
                )
            )
            self._click_element(button, by)
            stage = "search"
            search_box = wait(self.driver, 15).until(
                lambda driver: self._find_new_chat_search_box(
                    driver, by, existing_element_ids
                )
            )
            self._click_element(search_box, by)
            try:
                search_box.clear()  # type: ignore[attr-defined]
            except WebDriverException:
                modifier = keys.COMMAND if sys.platform == "darwin" else keys.CONTROL
                search_box.send_keys(modifier, "a")  # type: ignore[attr-defined]
                search_box.send_keys(keys.BACKSPACE)  # type: ignore[attr-defined]
            return search_box
        except TimeoutException as exc:
            if stage == "search":
                self._log_visible_textboxes(self.driver, by)
                raise BrowserAutomationError(
                    "New chat opened, but its number search field was not found."
                ) from exc
            raise BrowserAutomationError(
                "WhatsApp's New chat (+) button was not found."
            ) from exc
        except WebDriverException as exc:
            raise BrowserAutomationError(
                f"Could not use the New chat search: {summarize_browser_error(exc)}"
            ) from exc

    def _matching_contact_result(
        self, driver: object, by: object, phone: str
    ) -> object | None:
        # Exact number searches can return a saved contact whose row exposes
        # only the saved name. A sole visible listitem is safe as a fallback;
        # multiple name-only rows remain ambiguous and are never guessed.
        list_items: list[tuple[str, object]] = []
        for element in driver.find_elements(  # type: ignore[attr-defined]
            by.CSS_SELECTOR,
            _CONTACT_RESULT_SELECTORS[0],  # type: ignore[attr-defined]
        ):
            if not self._is_visible(element):
                continue
            try:
                text = str(element.text)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - stale search result
                continue
            if contact_result_matches(text, phone):
                return element
            if text_looks_like_contact_result(text):
                list_items.append((text, element))

        if len(list_items) == 1:
            logger.info(
                "Using the sole visible contact result; WhatsApp displayed its "
                "saved name instead of the searched number"
            )
            return list_items[0][1]

        for selector in _CONTACT_RESULT_SELECTORS[1:]:
            matches: list[tuple[int, object]] = []
            elements = driver.find_elements(  # type: ignore[attr-defined]
                by.CSS_SELECTOR,
                selector,  # type: ignore[attr-defined]
            )
            for element in elements:
                if not self._is_visible(element):
                    continue
                try:
                    text = str(element.text)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - stale search result
                    continue
                if contact_result_matches(text, phone):
                    matches.append((len(text), element))
            if matches:
                return min(matches, key=lambda item: item[0])[1]
        return None

    def _select_contact_from_search(
        self,
        contact: Contact,
        by: object,
        keys: object,
        wait: object,
    ) -> None:
        assert self.driver is not None
        from selenium.common.exceptions import TimeoutException, WebDriverException

        self.on_log(f"Searching WhatsApp for row {contact.row_number}…")
        search_box = self._open_new_chat_search(by, keys, wait)
        try:
            search_box.send_keys(f"+{contact.phone}")  # type: ignore[attr-defined]
            self.on_log("Waiting 2 seconds for contact results…")
            time.sleep(2)

            def result_dialog_or_empty(driver: object):
                dialogs = self._visible_dialogs(driver, by)
                if dialogs:
                    return "dialog", dialogs[0]
                result = self._matching_contact_result(driver, by, contact.phone)
                if result is not None:
                    return "result", result
                bodies = driver.find_elements(by.TAG_NAME, "body")  # type: ignore[attr-defined]
                body_text = bodies[0].text if bodies else ""  # type: ignore[attr-defined]
                return (
                    ("unavailable", None)
                    if search_text_indicates_no_result(body_text)
                    else False
                )

            state, element = wait(
                self.driver, self.settings.chat_timeout_seconds
            ).until(result_dialog_or_empty)
            if state == "unavailable":
                raise BrowserAutomationError(
                    "The number was not found in WhatsApp's New chat search."
                )
            if state == "dialog":
                dialog_text = self._dialog_text(element)
                self._dismiss_dialog(element, by, keys)
                raise BrowserAutomationError(
                    "WhatsApp blocked the contact search: "
                    f"{(dialog_text or 'unknown dialog')[:180]}"
                )
            self._click_element(element, by)
        except BrowserAutomationError:
            try:
                search_box.send_keys(keys.ESCAPE)  # type: ignore[attr-defined]
            except WebDriverException:
                pass
            raise
        except TimeoutException as exc:
            visible_list_items = [
                element
                for element in self.driver.find_elements(  # type: ignore[attr-defined]
                    by.CSS_SELECTOR,
                    _CONTACT_RESULT_SELECTORS[0],  # type: ignore[attr-defined]
                )
                if self._is_visible(element)
            ]
            logger.warning(
                "Contact result timeout: visible listitem count=%s",
                len(visible_list_items),
            )
            try:
                search_box.send_keys(keys.ESCAPE)  # type: ignore[attr-defined]
            except WebDriverException:
                pass
            raise BrowserAutomationError(
                "No matching WhatsApp contact appeared after searching the number."
            ) from exc
        except WebDriverException as exc:
            raise BrowserAutomationError(
                f"The contact search failed: {summarize_browser_error(exc)}"
            ) from exc

    def _wait_for_message_composer(
        self, contact: Contact, by: object, keys: object, wait: object
    ) -> object:
        assert self.driver is not None
        from selenium.common.exceptions import TimeoutException, WebDriverException

        def composer_dialog_or_error(driver: object):
            dialogs = self._visible_dialogs(driver, by)
            if dialogs:
                return "dialog", dialogs[0]
            composers = driver.find_elements(  # type: ignore[attr-defined]
                by.CSS_SELECTOR,
                "footer div[contenteditable='true'][role='textbox']",  # type: ignore[attr-defined]
            )
            visible = [element for element in composers if self._is_visible(element)]
            return ("composer", visible[-1]) if visible else False

        deadline = time.monotonic() + self.settings.chat_timeout_seconds
        try:
            for _attempt in range(4):
                remaining = max(0.5, deadline - time.monotonic())
                state, element = wait(self.driver, remaining).until(
                    composer_dialog_or_error
                )
                if state == "composer":
                    return element

                dialog_text = self._dialog_text(element)
                logger.info(
                    "WhatsApp dialog for row %s: %s",
                    contact.row_number,
                    dialog_text or "<no text>",
                )
                unavailable = dialog_indicates_unavailable_contact(dialog_text)
                dismissed = self._dismiss_dialog(element, by, keys)
                if unavailable:
                    raise BrowserAutomationError(
                        "The number is not available on WhatsApp."
                    )
                if not dismissed:
                    raise BrowserAutomationError(
                        "WhatsApp displayed a blocking dialog: "
                        f"{(dialog_text or 'unknown dialog')[:180]}"
                    )
                time.sleep(0.5)
        except TimeoutException as exc:
            raise BrowserAutomationError(
                "The contact was selected, but the message composer did not open."
            ) from exc
        except WebDriverException as exc:
            raise BrowserAutomationError(
                "WhatsApp stopped responding while opening the selected contact: "
                f"{summarize_browser_error(exc)}"
            ) from exc

        raise BrowserAutomationError("The message composer did not become available.")

    def _type_and_send_message(
        self,
        contact: Contact,
        message: str,
        composer: object,
        by: object,
        keys: object,
        wait: object,
    ) -> None:
        assert self.driver is not None
        from selenium.common.exceptions import TimeoutException, WebDriverException
        from selenium.webdriver.common.action_chains import ActionChains

        try:
            self._clear_stale_dialogs(by, keys)
            composer.click()  # type: ignore[attr-defined]
            lines = message.split("\n")
            for index, line in enumerate(lines):
                if line:
                    composer.send_keys(line)  # type: ignore[attr-defined]
                if index < len(lines) - 1:
                    ActionChains(self.driver).key_down(keys.SHIFT).send_keys(
                        keys.ENTER
                    ).key_up(keys.SHIFT).perform()

            composer_text = (composer.get_attribute("textContent") or "").strip()  # type: ignore[attr-defined]
            if not composer_text:
                raise BrowserAutomationError(
                    "WhatsApp opened the chat but did not accept the message text."
                )
            composer.send_keys(keys.ENTER)  # type: ignore[attr-defined]

            def message_was_sent(driver: object) -> bool:
                if self._visible_dialogs(driver, by):
                    return False
                try:
                    return not (composer.get_attribute("textContent") or "").strip()  # type: ignore[attr-defined]
                except WebDriverException:
                    return True

            wait(self.driver, 10).until(message_was_sent)
        except BrowserAutomationError:
            raise
        except TimeoutException as exc:
            dialogs = self._visible_dialogs(self.driver, by)
            if dialogs:
                dialog_text = self._dialog_text(dialogs[0])
                self._dismiss_dialog(dialogs[0], by, keys)
                raise BrowserAutomationError(
                    "WhatsApp blocked the send action: "
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

    def _open_and_send(self, contact: Contact, message: str) -> None:
        assert self.driver is not None
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait

        self._select_contact_from_search(contact, By, Keys, WebDriverWait)
        composer = self._wait_for_message_composer(contact, By, Keys, WebDriverWait)
        self._type_and_send_message(contact, message, composer, By, Keys, WebDriverWait)

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
                    SendResult(contact, ContactState.SENDING, "Searching number")
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
