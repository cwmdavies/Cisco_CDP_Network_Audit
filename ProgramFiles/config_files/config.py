"""
CDP Network Audit Configuration
================================

This module contains all configurable parameters for the CDP Network Audit tool.
Centralizing configuration here makes it easy to customize the tool's behavior
without modifying the core application logic.

Quick Start
-----------
To customize settings, edit the values in this file directly. For temporary overrides
or deployment-specific settings, use environment variables (see below).

Environment Variable Overrides
------------------------------
The following settings can be overridden using environment variables:

- CDP_LIMIT: Maximum concurrent worker threads (default: 10)
  Example: SET CDP_LIMIT=20
  
- CDP_TIMEOUT: SSH/connection timeout in seconds (default: 10)
  Example: SET CDP_TIMEOUT=15
  
- CDP_JUMP_SERVER: Jump/bastion server hostname or IP
  Example: SET CDP_JUMP_SERVER=10.1.1.1
  
- CDP_PRIMARY_CRED_TARGET: Windows Credential Manager target for primary credentials
  Example: SET CDP_PRIMARY_CRED_TARGET=MyCompany/NetworkAdmin
  
- CDP_ANSWER_CRED_TARGET: Windows Credential Manager target for answer credentials
  Example: SET CDP_ANSWER_CRED_TARGET=MyCompany/NetworkAnswer
  
- LOGGING_CONFIG: Path to logging configuration file
  Example: SET LOGGING_CONFIG=C:\path\to\logging.conf

Common Customizations
---------------------
1. Increase concurrency for faster scans: Increase DEFAULT_LIMIT (e.g., 20-30)
2. Handle slow networks: Increase DEFAULT_TIMEOUT (e.g., 20-30 seconds)
3. Change device type: Modify DEVICE_TYPE (e.g., "cisco_nxos", "cisco_xe")
4. Adjust retry behavior: Modify MAX_RETRY_ATTEMPTS (e.g., 5 for unreliable networks)
5. Customize Excel output: Modify sheet names, cell locations, or column names
"""

from pathlib import Path

# ===========================
# Network Connection Settings
# ===========================

# Default jump/bastion server (can be overridden via CDP_JUMP_SERVER env var)
# Set to empty string "" to disable jump host by default
# Users can still enable it interactively when prompted
JUMP_HOST = "10.112.250.6"

# Device type for Netmiko connections
# Common values: "cisco_ios", "cisco_nxos", "cisco_xe", "cisco_xr"
# See Netmiko documentation for full list of supported device types
DEVICE_TYPE = "cisco_ios"

# SSH port for device connections
# Standard SSH port is 22; change only if your devices use non-standard ports
SSH_PORT = 22

# Maximum number of concurrent worker threads for device discovery
# Higher values = faster discovery but more system resources and network load
# Recommended range: 5-30 depending on network size and system capabilities
DEFAULT_LIMIT = 10

# Timeout in seconds for SSH operations (connection, authentication, reads)
# Increase for slow networks or devices with high latency
# Recommended range: 10-30 seconds
DEFAULT_TIMEOUT = 10

# Maximum number of connection retry attempts per device
# Each device is retried this many times before being marked as a connection error
# Recommended range: 2-5 (higher for unreliable networks)
MAX_RETRY_ATTEMPTS = 3

# Maximum worker threads for DNS resolution pool
# DNS resolution runs after discovery completes
# Formula: min(DNS_MAX_WORKERS, max(DNS_MIN_WORKERS, worker_limit))
# These limits prevent excessive resource usage while maintaining performance
DNS_MAX_WORKERS = 32
DNS_MIN_WORKERS = 4


# ===========================
# Credential Settings
# ===========================

# Windows Credential Manager target for primary credentials
# Can be overridden via CDP_PRIMARY_CRED_TARGET environment variable
# Change this to match your organization's credential naming scheme
# Example: "MyCompany/NetworkAdmin"
CRED_TARGET = "MyApp/ADM"

# Windows Credential Manager target for fallback 'answer' credentials
# Can be overridden via CDP_ANSWER_CRED_TARGET environment variable
# The 'answer' account is used as a fallback when primary authentication fails
# Example: "MyCompany/NetworkAnswer"
ALT_CREDS = "MyApp/Answer"


