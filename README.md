# Mayfair WhatsApp Web Sender

A Python desktop UI that reads phone numbers from Excel, loads an editable message from Word, validates and de-duplicates the contacts, and sends sequentially through an operator's logged-in WhatsApp Web session.

The supplied files are detected automatically when the app is kept anywhere below the workspace folder:

- `Chubb_mobile_numbers.xlsx` — 34 contact rows under the `phone` header
- `message template.docx` — the supplied Mayfair Car Parts Concierge message

## Important use conditions

Use this tool only for recipients who have explicitly opted in. The consent checkbox is required before a run. The tool does not bypass WhatsApp challenges, hide browser automation, or confirm that a message was delivered or read. A `Sent` result means that the send action completed in WhatsApp Web.

Browser automation can break when WhatsApp changes its Web interface and may be restricted by WhatsApp's terms. For large or business-critical campaigns, the official WhatsApp Business Platform/API implementation in `WhatsApp_Bot/V1/mayfair-whatsapp` is more reliable.

## Requirements

- Python 3.10 or newer
- Google Chrome
- Internet access on the first run to install Python packages and obtain a matching Chrome driver if needed
- WhatsApp account with access to WhatsApp Web

## Install on macOS or Linux

### macOS double-click installation

1. Install Google Chrome and Python 3.15 from [python.org](https://www.python.org/downloads/macos/).
2. Double-click **Install on macOS.command** in Finder.
3. When installation finishes, double-click **Start on macOS.command** to open the application.

If macOS blocks a downloaded script, Control-click it, select **Open**, and confirm **Open** once. The scripts remain local and can be inspected in any text editor.

### Terminal installation

From the project folder, run:

```bash
chmod +x install.sh
./install.sh
```

The installer checks Python, creates the platform-specific virtual environment, installs all Python dependencies, validates Tkinter, checks for Chrome, and runs the non-sending tests. It does not open WhatsApp or send messages.

To use a specific Python executable:

```bash
PYTHON_COMMAND=python3.15 ./install.sh
```

## Run with one Python command

The cross-platform Python launcher creates an isolated environment, installs requirements when they change, and opens the UI. Later launches reuse the environment.

On macOS with Python 3.15, open Terminal in this folder and run:

```bash
python3.15 run.py
```

On Linux, run:

```bash
python3 run.py
```

On Windows, open Command Prompt in this folder and run either:

```bat
python run.py
```

or:

```bat
py -3 run.py
```

The launcher keeps macOS, Windows, and Linux virtual environments separate, so the same project folder can be moved between platforms. Python 3.15 on macOS uses `.venv-macos-3.15`, preventing an older environment from being reused. The Python installer from [python.org](https://www.python.org/downloads/macos/) includes the Tkinter UI dependency. On Linux, install the operating system's `python3-tk` package if the launcher reports that Tkinter is missing.

## Workflow

1. Load the Excel workbook. The first sheet must contain a recognized phone header such as `phone`, `mobile`, `whatsapp`, or `contact_number`.
2. Load the `.docx` file. Edit the message if needed. `{name}` and `{phone}` are optional per-contact placeholders.
3. Review the Ready, Invalid, and Duplicate rows. Local Malaysian numbers are normalized with country code `60` by default.
4. Keep the default 30-second interval or choose 15–300 seconds.
5. Confirm that every recipient opted in and press **Start sending**.
6. Chrome opens. Scan the WhatsApp Web QR code on the first run. The dedicated Chrome session folder preserves login for later runs.
7. Pause or stop from the desktop UI. Export a CSV report when finished.

Do not open the same dedicated Chrome session folder in another Chrome process while the sender is running.

## Diagnostics

The **Activity and diagnostics** panel at the bottom of the app shows each launch and sending stage. Use **Copy activity** to copy the visible messages or **Copy log path** to copy the location of the full persistent log.

The normal log location is:

```text
~/.mayfair-whatsapp-web/logs/app.log
```

It records the Python executable, operating system, detected Chrome executable, Chrome and ChromeDriver versions, selected profile folder, and complete Selenium tracebacks. Routine entries identify contacts by spreadsheet row rather than logging message text or full phone numbers. Logs rotate automatically.

If Chrome does not open, close any Chrome process already using the sender's dedicated session folder and try again. A launch failure now also appears in an error dialog with the diagnostic log path.

On Linux, the sender isolates system Chrome from GTK/GSettings paths inherited from Snap-packaged editors such as VS Code. Those mixed paths can otherwise make visible Chrome exit during startup with `session not created` even though headless Chrome works.

## Excel notes

Format phone cells as **Text** in Excel so `+` and leading zeroes are preserved. Numbers are normalized to an international digits-only form before WhatsApp Web is opened. Structurally invalid and duplicate numbers are never sent.

## Tests

The unit tests do not open Chrome or send messages. Run them through the same Python launcher:

```bash
python3.15 run.py --test
```

On Linux, use `python3 run.py --test`. On Windows, use `python run.py --test` or `py -3 run.py --test`.
# whats-App-Browser-Automated-message-sender
