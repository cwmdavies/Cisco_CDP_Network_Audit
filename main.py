#!/usr/bin/env python3
# -*- coding: cp1252 -*-

"""
CDP Network Audit Tool

This script performs automated discovery and documentation of Cisco network devices using
Cisco Discovery Protocol (CDP). Starting from one or more seed devices, it connects
(optionally via a jump server), collects 'show cdp neighbors detail' and 'show version'
outputs, parses them via TextFSM, and writes a structured Excel report.

Key features:
- Threaded discovery with a worker pool (configurable via env).
- Two-tier authentication (primary user, then fallback 'answer' user).
- Optional SSH jump/proxy host using Paramiko + Netmiko 'sock' channel.
- DNS resolution for discovered hostnames.
- Structured Excel output based on a supplied template.
- Hybrid logging: loads a logging.conf if present; otherwise uses sane defaults.

Environment variables (optional):
- CDP_LIMIT       : Max concurrent workers (default: 10)
- CDP_TIMEOUT     : Per-step timeout (seconds) for SSH/auth/reads (default: 10)
- CDP_JUMP_SERVER : Jump host (hostname/IP). If empty, connect directly to devices.
- CDP_PRIMARY_CRED_TARGET : Windows Credential Manager target for primary creds (default: "MyApp/ADM")
- CDP_ANSWER_CRED_TARGET  : Windows Credential Manager target for 'answer' password (default: "MyApp/Answer")
- LOGGING_CONFIG  : Path to an INI-style logging config. Overrides default search.

Expected files (relative to repo root unless you pass absolute paths in code):
- ProgramFiles/textfsm/cisco_ios_show_cdp_neighbors_detail.textfsm
- ProgramFiles/textfsm/cisco_ios_show_version.textfsm
- ProgramFiles/config_files/1 - CDP Network Audit _ Template.xlsx
- (Optional) ProgramFiles/Config_Files/logging.conf   # Note the capital 'C' and 'F'

Exit codes:
- 0  : Success
- 1  : Required template or Excel file missing
- 130: Interrupted by user (Ctrl+C)

Author: Christopher Davies
Email: chris.davies@weavermanor.co.uk
"""
import os
import sys
import threading
import queue
import socket
import shutil
import datetime
import ipaddress
import logging
import logging.config
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple, List, Dict
import time
import pandas as pd
import openpyxl
import textfsm
import paramiko
from netmiko import ConnectHandler
try:
    # Newer netmiko
    from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
except ImportError:
    # Older netmiko naming
    from netmiko.ssh_exception import NetmikoAuthenticationException, NetmikoTimeoutException
from paramiko.ssh_exception import SSHException

# --------------------------------------------------------------------------------------
# Logging bootstrap (HYBRID): try fileConfig() if a logging.conf exists; otherwise fallback
# --------------------------------------------------------------------------------------
def _configure_logging() -> None:
    """
    Configure logging using an INI file if available, else a sensible basicConfig.

    Search order:
    1) LOGGING_CONFIG environment variable (absolute or relative path)
    2) ProgramFiles/Config_Files/logging.conf (repository default)

    If neither path exists, configure a basic console logger at INFO level.
    """
    cfg_env = os.getenv("LOGGING_CONFIG", "").strip()
    default_cfg = Path("ProgramFiles") / "Config_Files" / "logging.conf"  # case-sensitive on non-Windows
    cfg_path = Path(cfg_env) if cfg_env else default_cfg

    if cfg_path.exists():
        # Keep existing library loggers (paramiko/netmiko) unless explicitly overridden in the file.
        logging.config.fileConfig(str(cfg_path), disable_existing_loggers=False)
    else:
        # Fallback: console INFO; timestamps include date for easier triage
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

_configure_logging()
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Minimal config (can be overridden with environment variables)
# --------------------------------------------------------------------------------------
DEFAULT_LIMIT = int(os.getenv("CDP_LIMIT", "10"))
DEFAULT_TIMEOUT = int(os.getenv("CDP_TIMEOUT", "10"))

