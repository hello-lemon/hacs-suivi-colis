"""Constants for Suivi de Colis."""

import re

DOMAIN = "suivi_colis"
STORAGE_KEY = "suivi_colis_packages"
STORAGE_VERSION = 1

# Config keys
CONF_API_KEY = "api_key"
CONF_ARCHIVE_AFTER_DAYS = "archive_after_days"
CONF_EMAIL_INTERVAL = "email_interval"
CONF_MAILBOXES = "mailboxes"
CONF_WEBHOOK_ID = "webhook_id"  # entry.data, secret part of the webhook URL
CONF_WEBHOOK_ENABLED = "webhook_enabled"  # entry.options

# Fired on the bus each time a package changes status
EVENT_STATUS_CHANGED = "suivi_colis_status_changed"

# Legacy single-mailbox keys (config entry v1, migrated to CONF_MAILBOXES)
CONF_IMAP_SERVER = "imap_server"
CONF_IMAP_PORT = "imap_port"
CONF_IMAP_USER = "imap_user"
CONF_IMAP_PASSWORD = "imap_password"
CONF_IMAP_FOLDER = "imap_folder"
CONF_IMAP_SSL = "imap_ssl"
CONF_IMAP_DEDICATED = "imap_dedicated"

# Mailbox keys (one dict per mailbox in options[CONF_MAILBOXES])
MB_ID = "id"
MB_NAME = "name"
MB_ENABLED = "enabled"
MB_PROVIDER = "provider"
MB_SERVER = "server"
MB_PORT = "port"
MB_SECURITY = "security"
MB_VERIFY_SSL = "verify_ssl"
MB_TIMEOUT = "timeout"
MB_USERNAME = "username"
MB_PASSWORD = "password"
MB_FOLDERS = "folders"
MB_DEDICATED = "dedicated"
MB_UNSEEN_ONLY = "unseen_only"
MB_MARK_SEEN = "mark_seen"
MB_SEARCH_DAYS = "search_days"
MB_MAX_EMAILS = "max_emails"

# Connection security modes
SECURITY_SSL = "ssl"  # Implicit TLS (IMAPS), usually port 993
SECURITY_STARTTLS = "starttls"  # Plain connection upgraded with STARTTLS, usually 143
SECURITY_NONE = "none"  # Unencrypted, usually 143
SECURITY_MODES = [SECURITY_SSL, SECURITY_STARTTLS, SECURITY_NONE]
DEFAULT_PORTS = {SECURITY_SSL: 993, SECURITY_STARTTLS: 143, SECURITY_NONE: 143}

# Provider presets: server, port, security
PROVIDER_CUSTOM = "custom"
IMAP_PROVIDERS: dict[str, tuple[str, int, str]] = {
    "gmail": ("imap.gmail.com", 993, SECURITY_SSL),
    "outlook": ("outlook.office365.com", 993, SECURITY_SSL),
    "yahoo": ("imap.mail.yahoo.com", 993, SECURITY_SSL),
    "icloud": ("imap.mail.me.com", 993, SECURITY_SSL),
    "orange": ("imap.orange.fr", 993, SECURITY_SSL),
    "free": ("imap.free.fr", 993, SECURITY_SSL),
    "sfr": ("imap.sfr.fr", 993, SECURITY_SSL),
    "laposte": ("imap.laposte.net", 993, SECURITY_SSL),
    "gmx": ("imap.gmx.com", 993, SECURITY_SSL),
    "ionos": ("imap.ionos.fr", 993, SECURITY_SSL),
    "ovh": ("ssl0.ovh.net", 993, SECURITY_SSL),
    "proton_bridge": ("127.0.0.1", 1143, SECURITY_STARTTLS),
}

# Defaults
DEFAULT_IMAP_PORT = 993
DEFAULT_IMAP_FOLDER = "INBOX"
DEFAULT_IMAP_SSL = True
DEFAULT_SECURITY = SECURITY_SSL
DEFAULT_VERIFY_SSL = True
DEFAULT_TIMEOUT = 30  # seconds
DEFAULT_SEARCH_DAYS = 1
DEFAULT_MAX_EMAILS = 50
DEFAULT_ARCHIVE_AFTER_DAYS = 2
DEFAULT_EMAIL_INTERVAL = 15  # minutes
DEFAULT_UPDATE_INTERVAL = 30  # minutes

# 17track API
API_17TRACK_BASE = "https://api.17track.net/track/v2.4"
API_17TRACK_REGISTER = f"{API_17TRACK_BASE}/register"
API_17TRACK_GETTRACKINFO = f"{API_17TRACK_BASE}/gettrackinfo"
API_17TRACK_STOPTRACK = f"{API_17TRACK_BASE}/stoptrack"
API_17TRACK_CHANGECARRIER = f"{API_17TRACK_BASE}/changecarrier"
API_17TRACK_QUOTA_ERRORS = (-18019907, -18019908)  # daily limit / quota exhausted
API_17TRACK_GETQUOTA = f"{API_17TRACK_BASE}/getquota"
API_17TRACK_RATE_LIMIT = 3  # req/sec

# Languages 17track can translate tracking events into (v2.4 "lang")
API_17TRACK_LANGS = {
    "en", "ja", "fr", "da", "th", "de", "es", "zh-hans", "zh-hant", "ar", "bg",
    "cs", "el", "et", "fi", "he", "hu", "it", "ko", "lv", "nl", "no", "pl", "pt",
    "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi",
}

