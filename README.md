<div align="center">

<img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/ChatGPT-Image-Oct-11-2025-01_58_44-PM.png" alt="Target Tracker Logo 1" height="120">
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/ChatGPT-Image-Oct-11-2025-02_08_00-PM.png" alt="Target Tracker Logo 2" height="120">

# Target Tracker
**A fan‑made desktop tool for Torn.com players to keep tabs on targets for chains and daily hunting.**  
_Not affiliated with Torn.com. Use at your own risk and respect the game's API rules._

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![GUI](https://img.shields.io/badge/GUI-PyQt6-41b883)
![Status](https://img.shields.io/badge/Platform-Windows%2011%20%7C%20Linux%20%7C%20macOS-555)

</div>

---

## ✨ What it does

Target Tracker reads **public player details via the Torn API** using your **personal API key** and gives you a fast, glanceable table of your targets, including:

- **Status chip** — _Okay / Hospital / Jail / Abroad_ at a glance  
- **Level** and **last seen / last action** timestamps  
- **Batch refresh** with a **configurable concurrency** (fast but API‑friendly)  
- **Local caching** to avoid unnecessary re‑fetches between runs  
- **Ignore list** support (skip IDs you don’t want to monitor)  
- **In‑app target editor** to **add/edit targets** during onboarding or anytime
- **Polished PyQt6 UI** with a compact, dark layout  

> This is a community tool built for convenience. It only reads data you permit via your API key and never posts to Torn.

---

## 🖼️ Screenshots
<div align="center">
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/Screenshot-2025-10-11-132910.png" alt="Main window" width="49%">
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/Screenshot-2025-10-11-132930.png" alt="Targets table" width="49%"><br><br>
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/Screenshot-2025-10-11-133232.png" alt="Settings dialog" width="49%">
  <img src="https://raw.githubusercontent.com/Skillerious87/SwiftImageHost/main/images/Screenshot-2025-10-11-193911.png" alt="Popover & status" width="49%">
</div>

---

## 🧩 Features in detail
- **In‑app onboarding** — paste your API key, choose/create your targets file, and **add targets immediately** (paste or import).
- **Targets from file** — point the app at your `target.json` file (a list of Torn user IDs) and it’ll track them automatically.
- **Configurable concurrency** — choose how many parallel requests you want to run (default is safe and conservative).
- **Auto‑refresh** — optional; refresh on a timer or run manual refreshes as needed.
- **Local cache** — keeps recent results on disk so restarts are instant and API‑friendly.
- **Ignore list** — place player IDs in `ignore.json` to exclude them from checks.
- **Safe by design** — built‑in rate‑limiter to play nicely with Torn API limits.

---

## 🚀 Getting started

### 1) Requirements
- **Python 3.10+** (3.11 recommended)
- A Torn.com **API key** with sufficient read access (see below)
- Windows, macOS or Linux

### 2) Clone and install
```bash
git clone https://github.com/skillerious/TornTargetTracker.git
cd TornTargetTracker
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
# source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

### 3) First run
```bash
python main.py
```
On first launch, the onboarding will guide you to:
1. **Paste your Torn API key**  
2. **Choose or create** your targets file (e.g. `%APPDATA%\TargetTracker\target.json` on Windows)  
3. **Add targets now** — paste one **Torn user ID per line**, or paste **profile URLs / comma‑separated IDs** (the app extracts IDs for you).  
4. Optionally tweak **concurrency**, **auto‑refresh**, and **cache** preferences.

You can revisit all of this later via **Settings** and the **Add Targets** dialog.

---

## ➕ Adding targets
You can add targets during onboarding or anytime from the toolbar/menu.

**Input formats supported:**
- Plain numeric IDs, **one per line**:  
  ```
  3212954
  1234567
  7654321
  ```
- Comma/space‑separated lists: `3212954, 1234567 7654321`
- Torn profile URLs: the app automatically extracts the `XID`.

**Where they’re stored:** your user config directory as `target.json`. The app will create/update this file for you.

**Ignoring players:** add numeric IDs to an `ignore.json` file in the same directory; those will be skipped during refreshes.

---

## 🔑 About the Torn API key
Target Tracker uses **your personal API key** to read player data you are allowed to access.  
- You can generate/manage keys from your **Torn account settings → API Keys**.  
- Choose an access level that covers the data you want to see (public info is sufficient for level/status/last action).  
- You can revoke the key at any time from Torn.

> **Security note:** Your key is stored **locally on your machine** inside your user configuration folder. Do not share the file or commit it to Git.

---

## 🗂️ Managing your targets (file view)

Target file format (JSON array of Torn user IDs):
```json
[3212954, 1234567, 7654321]
```

- Default file name: `target.json`  
- Optional ignore file: `ignore.json` (same directory), example:
  ```json
  [1111111, 2222222]
  ```

> Tip: Keep your target list in a repo or cloud drive if you share it across devices, but **never** share your API key.

---

## ⚙️ Settings overview

| Setting | Description |
| --- | --- |
| **API key** | Personal Torn API key used for lookups. |
| **Targets file** | Path to `target.json` with the IDs you want to monitor. |
| **Add Targets** | Opens the in‑app editor to paste/import IDs (creates or updates `target.json`). |
| **Concurrency** | How many parallel lookups to run (keep modest to respect rate limits). |
| **Auto‑refresh** | Optional timer (seconds) to refresh in the background. |
| **Load cache at start** | Re‑use cached results on launch for instant UI. |

All settings are stored in your user config directory (e.g., `%APPDATA%\TargetTracker\`). You can re‑open the **Settings** dialog anytime from the toolbar.

---

## 🧱 Tech stack
- **Python + PyQt6** desktop app
- **Requests + workers** with a **rate‑limiter**
- Modular code: `api.py`, `controllers.py`, `models.py`, `views.py`, `workers.py`, `storage.py`, `settings_dialog.py`

---

## 🐞 Troubleshooting
- `ModuleNotFoundError: No module named 'PyQt6'` → run `pip install -r requirements.txt` (or `pip install PyQt6`).
- API calls failing or slow → lower **Concurrency** and/or disable **Auto‑refresh** to stay within limits.
- Empty table → add targets via onboarding or **Add Targets** dialog; ensure the IDs are numeric.
- Wrong or expired key → open **Settings**, paste a fresh API key, and save.

---

## 🧭 Project layout
A quick map of key files you’ll touch:
- `main.py` – app entry point
- `controllers.py` – UI controller & app logic
- `views.py` – widgets / view components
- `api.py` – Torn API helpers
- `workers.py` – background fetch tasks
- `rate_limiter.py` – polite API throttling
- `storage.py` – settings, cache & file paths
- `settings_dialog.py` – preferences UI
- `target.json` / `ignore.json` – your data files (in your user config directory)

---

## 🤝 Contributing
PRs and ideas are welcome! Please keep PRs focused and include screenshots for UI changes.

---

## ⚖️ License
MIT — see [LICENSE](LICENSE).

---

## 🙏 Acknowledgements & Disclaimer
- Built by the community for the community — thanks to everyone who contributes ideas and fixes.
- **Not affiliated with Torn.com**. Be mindful of their API terms and rate limits.
- All in‑game names and assets belong to their respective owners.s