BASE_DIR = Path(".")
# NOTE: These paths are case-sensitive on Linux/macOS. Keep them consistent in your repo.
CDP_TEMPLATE = BASE_DIR / "ProgramFiles" / "textfsm" / "cisco_ios_show_cdp_neighbors_detail.textfsm"
VER_TEMPLATE = BASE_DIR / "ProgramFiles" / "textfsm" / "cisco_ios_show_version.textfsm"
EXCEL_TEMPLATE = BASE_DIR / "ProgramFiles" / "config_files" / "1 - CDP Network Audit _ Template.xlsx"


class CredentialManager:
    """
    Helper class to collect credentials from:
    - Windows Credential Manager (when on Windows and entries exist)
    - Interactive prompts (fallback)
    - Optional persistence back to Windows Credential Manager

    The class favors non-intrusive operation: read if present; prompt if missing;
    ask before writing to the credential store.
    """

    def __init__(self):
        self.primary_target = os.getenv("CDP_PRIMARY_CRED_TARGET", "MyApp/ADM")
        self.answer_target = os.getenv("CDP_ANSWER_CRED_TARGET", "MyApp/Answer")

    def _read_win_cred(self, target_name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Attempt to read a generic credential from Windows Credential Manager.

        Returns:
            (username, password) or (None, None) if not available or not on Windows.
        """
        try:
            if not sys.platform.startswith("win"):
                return None, None
            import win32cred  # type: ignore
            cred = win32cred.CredRead(target_name, win32cred.CRED_TYPE_GENERIC)  # type: ignore
            user = cred.get("UserName")
            blob = cred.get("CredentialBlob")
            pwd = blob.decode("utf-16le") if blob else None
            if user and pwd:
                return user, pwd
        except Exception:
            logger.debug("Reading credentials from Windows Credential Manager failed.", exc_info=True)
        return None, None

    def _write_win_cred(self, target: str, username: str, password: str, persist: int = 2) -> bool:
        """
        Write or update a generic credential in Windows Credential Manager.

        Args:
            target: Credential target name (e.g., 'MyApp/ADM').
            username: Username to store.
            password: Password to store.
            persist: Persistence (2 = local machine).

        Returns:
            True if the write succeeded, False otherwise.
        """
        try:
            if not sys.platform.startswith("win"):
                logger.warning("Not a Windows platform; cannot store credentials in Credential Manager.")
                return False
            import win32cred  # type: ignore

            # Prefer bytes; fallback to str if the installed pywin32 expects unicode.
            blob_bytes = password.encode("utf-16le")
            credential = {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": username,
                "CredentialBlob": blob_bytes,
                "Comment": "Created by CDP Network Audit tool",
                "Persist": persist,
            }
            try:
                win32cred.CredWrite(credential, 0)
            except TypeError as te:
                logger.debug("CredWrite rejected bytes for CredentialBlob (%s). Retrying with unicode string.", te)
                credential["CredentialBlob"] = password
                win32cred.CredWrite(credential, 0)
            logger.info("Stored/updated credentials in Windows Credential Manager: %s", target)
            return True
        except Exception:
            logger.exception("Failed to write credentials for '%s'", target)
            return False

    def _prompt_yes_no(self, msg: str, default_no: bool = True) -> bool:
        """Simple interactive [y/N] or [Y/n] prompt."""
        suffix = " [y/N] " if default_no else " [Y/n] "
        ans = input(msg + suffix).strip().lower()
        if ans == "":
            return not default_no
        return ans in ("y", "yes")

    def get_secret_with_fallback(
        self,
        display_name: str,
        cred_target: Optional[str] = None,
        prompt_user: Optional[str] = None,
        prompt_pass: Optional[str] = None,
        fixed_username: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Obtain credentials with this order of preference:
        1) If cred_target provided and on Windows, try to read from Credential Manager.
        2) Prompt the user (optionally fixing the username, e.g., 'answer').

        Returns:
            (username, password)
        """
        if cred_target and sys.platform.startswith("win"):
            u, p = self._read_win_cred(cred_target)
            if u and p:
                if fixed_username and fixed_username.lower() != u.lower():
                    logger.info(
                        "Loaded %s password from CredMan (%s). Using fixed username '%s'.",
                        display_name, cred_target, fixed_username
                    )
                    return fixed_username, p
                logger.info("Loaded %s credentials from Windows Credential Manager (%s).", display_name, cred_target)
                return (fixed_username or u), p

        # Fall back to interactive prompt
        import getpass
        if fixed_username:
            user = fixed_username
            if not prompt_pass:
                prompt_pass = f"Enter {display_name} password: "
            pwd = getpass.getpass(prompt_pass)
            if not pwd:
                raise RuntimeError(f"{display_name} password not provided.")
            return user, pwd

        if not prompt_user:
            prompt_user = f"Enter {display_name} username: "
        if not prompt_pass:
            prompt_pass = f"Enter {display_name} password: "
        user = input(prompt_user).strip()
        pwd = getpass.getpass(prompt_pass)
        if not user or not pwd:
            raise RuntimeError(f"{display_name} credentials not provided.")
        return user, pwd

    def prompt_for_inputs(self):
        """
        Interactively collect:
        - Site name (for the report filename)
        - Seed IPs/hostnames (comma-separated)
        - Primary credentials (read from CredMan if present, else prompt)
        - 'answer' password (read from CredMan if present, else prompt + fixed username)
        """
        logger.info("=== CDP Network Audit ===")

        site_name = input("Enter site name (used in Excel filename): ").strip()
        while not site_name:
            site_name = input("Site name cannot be empty. Please enter site name: ").strip()

        seed_str = input("Enter one or more seed device IPs or hostnames (comma-separated): ").strip()
        while not seed_str:
            seed_str = input("Seed IPs cannot be empty. Please enter one or more IPs: ").strip()
        seeds = [s.strip() for s in seed_str.split(",") if s.strip()]

        # Primary credentials: prefer CredMan, allow override, and optional re-save
        stored_user, stored_pass = self._read_win_cred(self.primary_target) if sys.platform.startswith("win") else (None, None)
        if stored_user and stored_pass:
            logger.info("Found stored Primary user: %s (target: %s)", stored_user, self.primary_target)
            override = input("Press Enter to accept, or type a different username: ").strip()
            if override:
                import getpass
                primary_user = override
                primary_pass = getpass.getpass("Enter switch/jump password (Primary): ")
                if self._prompt_yes_no(f"Save these Primary creds to Credential Manager as '{self.primary_target}'?", default_no=True):
                    self._write_win_cred(self.primary_target, primary_user, primary_pass)
            else:
                primary_user, primary_pass = stored_user, stored_pass
        else:
            primary_user, primary_pass = self.get_secret_with_fallback(
                display_name="Primary (jump/device)",
                cred_target=None,
                prompt_user="Enter switch/jump username (Primary): ",
                prompt_pass="Enter switch/jump password (Primary): ",
            )
            if self._prompt_yes_no(f"Store Primary creds in Credential Manager as '{self.primary_target}'?", default_no=True):
                self._write_win_cred(self.primary_target, primary_user, primary_pass)

        # 'answer' credentials: fixed username 'answer'; prefer CredMan password if present
        answer_user = "answer"
        a_user, a_pass = self._read_win_cred(self.answer_target) if sys.platform.startswith("win") else (None, None)
        if a_user and a_pass:
            logger.info("Loaded Answer password from Credential Manager (%s). Username fixed to 'answer'.", self.answer_target)
            answer_pass = a_pass
        else:
            _, answer_pass = self.get_secret_with_fallback(
                display_name="Answer (device fallback)",
                cred_target=None,
                prompt_user=None,
                prompt_pass="Enter 'answer' password: ",
                fixed_username="answer",
            )
            if self._prompt_yes_no(f"Store 'answer' password in Credential Manager as '{self.answer_target}'?", default_no=True):
                self._write_win_cred(self.answer_target, answer_user, answer_pass)

        return site_name, seeds, primary_user, primary_pass, answer_user, answer_pass


class ExcelReporter:
    """Handles writing the discovery results to an Excel workbook based on a template."""

    def __init__(self, excel_template: Path):
        """
        Args:
            excel_template: Path to the Excel template file (.xlsx) that contains
                            pre-formatted sheets: 'Audit', 'DNS Resolved',
                            'Authentication Errors', 'Connection Errors'.
        """
        self.excel_template = excel_template

    def save_to_excel(
        self,
        details_list: List[Dict],
        hosts: List[str],
        site_name: str,
        dns_ip: Dict[str, str],
        auth_errors: set,
        conn_errors: Dict[str, str],
    ) -> None:
        """
        Persist the collected data to an Excel file cloned from the template.

        The 'Audit' sheet header cells B4..B8 are populated with metadata.
        Parsed CDP rows are appended to 'Audit' from row 12 (0-based index adjusted).
        Other sheets receive their corresponding arrays at row 5.
        """
        # Build DataFrames for each sheet
        df = pd.DataFrame(
            details_list,
            columns=[
                "LOCAL_HOST", "LOCAL_IP", "LOCAL_PORT", "LOCAL_SERIAL", "LOCAL_UPTIME",
                "DESTINATION_HOST", "REMOTE_PORT", "MANAGEMENT_IP", "PLATFORM",
            ],
        )
        dns_array = pd.DataFrame(dns_ip.items(), columns=["Hostname", "IP Address"])
        auth_array = pd.DataFrame(sorted(list(auth_errors)), columns=["Authentication Errors"])
        conn_array = pd.DataFrame(conn_errors.items(), columns=["IP Address", "Error"])

        # Create the output workbook by copying the template
        filepath = f"{site_name}_CDP_Network_Audit.xlsx"
        shutil.copy2(src=self.excel_template, dst=filepath)

        # Stamp metadata
        date_now = datetime.datetime.now().strftime("%d %B %Y")
        time_now = datetime.datetime.now().strftime("%H:%M")
        wb = openpyxl.load_workbook(filepath)
        ws1 = wb["Audit"]
        ws1["B4"] = site_name
        ws1["B5"] = date_now
        ws1["B6"] = time_now
        ws1["B7"] = hosts[0] if hosts else ""
        ws1["B8"] = hosts[1] if len(hosts) > 1 else "Secondary Seed device not given"
        wb.save(filepath)
        wb.close()

        # Append tabular data using openpyxl engine in overlay mode
        with pd.ExcelWriter(filepath, engine="openpyxl", if_sheet_exists="overlay", mode="a") as writer:
            df.to_excel(writer, index=False, sheet_name="Audit", header=False, startrow=11)
            dns_array.to_excel(writer, index=False, sheet_name="DNS Resolved", header=False, startrow=4)
            auth_array.to_excel(writer, index=False, sheet_name="Authentication Errors", header=False, startrow=4)
            conn_array.to_excel(writer, index=False, sheet_name="Connection Errors", header=False, startrow=4)


class NetworkDiscoverer:
    """
    Coordinate threaded discovery via Netmiko, parse outputs via TextFSM, and
    accumulate results for reporting.

    Thread-safety:
    - `visited_lock` protects `visited` and `enqueued` (queue membership sets).
    - `data_lock` protects data structures appended/updated by worker threads.
    """

    def __init__(self, timeout: int, limit: int, cdp_template: Path, ver_template: Path):
        self.timeout = timeout
        self.limit = limit
        self.cdp_template = cdp_template
        self.ver_template = ver_template

        # Accumulators and thread-shared state
        self.cdp_neighbour_details: List[Dict] = []
        self.hostnames: set = set()
        self.visited: set = set()           # IPs we've completed attempts for
        self.enqueued: set = set()          # IPs currently scheduled in the queue
        self.visited_hostnames: set = set() # Hostnames we've seen (for dedupe)
        self.authentication_errors: set = set()
        self.connection_errors: Dict[str, str] = {}
        self.dns_ip: Dict[str, str] = {}

        # Locks and work queue
        self.visited_lock = threading.Lock()
        self.data_lock = threading.Lock()
        self.host_queue: "queue.Queue[str]" = queue.Queue()

    # -------------------------- Parsing helpers --------------------------
    def _safe_parse_textfsm(self, template_path: Path, text: str) -> List[Dict]:
        """
        Parse `text` using a TextFSM template. On failure, return an empty list and log at DEBUG.

        Returns:
            List of dicts keyed by template headers, or [] if parse fails.
        """
        try:
            with open(template_path, "r", encoding="cp1252") as f:
                table = textfsm.TextFSM(f)
                rows = table.ParseText(text or "")
                return [dict(zip(table.header, row)) for row in rows]
        except (OSError, textfsm.TextFSMError) as e:
            logger.debug("TextFSM parse failed for %s: %s", template_path, e, exc_info=True)
            return []
        except Exception:
            logger.exception("Unexpected error while parsing template %s", template_path)
            return []

    def parse_outputs_and_enqueue_neighbors(self, host: str, cdp_output: str, version_output: str) -> None:
        """
        Extract local device attributes and CDP neighbor entries, enrich rows, and enqueue
        candidate neighbor management IPs for further crawling.
        """
        cdp_list = self._safe_parse_textfsm(self.cdp_template, cdp_output)
        ver_list = self._safe_parse_textfsm(self.ver_template, version_output)

        if ver_list:
            hostname = ver_list[0].get("HOSTNAME", host)
            serial_numbers = ver_list[0].get("SERIAL", "")
            uptime = ver_list[0].get("UPTIME", "")
        else:
            hostname = host
            serial_numbers = ""
            uptime = ""

        # Track local hostname and mark IP visited
        with self.data_lock:
            if hostname:
                self.hostnames.add(hostname)
                self.visited_hostnames.add(hostname)
        with self.visited_lock:
            self.visited.add(host)

        # Enrich CDP entries and collect neighbors
        for entry in cdp_list:
            text = entry.get("DESTINATION_HOST", "")
            head = text.split(".", 1)[0].upper() if text else ""
            entry["DESTINATION_HOST"] = head
            entry["LOCAL_HOST"] = hostname
            entry["LOCAL_IP"] = host
            entry["LOCAL_SERIAL"] = serial_numbers
            entry["LOCAL_UPTIME"] = uptime

            with self.data_lock:
                self.cdp_neighbour_details.append(entry)

            caps = entry.get("CAPABILITIES", "")
            mgmt_ip = entry.get("MANAGEMENT_IP", "")

            # Heuristic: enqueue only devices that look like switches (not "Host"),
            # and only if we have a management IP.
            if "Switch" in caps and "Host" not in caps and mgmt_ip:
                # Deduplicate by hostname first to reduce queue churn
                with self.data_lock:
                    if head in self.visited_hostnames:
                        continue
                    if head:
                        self.visited_hostnames.add(head)

                # Avoid double enqueueing the same IP while it's pending
                should_enqueue = False
                with self.visited_lock:
                    if mgmt_ip not in self.visited and mgmt_ip not in self.enqueued:
                        self.enqueued.add(mgmt_ip)
                        should_enqueue = True
                if should_enqueue:
                    logger.debug("Enqueuing neighbor %s (%s) discovered from %s", head, mgmt_ip, host)
                    self.host_queue.put(mgmt_ip)

    # -------------------------- Connectivity helpers --------------------------
    def _paramiko_jump_client(self, jump_host: str, username: str, password: str) -> paramiko.SSHClient:
        """
        Establish an SSH client to the jump host (no agent/keys; password auth).
        Returns a connected Paramiko SSHClient.
        """
        client = paramiko.SSHClient()
        # Use explicit class reference to avoid AttributeError on some builds
        client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
        client.connect(
            hostname=jump_host,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=self.timeout,
            auth_timeout=self.timeout,
            timeout=self.timeout,
        )
        return client

    def _netmiko_via_jump(
        self,
        jump_host: str,
        target_ip: str,
        primary: bool,
        primary_user: str,
        primary_pass: str,
        answer_user: str,
        answer_pass: str,
    ):
        """
        Create a Netmiko connection either:
        - Directly to `target_ip` (when no `jump_host` provided), or
        - Through a Paramiko 'direct-tcpip' channel via `jump_host`.

        The `primary` flag determines which credentials are used for the device hop.
        """
        if primary:
            j_user, j_pass = primary_user, primary_pass     # Jump with primary
            d_user, d_pass = primary_user, primary_pass     # Device with primary
        else:
            j_user, j_pass = primary_user, primary_pass     # Jump still uses primary
            d_user, d_pass = answer_user, answer_pass       # Device uses fallback 'answer'

        # Direct connection path (no jump)
        if not jump_host:
            conn = ConnectHandler(
                device_type="cisco_ios",
                host=target_ip,
                username=d_user,
                password=d_pass,
                fast_cli=False,
                timeout=self.timeout,
                conn_timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
            )
            return conn

        # Jump path
        jump = None
        try:
            jump = self._paramiko_jump_client(jump_host, j_user, j_pass)
            transport = jump.get_transport()
            dest_addr = (target_ip, 22)
            local_addr = ("127.0.0.1", 0)
            channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)

            conn = ConnectHandler(
                device_type="cisco_ios",
                host=target_ip,
                username=d_user,
                password=d_pass,
                sock=channel,      # <-- send Netmiko traffic through the Paramiko channel
                fast_cli=False,
                timeout=self.timeout,
                conn_timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
            )
            # Remember the jump client to close it later
            conn._jump_client = jump  # type: ignore[attr-defined]
            return conn
        except Exception:
            if jump is not None:
                try:
                    jump.close()
                except Exception:
                    logger.debug("Failed to close jump client after error.", exc_info=True)
            raise

    def run_device_commands(
        self,
        jump_host: str,
        host: str,
        primary_user: str,
        primary_pass: str,
        answer_user: str,
        answer_pass: str,
    ) -> Tuple[str, str]:
        """
        Try to collect required outputs from `host` using primary creds, then fallback user on auth failure.

        Returns:
            (cdp_output, version_output)

        Raises:
            NetmikoAuthenticationException if both primary and fallback auth fail.
            Other exceptions for connectivity/timeout are propagated to caller for retry handling.
        """
        try:
            conn = self._netmiko_via_jump(
                jump_host=jump_host,
                target_ip=host,
                primary=True,
                primary_user=primary_user,
                primary_pass=primary_pass,
                answer_user=answer_user,
                answer_pass=answer_pass,
            )
            try:
                out_cdp = conn.send_command("show cdp neighbors detail", expect_string=r"#", read_timeout=self.timeout)
                out_ver = conn.send_command("show version", expect_string=r"#", read_timeout=self.timeout)
                return out_cdp, out_ver
            finally:
                # Always try to close cleanly
                try:
                    conn.disconnect()
                except Exception:
                    logger.debug("Error disconnecting Netmiko connection", exc_info=True)
                try:
                    if hasattr(conn, "_jump_client") and conn._jump_client:  # type: ignore[attr-defined]
                        conn._jump_client.close()
                except Exception:
                    logger.debug("Error closing jump client after disconnect", exc_info=True)

        except NetmikoAuthenticationException:
            # Retry once using 'answer' user (device hop only; jump still uses primary)
            logger.debug("Primary authentication failed for %s; attempting fallback user 'answer'", host)
            conn = None
            try:
                conn = self._netmiko_via_jump(
                    jump_host=jump_host,
                    target_ip=host,
                    primary=False,
                    primary_user=primary_user,
                    primary_pass=primary_pass,
                    answer_user=answer_user,
                    answer_pass=answer_pass,
                )
                try:
                    out_cdp = conn.send_command("show cdp neighbors detail", expect_string=r"#", read_timeout=self.timeout)
                    out_ver = conn.send_command("show version", expect_string=r"#", read_timeout=self.timeout)
                    return out_cdp, out_ver
                finally:
                    try:
                        conn.disconnect()
                    except Exception:
                        logger.debug("Error disconnecting Netmiko connection (fallback)", exc_info=True)
                    try:
                        if hasattr(conn, "_jump_client") and conn._jump_client:  # type: ignore[attr-defined]
                            conn._jump_client.close()
                    except Exception:
                        logger.debug("Error closing jump client after disconnect (fallback)", exc_info=True)
            except NetmikoAuthenticationException:
                # Both attempts failed
                logger.info("Authentication failed for both primary and fallback on %s", host)
                with self.data_lock:
                    self.authentication_errors.add(host)
                raise

    # -------------------------- Worker & DNS --------------------------


    def discover_worker(self, jump_host, primary_user, primary_pass, answer_user, answer_pass) -> None:
        tname = threading.current_thread().name
        logger.info("Worker start: %s", tname)
        try:
            while True:
                try:
                    # Block for a bit; this prevents hot spinning and accidental early exits
                    item = self.host_queue.get(timeout=1.0)
                except queue.Empty:
                    # Just wait again; neighbors may still be enqueued by other workers
                    time.sleep(0.2)
                    continue

                # Sentinel to shut down this worker
                if item is None:
                    self.host_queue.task_done()
                    logger.info("Worker exit (sentinel): %s", tname)
                    return

                host = item

                # Host has been dequeued — remove from 'enqueued' so it's eligible on explicit re-queue.
                with self.visited_lock:
                    self.enqueued.discard(host)

                if host in self.visited:
                    self.host_queue.task_done()
                    continue

                last_err = None
                for attempt in range(1, 4):
                    logger.info("[%s] %s Attempt %d: collecting CDP + version", host, tname, attempt)
                    try:
                        cdp_out, ver_out = self.run_device_commands(
                            jump_host, host, primary_user, primary_pass, answer_user, answer_pass
                        )
                        self.parse_outputs_and_enqueue_neighbors(host, cdp_out, ver_out)
                        last_err = None
                        break
                    except NetmikoAuthenticationException:
                        logger.info("[%s] Authentication failed", host)
                        last_err = "AuthenticationError"
                        break
                    except (NetmikoTimeoutException, SSHException, socket.timeout) as e:
                        logger.warning("[%s] Connection issue: %s", host, e)
                        last_err = type(e).__name__
                    except Exception:
                        logger.exception("[%s] Unexpected error", host)
                        last_err = "UnexpectedError"

                with self.visited_lock:
                    self.visited.add(host)
                if last_err:
                    with self.data_lock:
                        self.connection_errors.setdefault(host, last_err)

                self.host_queue.task_done()
        except Exception:
            logger.exception("Worker crashed: %s", tname)

            self.host_queue.task_done()

    def resolve_dns_for_host(self, hname: str) -> Tuple[str, str]:
        """Resolve a single hostname to IPv4 address (best-effort)."""
        try:
            logger.debug("[DNS] Resolving %s", hname)
            ip = socket.gethostbyname(hname)
            return hname, ip
        except socket.gaierror:
            return hname, "DNS Resolution Failed"
        except Exception as e:
            logger.exception("Unexpected DNS error for %s", hname)
            return hname, f"Error: {e}"

    def resolve_dns_parallel(self) -> None:
        """Resolve all collected hostnames using a thread pool."""
        names = list(self.hostnames)
        results: List[Tuple[str, str]] = []
        if not names:
            return
        # Keep the DNS pool modest; it's CPU/I/O light
        with ThreadPoolExecutor(max_workers=min(32, max(4, self.limit))) as ex:
            futs = [ex.submit(self.resolve_dns_for_host, n) for n in names]
            for f in as_completed(futs):
                try:
                    results.append(f.result())
                except Exception:
                    logger.exception("DNS worker failed while resolving names")
        with self.data_lock:
            for h, ip in results:
                self.dns_ip[h] = ip


