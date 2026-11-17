"""Exercise the real result XPath against the screenshot's role-free layout."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_sender
from lxml import html
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from whatsapp_tool.models import Contact
from whatsapp_tool.sender import BrowserAutomationError

FIXTURE = Path(__file__).parent / "fixtures" / "username_number_match.html"


class DOMElement:
    """Small WebElement adapter: XPath is evaluated by lxml, never mocked."""

    def __init__(self, document, node):
        self.document = document
        self.node = node

    def __eq__(self, other):
        return isinstance(other, DOMElement) and self.node is other.node

    @property
    def text(self):
        return self.node.text_content()

    def get_attribute(self, name):
        return self.text if name == "textContent" else self.node.get(name)

    def find_elements(self, by, selector):
        if by == By.XPATH:
            nodes = self.node.xpath(selector)
        elif selector == "#pane-side, #main, footer":
            nodes = self.node.xpath(
                ".//*[@id='pane-side' or @id='main' or self::footer]"
            )
        else:
            raise AssertionError(f"Unexpected selector: {selector}")
        return [DOMElement(self.document, node) for node in nodes]

    def is_displayed(self):
        return not self.node.xpath("ancestor-or-self::*[@hidden]")

    def send_keys(self, *values):
        if values != (Keys.ESCAPE,):
            self.node.set("value", "".join(values))

    def click(self):
        if self.document.stale_once:
            self.document.stale_once = False
            raise StaleElementReferenceException("Result re-rendered")
        self.document.clicks.append(self.node.get("id"))
        if self.document.close_on_click:
            self.document.root.get_element_by_id("new-chat").set("hidden", "hidden")


class DOMDocument:
    def __init__(self):
        self.root = html.parse(str(FIXTURE)).getroot()
        self.clicks = []
        self.stale_once = False
        self.close_on_click = True

    def get(self, element_id):
        return DOMElement(self, self.root.get_element_by_id(element_id))

    def find_elements(self, by, selector):
        # Background list entries remain in the page, just as on WhatsApp Web.
        selectors = {
            "div[role='listitem']": ".//div[@role='listitem']",
            "div[data-testid='cell-frame-container']": ".//div[@data-testid='cell-frame-container']",
            "div[role='button'][tabindex='0']": ".//div[@role='button'][@tabindex='0']",
        }
        if by != By.CSS_SELECTOR or selector not in selectors:
            raise AssertionError(f"Unexpected selector: {selector}")
        return [DOMElement(self, node) for node in self.root.xpath(selectors[selector])]


class PollingWait:
    def __init__(self, driver, _timeout):
        self.driver = driver

    def until(self, condition):
        for _ in range(3):
            value = condition(self.driver)
            if value:
                return value
        raise TimeoutException("Fixture did not change state")


class UsernameNumberMatchTests(unittest.TestCase):
    def setUp(self):
        self.sender = test_sender.SenderTests.make_sender()
        self.document = DOMDocument()
        self.sender.driver = self.document
        self.search = self.document.get("number-search")
        self.contact = Contact(2, "+60123456789", phone="60123456789")
        self.send = Mock()

    def match(self):
        return self.sender._matching_contact_result(
            self.document, By, self.contact.phone, self.search
        )

    def test_role_free_username_row_targets_name_not_add_contact(self):
        result = self.match()
        self.assertEqual(result, self.document.get("username-target"))
        result.click()
        self.assertEqual(self.document.clicks, ["username-target"])

    def test_search_must_contain_requested_number(self):
        self.search.node.set("value", "+60999999999")
        self.assertIsNone(self.match())

    def test_two_number_match_rows_are_ambiguous(self):
        panel = self.document.root.get_element_by_id("new-chat")
        panel.append(
            html.fromstring(
                "<div><span>@AnotherAccount</span><span>Matches this number</span></div>"
            )
        )
        self.assertIsNone(self.match())

    def test_background_username_cannot_match_active_search(self):
        row = self.document.root.get_element_by_id("contact-result")
        self.document.root.get_element_by_id("main").append(row)
        self.assertIsNone(self.match())

    def test_hidden_number_match_row_is_not_selected(self):
        self.document.root.get_element_by_id("contact-result").set("hidden", "hidden")
        self.assertIsNone(self.match())

    def run_open_chat(self):
        def current_search(*_args):
            return self.search if self.search.is_displayed() else None

        with (
            patch.object(
                self.sender, "_open_new_chat_search", return_value=self.search
            ),
            patch.object(
                self.sender, "_find_visible_by_selectors", side_effect=current_search
            ),
            patch.object(self.sender, "_visible_dialogs", return_value=[]),
            patch("whatsapp_tool.sender.time.sleep"),
            patch("selenium.webdriver.support.ui.WebDriverWait", PollingWait),
            patch.object(self.sender, "_wait_for_message_composer") as composer_wait,
            patch.object(self.sender, "_type_and_send_message", self.send) as send,
        ):
            self.sender._open_and_send(self.contact, "Test message")
        return composer_wait, send

    def test_click_opens_chat_before_message_stage(self):
        composer_wait, send = self.run_open_chat()
        self.assertEqual(self.document.clicks, ["username-target"])
        self.assertFalse(self.search.is_displayed())
        composer_wait.assert_called_once()
        send.assert_called_once()

    def test_stale_result_is_relocated_before_click(self):
        self.document.stale_once = True
        _, send = self.run_open_chat()
        self.assertEqual(self.document.clicks, ["username-target"])
        send.assert_called_once()

    def test_open_search_never_falls_through_to_old_chat_composer(self):
        self.document.close_on_click = False
        with self.assertRaisesRegex(BrowserAutomationError, "New chat is still open"):
            self.run_open_chat()
        self.send.assert_not_called()
