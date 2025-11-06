**CDP Network Audit Tool**  
Automated discovery and documentation of Cisco network devices using CDP (Cisco Discovery Protocol).  
Connects to seed devices (optionally via a jump server), collects neighbor and version info, and generates a comprehensive Excel report.  

**Features**  
Multi-threaded network discovery using CDP, with optional jump server SSH proxying.  
Interactive credential management, including Windows Credential Manager integration.  
Robust error handling for authentication and connection issues.  
Automated DNS resolution for discovered hostnames.  
Structured Excel report output, including:  

**CDP neighbor details**  
Device inventory  
DNS resolution results  
Authentication and connection errors  

**Usage**
Run the script and follow prompts for site name, seed device(s), credentials, and (optionally) a jump server.  
Environment variables can override defaults for thread/concurrency limits, timeouts, and credential targets.  
Requires supporting TextFSM templates and an Excel template in the expected locations.  

**Requirements**
Python 3.7+  
pandas, openpyxl, textfsm, paramiko, netmiko  
(Optional, Windows only) pywin32 for Credential Manager integration  

**Author:** Christopher Davies  
**Date:** 06/11/2025  