# Carrier regex patterns (for manual add detection) — pre-compiled
CARRIER_REGEX = {
    "chronopost": [
        re.compile(r"^[A-Z]{2}\d{9}FR$"),      # XX123456789FR
        re.compile(r"^\d{13}$"),                 # 13 digits
    ],
    "colissimo": [
        re.compile(r"^6[A-Z]\d{11}$"),          # 6X12345678901
        re.compile(r"^[0-9]{15}$"),             # 15 digits (Colissimo international)
    ],
    "ups": [
        re.compile(r"^1Z[A-Z0-9]{16}$"),        # 1Z + 16 chars
    ],
    "amazon": [
        re.compile(r"^TBA\d{12,}$"),            # TBA + digits
    ],
    "cainiao": [
        re.compile(r"^L[RPT][A-Z0-9]{7,9}[A-Z]{2}$"),  # LR/LP/LT...XX
        re.compile(r"^YANWEN\d+$"),
    ],
    "dhl": [
        re.compile(r"^\d{10,11}$"),             # 10-11 digits (also matches others)
        re.compile(r"^JJD\d{18}$"),             # JJD + 18 digits
        re.compile(r"^\d{3}-\d{8}$"),           # DHL Express
    ],
    "laposte": [
        re.compile(r"^[A-Z]{2}\d{9}FR$"),       # Same as Chronopost (resolved by context)
    ],
}

# Email sender to carrier mapping
EMAIL_CARRIER_MAP = {
    "noreply@chronopost.fr": "chronopost",
    "notification@chronopost.fr": "chronopost",
    "noreply@laposte.fr": "colissimo",
    "noreply@notif.laposte.fr": "colissimo",
    "noreply@dhl.com": "dhl",
    "noreply@ups.com": "ups",
    "pkginfo@ups.com": "ups",
    "shipment-tracking@amazon.fr": "amazon",
    "no-reply@amazon.fr": "amazon",
    "shipment-tracking@amazon.com": "amazon",
    "noreply@aliexpress.com": "cainiao",
    "transaction@notice.aliexpress.com": "cainiao",
}

# Email domain to carrier (fallback)
EMAIL_DOMAIN_CARRIER_MAP = {
    "chronopost.fr": "chronopost",
    "laposte.fr": "colissimo",
    "dhl.com": "dhl",
    "ups.com": "ups",
    "amazon.fr": "amazon",
    "amazon.com": "amazon",
    "aliexpress.com": "cainiao",
}

# Tracking number regex for extraction from emails — pre-compiled
TRACKING_NUMBER_PATTERNS = [
    re.compile(r"(?:suivi|tracking|n[°o]?\s*(?:de\s+)?colis|shipment)\s*[:=]?\s*([A-Z0-9]{10,30})", re.IGNORECASE),
    re.compile(r"(?:track|suivre)[^\n]*?([A-Z0-9]{10,30})", re.IGNORECASE),
    re.compile(r"\b(1Z[A-Z0-9]{16})\b", re.IGNORECASE),           # UPS
    re.compile(r"\b(TBA\d{12,})\b", re.IGNORECASE),               # Amazon
    re.compile(r"\b([A-Z]{2}\d{9}FR)\b", re.IGNORECASE),          # Chronopost/Colissimo
    re.compile(r"\b(6[A-Z]\d{11})\b", re.IGNORECASE),             # Colissimo
    re.compile(r"\b(L[RPT][A-Z0-9]{7,9}[A-Z]{2})\b", re.IGNORECASE),  # Cainiao
    re.compile(r"\b(JJD\d{18})\b", re.IGNORECASE),                # DHL
]

# 17track carrier codes, checked against
# https://res.17track.net/asset/carrier/info/apicarrier.all.json
# Only unambiguous carriers are forced on /register. DHL (Express, Paket,
# eCommerce…) and Amazon (Shipping FR/US, Swiship…) have several codes: 17track
# auto-detection picks the right one, a wrong code would track the wrong carrier.
CARRIER_17TRACK_CODE = {
    "chronopost": 100273,  # Chronopost (FR)
    "colissimo": 6051,  # La Poste (Colissimo)
    "laposte": 6051,
    "ups": 100002,
    "cainiao": 190271,
    "colisprive": 100027,  # Colis Privé
    "dpd": 100072,  # DPD (FR)
    "gls": 101272,  # GLS (FR)
    "mondialrelay": 100304,
    "relaiscolis": 100461,
    "tnt": 100241,  # TNT (FR)
    "fedex": 100003,
    "vintedgo": 101020,
}

# Choices of the change_carrier service: option -> (17track code, carrier name)
# Codes from https://res.17track.net/asset/carrier/info/apicarrier.all.json
CHANGE_CARRIER_OPTIONS: dict[str, tuple[int, str]] = {
    "colissimo": (6051, "colissimo"),
    "chronopost": (100273, "chronopost"),
    "colisprive": (100027, "colisprive"),
    "mondialrelay": (100304, "mondialrelay"),
    "relaiscolis": (100461, "relaiscolis"),
    "dpd": (100072, "dpd"),
    "gls": (101272, "gls"),
    "ups": (100002, "ups"),
    "fedex": (100003, "fedex"),
    "tnt": (100241, "tnt"),
    "dhl_paket": (7041, "dhl"),  # DHL Paket / Deutsche Post parcels
    "dhl_express": (100001, "dhl"),
    "amazon": (101001, "amazon"),  # Amazon Shipping (FR)
    "vintedgo": (101020, "vintedgo"),
    "cainiao": (190271, "cainiao"),
}

# Status icon mapping
STATUS_ICONS = {
    "unknown": "mdi:package-variant",
    "info_received": "mdi:package-variant-closed",
    "in_transit": "mdi:truck-delivery",
    "out_for_delivery": "mdi:truck-fast",
    "available_for_pickup": "mdi:store-marker",
    "delivered": "mdi:package-variant-closed-check",
    "delivery_failure": "mdi:package-variant-remove",
    "exception": "mdi:alert-circle",
    "expired": "mdi:clock-alert",
    "not_found": "mdi:help-circle",
}
