# CDP Network Audit Tool — Full User & Admin Guide

A threaded discovery utility that starts from one or more **seed** Cisco devices and crawls the
network using **Cisco Discovery Protocol (CDP)**. It connects (optionally via a **jump server**),
collects **`show cdp neighbors detail`** and **`show version`**, parses them with **TextFSM**, and
outputs a structured **Excel report** based on a supplied template. Designed for reliability,
concurrency, and repeatable reporting.

---

## ✨ Highlights
- **Parallel discovery** with a worker pool (configurable via env).  
- **Two‑tier authentication:** primary user first, then fallback **`answer`** user if primary fails.  
- **Jump server / bastion** support (Paramiko channel + Netmiko sock).  
- **DNS enrichment** for discovered hostnames.  
- **Excel report** written from a pre‑formatted template with multiple sheets.  
- **Hybrid logging:** optional `logging.conf`; sensible defaults otherwise.  

---

## 🧱 Repository layout (expected)
```
.
├── main.py
└── ProgramFiles/
    ├── textfsm/
    │   ├── cisco_ios_show_cdp_neighbors_detail.textfsm
    │   └── cisco_ios_show_version.textfsm
    └── config_files/
        ├── 1 - CDP Network Audit _ Template.xlsx
        └── logging.conf               # optional
```
> Paths are **case‑sensitive** on Linux/macOS; keep names exactly as shown.

---

## 📦 Requirements
- **Python**: 3.7+  
- **Python packages**: `pandas`, `openpyxl`, `textfsm`, `paramiko`, `netmiko`  
- *(Windows only, optional)* `pywin32` for Windows Credential Manager integration  

Install in one go:
```bash
pip install pandas openpyxl textfsm paramiko netmiko pywin32
```

### Required support files
- **TextFSM templates:**
  - `ProgramFiles/textfsm/cisco_ios_show_cdp_neighbors_detail.textfsm`
  - `ProgramFiles/textfsm/cisco_ios_show_version.textfsm`
- **Excel template:**
  - `ProgramFiles/config_files/1 - CDP Network Audit _ Template.xlsx`

The script validates presence of these files **at startup** and exits if any are missing.

---

## 🔐 Credentials model
This tool supports a **primary** credential and a fallback **`answer`** credential:
- **Primary credentials** (used for the jump and the device): read from Windows Credential Manager
  if present (default target **`MyApp/ADM`**), else prompted. You can optionally save what you type
  back to Credential Manager.
- **Fallback ‘answer’** (device hop only, jump still uses primary): username is **fixed to `answer`**.
  Password is read from Credential Manager (default target **`MyApp/Answer`**) or prompted; you may
  choose to save it.

> On non‑Windows platforms, prompts are used (no Credential Manager).

---

## 🌐 Jump server behaviour
- If **`CDP_JUMP_SERVER`** is set, connections go **via** the jump server.  
- If it is **empty**, you will be **prompted**; leaving it blank uses **direct** device connections.  
- The jump is created with Paramiko and a **`direct-tcpip`** channel; Netmiko is then bound to that
  channel (no local listener required).

> Host key policy defaults to a warning (accepts unknown keys but logs a warning). For production
> environments, prefer strict host key checking via `known_hosts` management.

---

## ⚙️ Configuration via environment variables
| Variable | Purpose | Default |
|---|---|---|
| `CDP_LIMIT` | Max concurrent worker threads | `10` |
| `CDP_TIMEOUT` | SSH/auth/read timeouts (seconds) | `10` |
| `CDP_JUMP_SERVER` | Jump host (IP/hostname). Empty = direct | *(empty)* |
| `CDP_PRIMARY_CRED_TARGET` | CredMan target for primary creds | `MyApp/ADM` |
| `CDP_ANSWER_CRED_TARGET` | CredMan target for ‘answer’ password | `MyApp/Answer` |
| `LOGGING_CONFIG` | Path to INI logging config | `ProgramFiles/Config_Files/logging.conf` (searched) |

### Example
```powershell
# Windows PowerShell
$env:CDP_LIMIT = "20"
$env:CDP_TIMEOUT = "15"
$env:CDP_JUMP_SERVER = "bastion.corp.local"
$env:CDP_PRIMARY_CRED_TARGET = "MyApp/ADM"
$env:CDP_ANSWER_CRED_TARGET = "MyApp/Answer"
$env:LOGGING_CONFIG = "ProgramFiles/Config_Files/logging.conf"
```

---

## 🚀 How to run (interactive flow)
1. **Ensure templates and Excel file exist** under `ProgramFiles/...` (see above).  
2. **Set env vars** as needed (optional).  
3. Run:
   ```bash
   python -m main
   # or: python main.py
   ```
