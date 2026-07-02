"""Constants for the SpoolTap V2 integration."""

from __future__ import annotations

DOMAIN = "spooltap"

# config-entry keys
CONF_HOST = "host"
CONF_API_TOKEN = "api_token"

DEFAULT_HOST = "http://bambuddy.local:8000"  # placeholder — user enters their BB URL
DEFAULT_INVENTORY_INTERVAL = 30  # seconds

# services
SERVICE_RESOLVE_TAG = "resolve_tag"
SERVICE_BIND_TAG = "bind_tag"
SERVICE_RECYCLE_TAG = "recycle_tag"
SERVICE_CREATE_SPOOL = "create_spool"
SERVICE_ASSIGN_SLOT = "assign_slot"
SERVICE_UNASSIGN_SLOT = "unassign_slot"
SERVICE_MODIFY_SPOOL = "modify_spool"
SERVICE_WEIGH_SPOOL = "weigh_spool"
SERVICE_ARCHIVE_SPOOL = "archive_spool"
SERVICE_REFRESH = "refresh"
SERVICE_BIND_SLOT_TAG = "bind_slot_tag"
SERVICE_CLEAR_SLOT_TAG = "clear_slot_tag"
SERVICE_INSTALL_DASHBOARD = "install_dashboard"

# repair issue id (Spoolman must be OFF for the native tag path)
ISSUE_SPOOLMAN_ON = "spoolman_enabled"