# ===========================
# File Paths
# ===========================
# NOTE: These paths are case-sensitive on Linux/macOS. Keep them consistent.

# Base directory for the application (relative to main.py)
# Usually you don't need to change this unless restructuring the project
BASE_DIR = Path(".")

# TextFSM template for parsing 'show cdp neighbors detail' output
# This template extracts neighbor information from CDP output
# Modify only if you need custom parsing logic
CDP_TEMPLATE = BASE_DIR / "ProgramFiles" / "textfsm" / "cisco_ios_show_cdp_neighbors_detail.textfsm"

# TextFSM template for parsing 'show version' output
# This template extracts device version, serial number, and uptime
# Modify only if you need custom parsing logic
VER_TEMPLATE = BASE_DIR / "ProgramFiles" / "textfsm" / "cisco_ios_show_version.textfsm"

# Excel template file path
# This is the pre-formatted workbook that the tool uses as a base for reports
# Contains sheets: Audit, DNS Resolved, Authentication Errors, Connection Errors
EXCEL_TEMPLATE = BASE_DIR / "ProgramFiles" / "config_files" / "1 - CDP Network Audit _ Template.xlsx"

# Logging configuration file path (searched if LOGGING_CONFIG env var not set)
# This is an INI-style logging configuration file
# If not found, the tool falls back to basic console logging
LOGGING_CONFIG_PATH = BASE_DIR / "ProgramFiles" / "Config_Files" / "logging.conf"


# ===========================
# Excel Report Settings
# ===========================
# These settings control the structure and format of the Excel output report.
# Modify these if you've customized the Excel template or need different formatting.

# Excel sheet names (must match sheet names in the Excel template)
EXCEL_SHEET_AUDIT = "Audit"
EXCEL_SHEET_DNS = "DNS Resolved"
EXCEL_SHEET_AUTH_ERRORS = "Authentication Errors"
EXCEL_SHEET_CONN_ERRORS = "Connection Errors"

# Metadata cell locations in Audit sheet (cell addresses in Excel A1 notation)
# These cells are populated with site name, date, time, and seed device information
EXCEL_CELL_SITE_NAME = "B4"
EXCEL_CELL_DATE = "B5"
EXCEL_CELL_TIME = "B6"
EXCEL_CELL_PRIMARY_SEED = "B7"
EXCEL_CELL_SECONDARY_SEED = "B8"

# Data start rows (0-indexed for pandas; subtract 1 from Excel row number)
# EXCEL_AUDIT_DATA_START_ROW = 11 means data starts at Excel row 12
EXCEL_AUDIT_DATA_START_ROW = 11
EXCEL_OTHER_DATA_START_ROW = 4

# Default text for missing secondary seed
# Displayed in the report when only one seed device is provided
EXCEL_SECONDARY_SEED_DEFAULT = "Secondary Seed device not given"

# Column names for CDP audit data
# These columns define the structure of the main audit data
# Order matters: it determines the column order in the Excel output
EXCEL_AUDIT_COLUMNS = [
    "LOCAL_HOST",         # Hostname of the local/source device
    "LOCAL_IP",           # Management IP of the local device
    "LOCAL_PORT",         # Local interface connected to neighbor
    "LOCAL_SERIAL",       # Serial number of the local device
    "LOCAL_UPTIME",       # Uptime of the local device
    "DESTINATION_HOST",   # Hostname of the discovered neighbor
    "REMOTE_PORT",        # Remote interface on the neighbor
    "MANAGEMENT_IP",      # Management IP of the neighbor
    "PLATFORM"            # Platform/model of the neighbor device
]

# Column names for DNS resolution data
EXCEL_DNS_COLUMNS = ["Hostname", "IP Address"]

# Column names for authentication errors
EXCEL_AUTH_ERROR_COLUMNS = ["Authentication Errors"]

# Column names for connection errors
EXCEL_CONN_ERROR_COLUMNS = ["IP Address", "Error"]


# ===========================
# DNS Resolution Settings
# ===========================

# DNS error markers for Excel output
# These strings are used when DNS resolution fails for a hostname
# UNRESOLVED: DNS lookup failed (name not found, temporary failure, etc.)
# ERROR: Unexpected error occurred during DNS resolution
DNS_UNRESOLVED_MARKER = "UNRESOLVED"
DNS_ERROR_MARKER = "ERROR"