4. **Follow prompts**:
   - **Site name** (used in the output filename)
   - **Seed devices** (comma‑separated IPv4 / resolvable hostnames)
   - **Primary credentials** (reads from CredMan if present; else prompts; optional save)
   - **‘answer’ password** (reads from CredMan if present; else prompts; optional save)
   - **Jump server** (from env or prompt; blank = direct)

The tool validates/normalizes seeds to IP addresses, de‑duplicates them, then starts the
**threaded discovery**.

---

## 🧪 What gets collected
For each visited device the tool attempts to collect:
- **`show version`** (hostname, serials, uptime) — for local context.
- **`show cdp neighbors detail`** — parsed into structured rows.
- **DNS resolution** for all discovered hostnames (best‑effort), in parallel.

### Discovery heuristics
- Only **Switch**‑capable CDP entries (and **not** hosts) with a **management IP** are queued as
  crawl candidates.  
- Deduplication is performed by **hostname** and **IP** to reduce churn.  
- Each target is retried up to **3** times for transient connectivity issues.

---

## 📤 Excel output
An output file named **`<site_name>_CDP_Network_Audit.xlsx`** is created by copying the template.

### Sheets
- **Audit** — Main CDP dataset. Also stamped with metadata:
  - `B4`: Site name  
  - `B5`: Date  
  - `B6`: Time  
  - `B7`: Primary seed  
  - `B8`: Secondary seed (or “Secondary Seed device not given”)  
- **DNS Resolved** — Two columns: `Hostname`, `IP Address`  
- **Authentication Errors** — One column: `Authentication Errors` (IP list)  
- **Connection Errors** — Two columns: `IP Address`, `Error`

### Columns in **Audit** (data rows)
`LOCAL_HOST`, `LOCAL_IP`, `LOCAL_PORT`, `LOCAL_SERIAL`, `LOCAL_UPTIME`,
`DESTINATION_HOST`, `REMOTE_PORT`, `MANAGEMENT_IP`, `PLATFORM`.

> The template governs formatting/filters/charts (if any). The writer appends data starting at the
> appropriate row offsets to preserve the layout.

---

## 🧰 Logging
- If a config file is present, logging is configured via **`logging.config.fileConfig()`**.  
- Otherwise, a **basic console logger** is configured at **INFO** with timestamps.  
- You can set `LOGGING_CONFIG` to point to an INI file anywhere; if not set, the tool looks for
  `ProgramFiles/Config_Files/logging.conf`.

---

## 🧯 Errors & retry behaviour
- **Authentication failures**: the host is recorded under **Authentication Errors**.  
- **Connectivity/timeouts**: the host is recorded under **Connection Errors** with the last error tag
  (e.g., `NetmikoTimeoutException`, `SSHException`, `socket.timeout`).  
- **Retries**: up to **3** attempts for each device before recording a connection error.  
- **Graceful exit**: workers always `task_done()` to avoid queue hangs.

---

## 📈 Performance
- Worker threads = `CDP_LIMIT` (default **10**).  
- DNS resolution runs in a small parallel pool after discovery.  
- Use a conservative limit on older/CPU‑bound platforms; increase on fast links.

---

## 🔒 Security considerations
- Prefer **Credential Manager** (Windows) or other secret stores instead of plaintext.  
- Ensure **jump host** is hardened; consider strict host key verification.  
- Output workbooks can contain sensitive topology data — share on a **need‑to‑know** basis.

---

## ❌ Exit codes
- **0** — Success  
- **1** — Required TextFSM or Excel template missing / invalid  
- **130** — Interrupted by user (Ctrl+C)

---

## ✅ Example session
```text
=== CDP Network Audit ===
Enter site name (used in Excel filename, max 50 chars): MKD-Campus
Enter one or more seed device IPs or hostnames (comma-separated, max 500): 10.10.0.11, sw-core-1
...
Press Enter to accept, or type a different username: opsadmin
Enter switch/jump password (Primary): ********
Store Primary creds in Credential Manager as 'MyApp/ADM'? [y/N]
Enter 'answer' password: ********
Store 'answer' password in Credential Manager as 'MyApp/Answer'? [y/N]

Enter jump server IP/hostname (or leave blank to use device directly)
Enter IP Address: bastion.corp.local

INFO Validated 2 seed device(s) for discovery
... (threaded discovery logs) ...
Done!
 Discovered devices: 42
 CDP entries: 314
 Auth errors: 1
 Conn errors: 3
```

---

## 🛠️ Customization points
- **Template paths**: adjust in `main.py` under the `ProgramFiles/...` constants.  
- **Queueing heuristics** (which neighbors to crawl): `parse_outputs_and_enqueue_neighbors()`.  
- **Retry counts / timeouts**: via env or tweak in code.  
- **Logging**: provide a `logging.conf` that matches your standards.  

---

## 📝 License
GNU General Public License v3.0

## 👤 Author
Christopher Davies
