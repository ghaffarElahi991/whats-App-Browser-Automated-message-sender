from __future__ import annotations

import csv
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .contacts import ContactFileError, load_contacts
from .diagnostics import configure_logging, get_logger
from .models import Contact, ContactState, SendResult
from .sender import SenderSettings, WhatsAppWebSender
from .templates import TemplateFileError, load_docx_template

APP_TITLE = "Mayfair WhatsApp Web Sender"


def calculate_window_geometry(
    screen_width: int, screen_height: int
) -> tuple[int, int, int, int]:
    """Return a centered size that leaves room for macOS menus and the Dock."""

    available_width = max(480, screen_width - 64)
    available_height = max(520, screen_height - 120)
    width = min(1180, available_width)
    height = min(820, available_height)
    x_position = max(0, (screen_width - width) // 2)
    y_position = max(24, (screen_height - height) // 3)
    return width, height, x_position, y_position


class ScrollablePage(ttk.Frame):
    """A vertically scrollable page whose body follows the window width."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self, orient=tk.VERTICAL, command=self.canvas.yview
        )
        self.body = ttk.Frame(self.canvas, padding=16)
        self.body_window = self.canvas.create_window(
            (0, 0), window=self.body, anchor=tk.NW
        )

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.body.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_body_width)

    def _sync_scroll_region(self, _event: tk.Event) -> None:
        bounds = self.canvas.bbox("all")
        if bounds:
            self.canvas.configure(scrollregion=bounds)

    def _sync_body_width(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.body_window, width=event.width)

    def scroll_units(self, units: int) -> None:
        self.canvas.yview_scroll(units, "units")


def _find_workspace_file(filename: str) -> str:
    current = Path(__file__).resolve()
    for directory in current.parents:
        candidate = directory / filename
        if candidate.exists():
            return str(candidate)
    return ""


class SenderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.log_file = configure_logging()
        self.logger = get_logger("ui")
        self.root.title(APP_TITLE)
        width, height, x_position, y_position = calculate_window_geometry(
            self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        )
        self.root.geometry(f"{width}x{height}+{x_position}+{y_position}")
        self.root.minsize(min(760, width), min(560, height))
        self.window_width = width

        self.contacts: list[Contact] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.run_event = threading.Event()
        self.run_event.set()
        self.worker: threading.Thread | None = None
        self.is_sending = False

        self.contacts_path = tk.StringVar(
            value=_find_workspace_file("Chubb_mobile_numbers.xlsx")
        )
        self.template_path = tk.StringVar(
            value=_find_workspace_file("message template.docx")
        )
        self.country_code = tk.StringVar(value="60")
        self.delay_seconds = tk.StringVar(value="30")
        self.login_timeout = tk.StringVar(value="180")
        self.profile_path = tk.StringVar(
            value=str(Path.home() / ".mayfair-whatsapp-web" / "chrome-profile")
        )
        self.consent_confirmed = tk.BooleanVar(value=False)
        self.summary_text = tk.StringVar(value="No contacts loaded")
        self.progress_text = tk.StringVar(value="0 / 0")
        self.status_text = tk.StringVar(value="Ready")

        self._configure_style()
        self._build_ui()
        self.root.bind("<MouseWheel>", self._scroll_page, add="+")
        self.root.bind("<Button-4>", self._scroll_page, add="+")
        self.root.bind("<Button-5>", self._scroll_page, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_events)
        self.root.after(250, self._load_defaults)
        self._log(f"Persistent diagnostic log: {self.log_file}")

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("TkDefaultFont", 18, "bold"))
        style.configure("Subtitle.TLabel", foreground="#4b5563")
        style.configure("Accent.TButton", font=("TkDefaultFont", 10, "bold"))
        style.configure("Treeview", rowheight=25)

    def _build_ui(self) -> None:
        self.scrollable_page = ScrollablePage(self.root)
        self.scrollable_page.pack(fill=tk.BOTH, expand=True)
        outer = self.scrollable_page.body

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header,
            text="Review contacts, confirm consent, then send sequentially through your Chrome WhatsApp Web session.",
            style="Subtitle.TLabel",
            wraplength=max(520, self.window_width - 80),
        ).pack(anchor=tk.W, pady=(3, 0))

        sources = ttk.LabelFrame(outer, text="1. Source files", padding=10)
        sources.pack(fill=tk.X, pady=(0, 10))
        sources.columnconfigure(1, weight=1)

        ttk.Label(sources, text="Contacts workbook").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8)
        )
        ttk.Entry(sources, textvariable=self.contacts_path).grid(
            row=0, column=1, sticky=tk.EW
        )
        ttk.Button(sources, text="Browse…", command=self._browse_contacts).grid(
            row=0, column=2, padx=6
        )
        ttk.Button(sources, text="Load", command=self._load_contacts).grid(
            row=0, column=3
        )

        ttk.Label(sources, text="Word template").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0)
        )
        ttk.Entry(sources, textvariable=self.template_path).grid(
            row=1, column=1, sticky=tk.EW, pady=(8, 0)
        )
        ttk.Button(sources, text="Browse…", command=self._browse_template).grid(
            row=1, column=2, padx=6, pady=(8, 0)
        )
        ttk.Button(sources, text="Load", command=self._load_template).grid(
            row=1, column=3, pady=(8, 0)
        )

        panes = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)

        contacts_frame = ttk.LabelFrame(panes, text="2. Validated contacts", padding=8)
        message_frame = ttk.LabelFrame(panes, text="3. Message", padding=8)
        panes.add(contacts_frame, weight=3)
        panes.add(message_frame, weight=2)

        contacts_frame.rowconfigure(1, weight=1)
        contacts_frame.columnconfigure(0, weight=1)
        ttk.Label(contacts_frame, textvariable=self.summary_text).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 6)
        )

        tree_wrap = ttk.Frame(contacts_frame)
        tree_wrap.grid(row=1, column=0, sticky=tk.NSEW)
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        self.contact_tree = ttk.Treeview(
            tree_wrap,
            columns=("row", "name", "phone", "status", "detail"),
            show="headings",
        )
        columns = {
            "row": ("Row", 55, tk.CENTER),
            "name": ("Name", 130, tk.W),
            "phone": ("Phone", 135, tk.W),
            "status": ("Status", 85, tk.CENTER),
            "detail": ("Detail", 210, tk.W),
        }
        for column, (label, width, anchor) in columns.items():
            self.contact_tree.heading(column, text=label)
            self.contact_tree.column(column, width=width, minwidth=50, anchor=anchor)
        self.contact_tree.grid(row=0, column=0, sticky=tk.NSEW)
        vertical_scrollbar = ttk.Scrollbar(
            tree_wrap, orient=tk.VERTICAL, command=self.contact_tree.yview
        )
        vertical_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        horizontal_scrollbar = ttk.Scrollbar(
            tree_wrap, orient=tk.HORIZONTAL, command=self.contact_tree.xview
        )
        horizontal_scrollbar.grid(row=1, column=0, sticky=tk.EW)
        self.contact_tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        self.contact_tree.tag_configure(
            ContactState.INVALID.value, foreground="#b91c1c"
        )
        self.contact_tree.tag_configure(
            ContactState.DUPLICATE.value, foreground="#92400e"
        )
        self.contact_tree.tag_configure(ContactState.SENT.value, foreground="#047857")
        self.contact_tree.tag_configure(ContactState.FAILED.value, foreground="#b91c1c")

        message_frame.rowconfigure(1, weight=1)
        message_frame.columnconfigure(0, weight=1)
        ttk.Label(
            message_frame,
            text="You can edit this text. Optional placeholders: {name} and {phone}.",
            style="Subtitle.TLabel",
            wraplength=390,
        ).grid(row=0, column=0, sticky=tk.W, pady=(0, 6))
        self.message_text = tk.Text(
            message_frame,
            height=12,
            wrap=tk.WORD,
            undo=True,
            font=("TkDefaultFont", 10),
        )
        self.message_text.grid(row=1, column=0, sticky=tk.NSEW)
        message_scroll = ttk.Scrollbar(
            message_frame, orient=tk.VERTICAL, command=self.message_text.yview
        )
        message_scroll.grid(row=1, column=1, sticky=tk.NS)
        self.message_text.configure(yscrollcommand=message_scroll.set)

        settings = ttk.LabelFrame(outer, text="4. Sending controls", padding=10)
        settings.pack(fill=tk.X, pady=(10, 0))
        for index in range(8):
            settings.columnconfigure(index, weight=1 if index in (1, 7) else 0)

        ttk.Label(settings, text="Default country +").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(settings, textvariable=self.country_code, width=7).grid(
            row=0, column=1, sticky=tk.W, padx=(5, 14)
        )
        ttk.Label(settings, text="Delay (seconds)").grid(row=0, column=2, sticky=tk.W)
        ttk.Spinbox(
            settings, from_=15, to=300, textvariable=self.delay_seconds, width=7
        ).grid(row=0, column=3, sticky=tk.W, padx=(5, 14))
        ttk.Label(settings, text="QR login timeout").grid(row=0, column=4, sticky=tk.W)
        ttk.Spinbox(
            settings, from_=30, to=600, textvariable=self.login_timeout, width=7
        ).grid(row=0, column=5, sticky=tk.W, padx=(5, 14))
        ttk.Label(settings, text="seconds").grid(row=0, column=6, sticky=tk.W)

        ttk.Label(settings, text="Chrome session folder").grid(
            row=1, column=0, sticky=tk.W, pady=(8, 0)
        )
        ttk.Entry(settings, textvariable=self.profile_path).grid(
            row=1, column=1, columnspan=6, sticky=tk.EW, padx=(5, 6), pady=(8, 0)
        )
        ttk.Button(settings, text="Choose…", command=self._browse_profile).grid(
            row=1, column=7, sticky=tk.E, pady=(8, 0)
        )

        consent = ttk.Checkbutton(
            settings,
            text="I confirm these recipients opted in to receive this WhatsApp message.",
            variable=self.consent_confirmed,
        )
        consent.grid(row=2, column=0, columnspan=8, sticky=tk.W, pady=(10, 0))

        action_row = ttk.Frame(outer)
        action_row.pack(fill=tk.X, pady=(10, 0))
        self.start_button = ttk.Button(
            action_row,
            text="Start sending",
            style="Accent.TButton",
            command=self._start_sending,
        )
        self.start_button.pack(side=tk.LEFT)
        self.pause_button = ttk.Button(
            action_row, text="Pause", command=self._toggle_pause, state=tk.DISABLED
        )
        self.pause_button.pack(side=tk.LEFT, padx=(6, 0))
        self.stop_button = ttk.Button(
            action_row, text="Stop", command=self._stop_sending, state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(action_row, text="Export report…", command=self._export_report).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Label(
            action_row,
            textvariable=self.status_text,
            width=32,
            anchor=tk.E,
        ).pack(side=tk.RIGHT)

        progress_row = ttk.Frame(outer)
        progress_row.pack(fill=tk.X, pady=(8, 0))
        self.progress = ttk.Progressbar(progress_row, mode="determinate", maximum=1)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(
            progress_row, textvariable=self.progress_text, width=12, anchor=tk.E
        ).pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(outer, text="Activity and diagnostics", padding=6)
        log_frame.pack(fill=tk.X, pady=(10, 0))
        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(
            log_toolbar,
            text=f"Log file: {self.log_file.name}",
            style="Subtitle.TLabel",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(log_toolbar, text="Copy activity", command=self._copy_activity).pack(
            side=tk.RIGHT
        )
        ttk.Button(log_toolbar, text="Copy log path", command=self._copy_log_path).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        log_body = ttk.Frame(log_frame)
        log_body.pack(fill=tk.X)
        self.log_text = tk.Text(log_body, height=7, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        log_scroll = ttk.Scrollbar(
            log_body, orient=tk.VERTICAL, command=self.log_text.yview
        )
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _scroll_page(self, event: tk.Event) -> str | None:
        internal_scrollers = (
            getattr(self, "contact_tree", None),
            getattr(self, "message_text", None),
            getattr(self, "log_text", None),
        )
        widget: tk.Misc | None = event.widget
        while widget is not None:
            if widget in internal_scrollers:
                return None
            widget = getattr(widget, "master", None)

        event_number = getattr(event, "num", None)
        if event_number == 4:
            units = -3
        elif event_number == 5:
            units = 3
        else:
            delta = int(getattr(event, "delta", 0))
            if not delta:
                return None
            if sys.platform == "darwin":
                units = -delta
            else:
                units = (
                    -(delta // 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
                )

        self.scrollable_page.scroll_units(units)
        return "break"

    def _load_defaults(self) -> None:
        if self.contacts_path.get():
            self._load_contacts(show_errors=False)
        if self.template_path.get():
            self._load_template(show_errors=False)

    def _browse_contacts(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose contacts workbook",
            filetypes=(("Excel workbook", "*.xlsx *.xlsm"), ("All files", "*.*")),
        )
        if path:
            self.contacts_path.set(path)
            self._load_contacts()

    def _browse_template(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose Word message template",
            filetypes=(("Word document", "*.docx"), ("All files", "*.*")),
        )
        if path:
            self.template_path.set(path)
            self._load_template()

    def _browse_profile(self) -> None:
        path = filedialog.askdirectory(title="Choose Chrome session folder")
        if path:
            self.profile_path.set(path)

    def _load_contacts(self, show_errors: bool = True) -> None:
        if self.is_sending:
            messagebox.showwarning(
                APP_TITLE, "Stop the current sender before changing contacts."
            )
            return
        try:
            contacts = load_contacts(self.contacts_path.get(), self.country_code.get())
        except ContactFileError as exc:
            if show_errors:
                messagebox.showerror(APP_TITLE, str(exc))
            self._log(str(exc))
            return

        self.contacts = contacts
        self._refresh_contact_tree()
        ready = sum(contact.is_ready for contact in contacts)
        invalid = sum(contact.state == ContactState.INVALID for contact in contacts)
        duplicates = sum(
            contact.state == ContactState.DUPLICATE for contact in contacts
        )
        self.summary_text.set(
            f"{ready} ready · {invalid} invalid · {duplicates} duplicate · {len(contacts)} total"
        )
        self.progress.configure(maximum=max(ready, 1), value=0)
        self.progress_text.set(f"0 / {ready}")
        self._log(f"Loaded {len(contacts)} rows; {ready} are ready to send.")

    def _load_template(self, show_errors: bool = True) -> None:
        if self.is_sending:
            messagebox.showwarning(
                APP_TITLE, "Stop the current sender before changing the message."
            )
            return
        try:
            message = load_docx_template(self.template_path.get())
        except TemplateFileError as exc:
            if show_errors:
                messagebox.showerror(APP_TITLE, str(exc))
            self._log(str(exc))
            return
        self.message_text.delete("1.0", tk.END)
        self.message_text.insert("1.0", message)
        self._log(f"Loaded message template ({len(message)} characters).")

    def _refresh_contact_tree(self) -> None:
        self.contact_tree.delete(*self.contact_tree.get_children())
        for contact in self.contacts:
            self._insert_or_update_contact(contact)

    def _insert_or_update_contact(self, contact: Contact) -> None:
        iid = str(contact.row_number)
        shown_phone = f"+{contact.phone}" if contact.phone else contact.original_phone
        values = (
            contact.row_number,
            contact.name,
            shown_phone,
            contact.state.value,
            contact.detail,
        )
        if self.contact_tree.exists(iid):
            self.contact_tree.item(iid, values=values, tags=(contact.state.value,))
        else:
            self.contact_tree.insert(
                "", tk.END, iid=iid, values=values, tags=(contact.state.value,)
            )

    def _start_sending(self) -> None:
        if self.is_sending:
            return
        ready = [contact for contact in self.contacts if contact.is_ready]
        if not ready:
            messagebox.showerror(
                APP_TITLE, "Load a workbook with at least one valid contact."
            )
            return
        message = self.message_text.get("1.0", tk.END).strip()
        if not message:
            messagebox.showerror(APP_TITLE, "Load or enter a message first.")
            return
        if len(message) > 4096:
            messagebox.showerror(
                APP_TITLE,
                "The message is longer than WhatsApp's 4,096-character limit.",
            )
            return
        if not self.consent_confirmed.get():
            messagebox.showerror(APP_TITLE, "Confirm recipient opt-in before sending.")
            return

        try:
            delay = int(self.delay_seconds.get())
            timeout = int(self.login_timeout.get())
            settings = SenderSettings(
                profile_directory=Path(self.profile_path.get()),
                delay_seconds=delay,
                login_timeout_seconds=timeout,
            )
            settings.validate()
        except (TypeError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        if not self.profile_path.get().strip():
            messagebox.showerror(APP_TITLE, "Choose a Chrome session folder.")
            return

        estimated_minutes = ((len(ready) - 1) * delay) / 60
        confirmed = messagebox.askyesno(
            "Confirm send",
            f"Send this message to {len(ready)} opted-in recipient(s)?\n\n"
            f"Delay: {delay} seconds between sends\n"
            f"Minimum estimated time: {estimated_minutes:.1f} minutes\n\n"
            "Chrome will open. You may need to scan the WhatsApp Web QR code.",
        )
        if not confirmed:
            return

        self.stop_event.clear()
        self.run_event.set()
        self.is_sending = True
        self._set_running_controls(True)
        self.progress.configure(maximum=max(len(ready), 1), value=0)
        self.progress_text.set(f"0 / {len(ready)}")
        self.status_text.set("Starting Chrome…")
        self._log(f"Starting sender for {len(ready)} recipient(s).")

        sender = WhatsAppWebSender(
            settings,
            stop_event=self.stop_event,
            run_event=self.run_event,
            on_status=lambda result: self.events.put(("status", result)),
            on_log=lambda text: self.events.put(("log", text)),
            on_progress=lambda done, total: self.events.put(
                ("progress", (done, total))
            ),
            on_fatal=lambda text: self.events.put(("fatal", text)),
        )

        def work() -> None:
            try:
                sender.run(self.contacts, message)
            except Exception as exc:
                self.logger.exception("Unexpected sender worker failure")
                self.events.put(("fatal", f"Unexpected sender error: {exc}"))
            finally:
                self.events.put(("finished", None))

        self.worker = threading.Thread(target=work, name="whatsapp-sender", daemon=True)
        self.worker.start()

    def _toggle_pause(self) -> None:
        if self.run_event.is_set():
            self.run_event.clear()
            self.pause_button.configure(text="Resume")
            self.status_text.set("Paused")
            self._log(
                "Paused by operator. The current browser action may finish first."
            )
        else:
            self.run_event.set()
            self.pause_button.configure(text="Pause")
            self.status_text.set("Sending")
            self._log("Resumed by operator.")

    def _stop_sending(self) -> None:
        if not self.is_sending:
            return
        self.stop_event.set()
        self.run_event.set()
        self.status_text.set("Stopping…")
        self.stop_button.configure(state=tk.DISABLED)
        self._log("Stop requested. Closing the browser safely…")

    def _set_running_controls(self, running: bool) -> None:
        self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.pause_button.configure(
            state=tk.NORMAL if running else tk.DISABLED, text="Pause"
        )
        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self._log(str(payload))
                    if self.is_sending and self.run_event.is_set():
                        self.status_text.set(str(payload))
                elif event == "fatal":
                    detail = str(payload)
                    self._log(f"FATAL: {detail}")
                    messagebox.showerror(
                        "Chrome / sender error",
                        f"{detail}\n\nFull diagnostic log:\n{self.log_file}",
                    )
                elif event == "status" and isinstance(payload, SendResult):
                    payload.contact.state = payload.state
                    payload.contact.detail = payload.detail
                    self._insert_or_update_contact(payload.contact)
                    self.contact_tree.see(str(payload.contact.row_number))
                elif event == "progress":
                    done, total = payload  # type: ignore[misc]
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self.progress_text.set(f"{done} / {total}")
                elif event == "finished":
                    self.is_sending = False
                    self._set_running_controls(False)
                    self.status_text.set("Finished")
                    self.worker = None
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _log(self, text: str) -> None:
        self.logger.info(text)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {text}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _copy_activity(self) -> None:
        activity = self.log_text.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(activity)
        self.status_text.set("Activity copied")

    def _copy_log_path(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(str(self.log_file))
        self.status_text.set("Log path copied")

    def _export_report(self) -> None:
        if not self.contacts:
            messagebox.showerror(APP_TITLE, "There are no contacts to export.")
            return
        default_name = f"whatsapp-results-{datetime.now():%Y%m%d-%H%M%S}.csv"
        destination = filedialog.asksaveasfilename(
            title="Save results report",
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=(("CSV report", "*.csv"),),
        )
        if not destination:
            return
        try:
            with Path(destination).open(
                "w", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "source_row",
                        "name",
                        "original_phone",
                        "normalized_phone",
                        "status",
                        "detail",
                    ]
                )
                for contact in self.contacts:
                    writer.writerow(
                        [
                            contact.row_number,
                            contact.name,
                            contact.original_phone,
                            f"+{contact.phone}" if contact.phone else "",
                            contact.state.value,
                            contact.detail,
                        ]
                    )
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not save report: {exc}")
            return
        self._log(f"Saved report to {destination}")
        messagebox.showinfo(APP_TITLE, "Report saved.")

    def _on_close(self) -> None:
        if self.is_sending:
            if not messagebox.askyesno(
                APP_TITLE, "Stop sending and close the application?"
            ):
                return
            self.stop_event.set()
            self.run_event.set()
            self.root.after(1200, self.root.destroy)
            return
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    SenderApp(root)
    root.mainloop()
