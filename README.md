<div align="center">

<img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/ChatGPT-Image-Oct-11-2025-01_58_44-PM.png" alt="Target Tracker logo 1" height="120">
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/ChatGPT-Image-Oct-11-2025-02_08_00-PM.png" alt="Target Tracker logo 2" height="120">

# Target Tracker
**A fan-made desktop assistant for Torn.com players who keep personal target lists for chains, dailies, or revives.**  
_Not affiliated with Torn.com. Respect the game's API rules and your key's access level._

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![GUI](https://img.shields.io/badge/GUI-PyQt6-41b883)
![Platform](https://img.shields.io/badge/Platform-Windows%2011%20%7C%20macOS%20%7C%20Linux-555)

</div>

---

## Highlights
- Live, color-coded target table with status chips, level, faction, timers, and per-row diagnostics.
- Search bar with field scope, regex toggle, case sensitivity, status filters, level bounds, and a hide-ignored option.
- Auto-refresh engine with countdown pill, online/offline detection, and manual refresh guardrails.
- Safe concurrent fetching backed by a global rate limiter, exponential backoff, and cooperative shutdown.
- CSV and JSON exports, toolbar/context shortcuts, and confirmation flows for adding, removing, and ignoring targets.
- Dedicated ignore manager with search, open/unignore actions, and import/export for lists you share with others.
- Guided onboarding plus an in-app documentation browser with navigation, search, and data-folder helpers.
- About dialog reads https://skillerious.github.io/Version-Tracker/?format=code&app=target-tracker to compare versions, surface release notes, and link to updates.

---

## Screenshots
<div align="center">
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/TargetTracker/Screenshot-2025-10-21-193013.png" alt="Target Tracker main interface" width="90%"><br><br>
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/TargetTracker/Screenshot-2025-10-21-193026.png" alt="Settings dialog tabs" width="45%">
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/TargetTracker/Screenshot-2025-10-21-193337.png" alt="Ignore manager dialog" width="45%"><br><br>
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/TargetTracker/Screenshot-2025-10-21-193319.png" alt="Built-in help and documentation" width="45%">
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/TargetTracker/Screenshot-2025-10-21-193751.png" alt="About dialog with update status" width="45%">
</div>

---

## Feature tour

**Main workspace**
- Sortable columns for name, ID, level, status, details, hospital/jail until, faction, last action, and errors.
- Toolbar and context menu for refresh, export CSV/JSON, copy IDs, open profile or attack URLs, ignore/unignore, and remove.
- Search scopes (All, Name, ID, Faction) with regex and case toggles plus level and status filters that update instantly.

**Status bar**
- Glass pills show totals, visible counts, ignored totals, cached hits, and error counts.
- Progress meter and auto-refresh countdown change color based on success or fetch issues.
- Connectivity probes pause timers when offline and resume scheduling once the network returns.

**Managing targets**
- Add Targets dialog accepts IDs, comma or space lists, and Torn profile URLs, previewing parsed results before saving.
- Remove Targets dialog summarizes affected rows and warns that the action cannot be undone.
- Ignore dialog includes search, open profile, unignore, and import/export of ignore lists.

**Documentation and onboarding**
- First-run onboarding covers API keys, storage, input formats, and safe pacing.
- Documentation dialog offers tabbed sections, table-of-contents navigation, search, jump-to combo, and quick data-folder actions.

**Updates and diagnostics**
- About dialog compares local vs remote version, surfaces the latest release notes, and links to GitHub releases.
- Logging channels (TargetTracker.*) keep diagnosis output tidy without cluttering the UI.

---

## Getting started

### Requirements
- Python 3.10 or newer (3.11+ recommended).
- Torn.com API key with Limited Access scope.
- Windows, macOS, or Linux with a desktop environment.

### Installation
```bash
git clone https://github.com/skillerious/TornTargetTracker.git
cd TornTargetTracker
python -m venv venv
```

Activate the virtual environment:
```bash
# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

Install dependencies:
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### First run
```bash
python main.py
```
The onboarding flow helps you:
1. Paste or create a Limited Access Torn API key (stored locally only).
2. Choose or create `target.json` (default lives in `%APPDATA%\TargetTracker` on Windows).
3. Paste IDs, comma-separated lists, or profile URLs to populate your first target list.
4. Pick concurrency, auto-refresh cadence, and caching defaults; adjust any time in Settings.

---

## Managing data

- `target.json` stores the Torn user IDs you monitor (JSON array of integers).
- `ignore.json` tracks IDs you want to skip; the UI keeps the table in sync.
- Cached API responses live in your app data folder so warm starts are instant when caching is enabled.
- Settings dialog provides buttons to open the app data folder, clear caches, and create a fresh targets file.

**Supported input formats**
```
3212954
3212954, 1234567, 7654321
https://www.torn.com/profiles.php?XID=3212954
```

---

## Settings at a glance

| Tab | Highlights |
| --- | --- |
| General | Manage API key (show, paste, test), pick or create target file, decide if the main window starts maximized. |
| Data & Cache | Jump to the app data folder, preload cache on startup, tune cache save cadence, clear cache safely. |
| Performance | Sliders for concurrency, auto-refresh interval, rate cap per minute, and minimum interval; includes a Torn-safe preset. |
| Retries & Backoff | Configure retry count, backoff base and ceiling, and whether to honor Retry-After headers; preview shows effective timings. |
| Help | Handy links to API docs, release notes, GitHub issues, Discord, and data-folder actions. |

Apply commits changes immediately and the controller updates running workers where possible.

---

## Built-in help and tooling
- Help -> Documentation opens a full guide covering setup, workflows, troubleshooting, and shortcuts.
- Help -> Copy Diagnostics copies version, Python, PyQt, rate limiter, and path info for support requests.
- Status bar icon tooltips surface last update time, error counts, and quick hints.

---

## Tech stack
- Python + PyQt6 widgets (`views.py`, `search_bar.py`, `statusbar.py`).
- Worker pool built on `QThreadPool` for concurrent Torn API calls.
- `rate_limiter.py` implements a token bucket with cooldown penalties.
- `storage.py` manages app data paths, settings, cache serialization, and ignore lists.

---

## Project layout
- `main.py` - application bootstrap and controller wiring.
- `controllers.py` - main window, menus, and actions.
- `views.py` - table model, main view, about, ignore, add/remove dialogs.
- `api.py` - Torn API client with retry/backoff logic.
- `workers.py` - background fetch orchestration.
- `settings_dialog.py` - tabbed preferences UI with API testing.
- `documentation.py` - in-app help browser.
- `storage.py` - settings, cache, and path helpers.
- `rate_limiter.py` - global throttling primitives.

---

## Contributing
Bug reports, feature suggestions, and pull requests are welcome. Include screenshots or clips for UI tweaks to speed up review.

---

## License
Released under the MIT License. See [LICENSE](LICENSE).

---

## Disclaimer
- Target Tracker is an unofficial community tool. Use it responsibly and follow Torn.com's API terms.
- The application only makes read-only requests with the API key you supply. Keys and cached data never leave your machine.
- Torn.com, Torn, and related assets remain the property of their respective owners.