def main() -> None:
    """
    Entrypoint:
    - Validate presence of templates and Excel workbook.
    - Collect interactive inputs (site name, seeds, credentials).
    - Optionally collect jump server from env or prompt.
    - Seed the queue and run threaded discovery.
    - Resolve DNS and emit Excel report.
    - Print a brief summary.
    """
   
    # Use minimal config (env overrides allowed)
    limit = DEFAULT_LIMIT
    timeout = DEFAULT_TIMEOUT
    cdp_template = CDP_TEMPLATE
    ver_template = VER_TEMPLATE
    excel_template = EXCEL_TEMPLATE

    # Validate template and excel files early (fail fast)
    missing = []
    for p in (cdp_template, ver_template, excel_template):
        if not p.exists():
            missing.append(str(p))
    if missing:
        logger.error("Required files missing: %s", ", ".join(missing))
        raise SystemExit(1)

    creds = CredentialManager()
    discoverer = NetworkDiscoverer(timeout=timeout, limit=limit, cdp_template=cdp_template, ver_template=ver_template)
    reporter = ExcelReporter(excel_template)

    # Interactive input
    site_name, seeds, primary_user, primary_pass, answer_user, answer_pass = creds.prompt_for_inputs()

    # If jump server provided via env use it, otherwise prompt
    jump_server = os.getenv("CDP_JUMP_SERVER", "").strip()
    if not jump_server:
        jump_server = input(
            f"\nEnter jump server IP/hostname to use for SSH proxy (or leave blank to use device directly) \n"
            f"GBMKD1V-APPAD03: 10.112.250.6\n"
            f"GBMKD1V-APPAD03: 10.80.250.5\n"
            f"Enter IP Address:"
            ).strip()
    if not jump_server:
        logger.info("No jump server provided; you may need direct access to targets from this host.")

    # Validate seeds: accept IPs or resolvable hostnames; normalize to IPs
    validated_seeds: List[str] = []
    for s in seeds:
        try:
            ipaddress.ip_address(s)
            validated_seeds.append(s)
        except ValueError:
            try:
                resolved = socket.gethostbyname(s)
                validated_seeds.append(resolved)
            except Exception:
                logger.error("Seed '%s' is not a valid IP and could not be resolved. Aborting.", s)
                raise SystemExit(1)

    # Queue seeds (deduplicate via 'enqueued' so workers actually process them)
    for s in set(validated_seeds):
        with discoverer.visited_lock:
            if s in discoverer.visited or s in discoverer.enqueued:
                continue
            discoverer.enqueued.add(s)
            discoverer.host_queue.put(s)

    # Discovery (threaded worker pool)
    with ThreadPoolExecutor(max_workers=limit) as executor:
        futures = [
            executor.submit(
                discoverer.discover_worker,
                jump_server,
                primary_user,
                primary_pass,
                answer_user,
                answer_pass,
            )
            for _ in range(limit)
        ]

        # Wait until all tasks that were put() are processed
        discoverer.host_queue.join()

        # Now tell workers to exit
        for _ in range(limit):
            discoverer.host_queue.put(None)

        # Ensure sentinels are consumed
        discoverer.host_queue.join()

        # Wait for all worker threads to finish
        for f in futures:
            f.result()

    # DNS resolution (post processing)
    discoverer.resolve_dns_parallel()

    # Excel output
    reporter.save_to_excel(
        discoverer.cdp_neighbour_details,
        validated_seeds,
        site_name,
        discoverer.dns_ip,
        discoverer.authentication_errors,
        discoverer.connection_errors,
    )

    # Summary
    logger.info("Done!")
    logger.info(" Discovered devices: %d", len(discoverer.visited))
    logger.info(" CDP entries: %d", len(discoverer.cdp_neighbour_details))
    logger.info(" Auth errors: %d", len(discoverer.authentication_errors))
    logger.info(" Conn errors: %d", len(discoverer.connection_errors))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting gracefully…")
        raise SystemExit(130)