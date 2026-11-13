"""IMAP email parser for tracking number extraction."""

from __future__ import annotations

import logging
import socket
import ssl
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .carrier_detect import detect_carrier_from_email, detect_carrier_from_number
from .const import (
    DEFAULT_IMAP_FOLDER,
    DEFAULT_MAX_EMAILS,
    DEFAULT_PORTS,
    DEFAULT_SEARCH_DAYS,
    DEFAULT_SECURITY,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    MB_DEDICATED,
    MB_FOLDERS,
    MB_MARK_SEEN,
    MB_MAX_EMAILS,
    MB_NAME,
    MB_PASSWORD,
    MB_PORT,
    MB_SEARCH_DAYS,
    MB_SECURITY,
    MB_SERVER,
    MB_TIMEOUT,
    MB_UNSEEN_ONLY,
    MB_USERNAME,
    MB_VERIFY_SSL,
    SECURITY_NONE,
    SECURITY_STARTTLS,
    TRACKING_NUMBER_PATTERNS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ExtractedPackage:
    """A tracking number extracted from an email."""

    tracking_number: str
    carrier: str
    friendly_name: str
    source_email: str


@dataclass
class ImapCheckResult:
    """Outcome of an IMAP connection test."""

    error: str | None = None  # translation key of the error, None if OK
    detail: str = ""
    folders: list[str] = field(default_factory=list)


def _ssl_context(verify: bool) -> ssl.SSLContext:
    """Build the TLS context. Must run in executor (loads CA certificates)."""
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _open_mailbox(
    server: str,
    port: int | None,
    security: str = DEFAULT_SECURITY,
    verify_ssl: bool = DEFAULT_VERIFY_SSL,
    timeout: int | None = DEFAULT_TIMEOUT,
):
    """Return a connected (not logged in) imap_tools mailbox.

    imap_tools has no ``starttls`` argument on ``MailBox``: each security mode
    is a dedicated class. The STARTTLS one is named ``MailBoxTls`` up to 1.7
    and ``MailBoxStartTls`` in newer releases.
    """
    import imap_tools

    port = port or DEFAULT_PORTS.get(security, 993)
    timeout = timeout or None

    if security == SECURITY_NONE:
        return imap_tools.MailBoxUnencrypted(server, port, timeout=timeout)

    context = _ssl_context(verify_ssl)
    if security == SECURITY_STARTTLS:
        tls_cls = getattr(imap_tools, "MailBoxStartTls", None) or getattr(
            imap_tools, "MailBoxTls"
        )
        return tls_cls(server, port, timeout=timeout, ssl_context=context)
    return imap_tools.MailBox(server, port, timeout=timeout, ssl_context=context)


def _classify_error(err: Exception) -> str:
    """Map a connection/login exception to a translation key."""
    from imap_tools.errors import MailboxLoginError

    if isinstance(err, MailboxLoginError):
        return "imap_auth_failed"
    if isinstance(err, ssl.SSLCertVerificationError):
        return "imap_certificate"
    if isinstance(err, ssl.SSLError):
        return "imap_ssl_error"
    if isinstance(err, socket.gaierror):
        return "imap_unknown_host"
    if isinstance(err, (TimeoutError, socket.timeout)):
        return "imap_timeout"
    if isinstance(err, ConnectionRefusedError):
        return "imap_refused"
    if isinstance(err, OSError):
        return "imap_cannot_connect"
    # imaplib.IMAP4.error: protocol issue (e.g. STARTTLS not supported, LOGINDISABLED)
    return "imap_connection_failed"


def _list_folders(mailbox) -> list[str]:
    """Return selectable folder names, INBOX first."""
    folders: list[str] = []
    for info in mailbox.folder.list():
        flags = {f.lower() for f in info.flags}
        if "\\noselect" in flags or "\\nonexistent" in flags:
            continue
        folders.append(info.name)
    return sorted(folders, key=lambda n: (n.upper() != "INBOX", n.lower()))


def check_imap_connection(
    server: str,
    port: int | None,
    user: str,
    password: str,
    security: str = DEFAULT_SECURITY,
    verify_ssl: bool = DEFAULT_VERIFY_SSL,
    timeout: int | None = DEFAULT_TIMEOUT,
) -> ImapCheckResult:
    """Connect, log in and list folders. Runs in executor."""
    try:
        with _open_mailbox(server, port, security, verify_ssl, timeout) as mailbox:
            mailbox.login(user, password, initial_folder=None)
            try:
                folders = _list_folders(mailbox)
            except Exception as err:  # listing is a bonus, not a requirement
                _LOGGER.debug("Could not list IMAP folders on %s: %s", server, err)
                folders = []
            return ImapCheckResult(folders=folders)
    except Exception as err:
        key = _classify_error(err)
        _LOGGER.error(
            "IMAP connection test to %s:%s failed (%s): %s", server, port, key, err
        )
        return ImapCheckResult(error=key, detail=str(err) or type(err).__name__)


def run_imap_fetch(
    config: dict[str, Any], known_numbers: set[str]
) -> list[ExtractedPackage]:
    """Fetch emails of one mailbox and extract tracking numbers. Runs in executor.

    ``known_numbers`` is updated in place so several mailboxes can share it.
    Connection and login errors are raised to the caller.
    """
    from imap_tools import AND

    results: list[ExtractedPackage] = []
    dedicated: bool = config.get(MB_DEDICATED, False)
    mark_seen: bool = config.get(MB_MARK_SEEN, dedicated)
    days = max(int(config.get(MB_SEARCH_DAYS, DEFAULT_SEARCH_DAYS)), 0)
    limit = int(config.get(MB_MAX_EMAILS, DEFAULT_MAX_EMAILS)) or None
    folders = config.get(MB_FOLDERS) or [DEFAULT_IMAP_FOLDER]
    label = config.get(MB_NAME) or config[MB_SERVER]

    criteria: dict[str, Any] = {"date_gte": date.today() - timedelta(days=days)}
    if config.get(MB_UNSEEN_ONLY, False):
        criteria["seen"] = False

    with _open_mailbox(
        config[MB_SERVER],
        config.get(MB_PORT),
        config.get(MB_SECURITY, DEFAULT_SECURITY),
        config.get(MB_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        config.get(MB_TIMEOUT, DEFAULT_TIMEOUT),
    ) as mailbox:
        mailbox.login(config[MB_USERNAME], config[MB_PASSWORD], initial_folder=None)

        for folder in folders:
            try:
                mailbox.folder.set(folder)
            except Exception as err:
                _LOGGER.warning(
                    "Mailbox %s: cannot open folder %s: %s", label, folder, err
                )
                continue

            # mark_seen=False: never flag mails as read just because we fetched them
            for msg in mailbox.fetch(
                AND(**criteria), limit=limit, reverse=True, mark_seen=False
            ):
                found = _extract_from_message(msg, dedicated, known_numbers, results)
                if mark_seen and found:
                    mailbox.flag(msg.uid, [r"\Seen"], True)

    return results


def _extract_from_message(
    msg, dedicated: bool, known_numbers: set[str], results: list[ExtractedPackage]
) -> bool:
    """Extract tracking numbers from one message. Returns True if any found."""
    sender = msg.from_ or ""
    subject = msg.subject or ""

    carrier_from_email = detect_carrier_from_email(sender)
    # Personal mailbox: only process known carrier senders
    if not dedicated and carrier_from_email == "unknown":
        return False

    # Extract tracking numbers — limit to 50KB to avoid performance issues
    text = f"{subject}\n{msg.text or ''}\n{msg.html or ''}"[:50000]
    found: set[str] = set()
    for pattern in TRACKING_NUMBER_PATTERNS:
        for match in pattern.finditer(text):
            number = (
                match.group(1).upper() if match.lastindex else match.group(0).upper()
            )
            if len(number) >= 10:
                found.add(number)

    name = subject.strip()[:60]

    for number in found:
        if number not in known_numbers:
            carrier = carrier_from_email
            if carrier == "unknown":
                carrier = detect_carrier_from_number(number)
            results.append(
                ExtractedPackage(
                    tracking_number=number,
                    carrier=carrier,
                    friendly_name=name or carrier.capitalize(),
                    source_email=sender,
                )
            )
            known_numbers.add(number)

    return bool(found)
