from __future__ import annotations

import asyncio
import getpass
import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Preferences
from .utils import ROOT

QR_LOGIN_WINDOW_SECONDS = 60.0
QR_LOGIN_MAX_REFRESHES = 3


class TelegramError(RuntimeError):
    """Base error whose message is safe to display and persist."""


class TelegramCredentialsError(TelegramError):
    pass


class TelegramAuthorizationError(TelegramError):
    pass


class TelegramConnectionError(TelegramError):
    pass


class TelegramRateLimitError(TelegramError):
    def __init__(self, wait_seconds: int) -> None:
        self.wait_seconds = max(1, int(wait_seconds))
        super().__init__(f"Telegram rate limit requires waiting {self.wait_seconds} seconds; run stopped safely")


@dataclass(frozen=True)
class TelegramCredentials:
    api_id: int = field(repr=False)
    api_hash: str = field(repr=False)
    phone: str = field(repr=False)
    session_path: Path


@dataclass(frozen=True)
class TelegramMessage:
    channel_username: str
    channel_id: int
    message_id: int
    message_url: str | None
    text: str
    published_at: datetime
    edited_at: datetime | None = None
    forwarded_from: str | None = None
    entity_urls: tuple[str, ...] = ()


def load_telegram_credentials(preferences: Preferences) -> TelegramCredentials:
    config = preferences.sources.telegram
    values = {name: os.getenv(name, "").strip() for name in config.requires_env}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise TelegramCredentialsError(
            "Telegram credentials are not configured; set the required variables locally before a live command"
        )
    raw_api_id = values.get("TELEGRAM_API_ID", "")
    try:
        api_id = int(raw_api_id)
    except ValueError as exc:
        raise TelegramCredentialsError("TELEGRAM_API_ID must be an integer") from exc
    if api_id <= 0:
        raise TelegramCredentialsError("TELEGRAM_API_ID must be a positive integer")
    configured_path = os.getenv(config.session_path_env, "").strip() or config.default_session_path
    session_path = Path(configured_path).expanduser()
    if not session_path.is_absolute():
        session_path = ROOT / session_path
    return TelegramCredentials(
        api_id=api_id,
        api_hash=values["TELEGRAM_API_HASH"],
        phone=values["TELEGRAM_PHONE"],
        session_path=session_path,
    )


class TelethonReader:
    """Small read-only Telethon boundary used by ingestion and replaced by fakes in tests."""

    def __init__(self, credentials: TelegramCredentials, max_flood_wait_seconds: int) -> None:
        self.credentials = credentials
        self.max_flood_wait_seconds = max_flood_wait_seconds
        self._client: Any = None

    async def __aenter__(self) -> TelethonReader:
        TelegramClient, _ = _telethon_imports()
        _prepare_session_directory(self.credentials.session_path)
        self._client = _create_telethon_client(
            TelegramClient,
            self.credentials,
            receive_updates=False,
            max_flood_wait_seconds=self.max_flood_wait_seconds,
        )
        try:
            await self._client.connect()
            if not await self._client.is_user_authorized():
                raise TelegramAuthorizationError("Telegram session is not authorized; run telegram-login first")
            return self
        except TelegramError:
            await self._disconnect_safely()
            raise
        except Exception as exc:
            wait_seconds = _flood_wait_seconds(exc)
            await self._disconnect_safely()
            if wait_seconds is not None:
                raise TelegramRateLimitError(wait_seconds) from None
            raise TelegramError("Telegram connection or authorization check failed") from None

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self._disconnect_safely()

    async def resolve_joined_sources(self, allowlist: list[str]) -> dict[str, Any]:
        """Resolve only allowlisted usernames found in the account's existing dialogs."""
        client = self._require_client()
        wanted = {source.casefold(): source for source in allowlist}
        attempts = 0
        while True:
            resolved: dict[str, Any] = {}
            try:
                async for dialog in client.iter_dialogs():
                    entity = dialog.entity
                    for username in _entity_usernames(entity):
                        configured = wanted.get(username.casefold())
                        if configured is not None:
                            resolved[configured] = entity
                    if len(resolved) == len(wanted):
                        break
                return resolved
            except Exception as exc:
                wait_seconds = _flood_wait_seconds(exc)
                if wait_seconds is None:
                    raise TelegramError("Telegram dialog request failed") from None
                if wait_seconds > self.max_flood_wait_seconds or attempts >= 1:
                    raise TelegramRateLimitError(wait_seconds) from None
                attempts += 1
                await asyncio.sleep(wait_seconds)

    async def read_messages(
        self,
        source: str,
        entity: Any,
        since: datetime,
        limit: int | None = None,
    ) -> list[TelegramMessage]:
        client = self._require_client()
        attempts = 0
        while True:
            try:
                records: list[TelegramMessage] = []
                async for message in client.iter_messages(entity, limit=None):
                    published_at = _aware_utc(getattr(message, "date", None))
                    if published_at is None:
                        continue
                    if published_at < since:
                        break
                    records.append(_serialize_message(source, entity, message, published_at))
                    if limit is not None and len(records) >= limit:
                        break
                return records
            except Exception as exc:
                wait_seconds = _flood_wait_seconds(exc)
                if wait_seconds is None:
                    raise TelegramError("Telegram message history request failed") from None
                if wait_seconds > self.max_flood_wait_seconds or attempts >= 1:
                    raise TelegramRateLimitError(wait_seconds) from None
                attempts += 1
                await asyncio.sleep(wait_seconds)

    def _require_client(self) -> Any:
        if self._client is None:
            raise TelegramError("Telegram client is not connected")
        return self._client

    async def _disconnect_safely(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        _secure_session_files(self.credentials.session_path)


async def telegram_login(preferences: Preferences, *, qr: bool = False) -> str:
    """Authorize a local user session with code or local QR login."""
    credentials = load_telegram_credentials(preferences)
    TelegramClient, errors = _telethon_imports()
    _prepare_session_directory(credentials.session_path)
    client = _create_telethon_client(
        TelegramClient,
        credentials,
        # Telethon's QRLogin.wait() completes from an UpdateLoginToken event.
        # Ordinary code login does not need the background update receiver.
        receive_updates=qr,
        max_flood_wait_seconds=preferences.sources.telegram.max_flood_wait_seconds,
    )
    authorization_completed = False
    try:
        try:
            await client.connect()
        except Exception as exc:
            _raise_login_transport_error(exc, "connection")
        try:
            authorized = await client.is_user_authorized()
        except Exception as exc:
            _raise_login_transport_error(exc, "authorization check")
        if authorized:
            authorization_completed = True
            return "already_authorized"
        if qr:
            await _telegram_qr_login(client, errors)
        else:
            await _telegram_code_login(client, errors, credentials.phone)
        try:
            authorized = await client.is_user_authorized()
        except Exception as exc:
            _raise_login_transport_error(exc, "authorization check")
        if not authorized:
            raise TelegramAuthorizationError("Telegram authorization did not complete")
        authorization_completed = True
        return "authorized"
    except TelegramError:
        raise
    except Exception as exc:
        wait_seconds = _flood_wait_seconds(exc)
        if wait_seconds is not None:
            raise TelegramRateLimitError(wait_seconds) from None
        raise TelegramAuthorizationError("Telegram authorization failed") from None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        _secure_session_files(credentials.session_path, strict=authorization_completed)


async def _telegram_code_login(client: Any, errors: Any, phone: str) -> None:
    try:
        sent = await client.send_code_request(phone)
    except Exception as exc:
        _raise_login_transport_error(exc, "code request")
    print(_format_code_delivery(sent), flush=True)
    verification_code = getpass.getpass("Telegram verification code (input hidden): ").strip()
    if not verification_code:
        raise TelegramAuthorizationError("Telegram verification code was not provided")
    try:
        await client.sign_in(
            phone=phone,
            code=verification_code,
            phone_code_hash=getattr(sent, "phone_code_hash", None),
        )
    except errors.SessionPasswordNeededError:
        password = getpass.getpass("Telegram 2FA password (input hidden): ")
        if not password:
            raise TelegramAuthorizationError("Telegram 2FA password was not provided") from None
        try:
            await client.sign_in(password=password)
        except Exception as exc:
            _raise_login_2fa_error(exc)
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as exc:
        raise TelegramAuthorizationError("Telegram verification code was invalid or expired") from exc
    except Exception as exc:
        _raise_login_transport_error(exc, "sign-in")


async def _telegram_qr_login(client: Any, errors: Any) -> None:
    try:
        qr_login = await client.qr_login()
    except Exception as exc:
        _raise_login_transport_error(exc, "QR login request")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QR_LOGIN_WINDOW_SECONDS
    refresh_count = 0
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            _raise_qr_expired()
        qr_text = _render_qr_terminal(qr_login.url)
        wait_timeout = _qr_wait_timeout(qr_login, remaining)
        try:
            # Register Telethon's login-token update handler before displaying
            # the scannable code so a very fast scan cannot race the waiter.
            await _wait_for_qr_scan(qr_login, wait_timeout, qr_text)
            return
        except asyncio.TimeoutError:
            if loop.time() >= deadline:
                _raise_qr_expired()
            if refresh_count >= QR_LOGIN_MAX_REFRESHES:
                _raise_qr_expired()
            refresh_count += 1
            await _refresh_qr(qr_login)
        except errors.SessionPasswordNeededError:
            password = getpass.getpass("Telegram 2FA password (input hidden): ")
            if not password:
                raise TelegramAuthorizationError("Telegram 2FA password was not provided") from None
            try:
                await client.sign_in(password=password)
            except Exception as exc:
                _raise_login_2fa_error(exc)
            return
        except TelegramError:
            raise
        except Exception as exc:
            if _is_qr_expiration_error(exc):
                if loop.time() >= deadline:
                    _raise_qr_expired()
                if refresh_count >= QR_LOGIN_MAX_REFRESHES:
                    _raise_qr_expired()
                refresh_count += 1
                await _refresh_qr(qr_login)
                continue
            _raise_login_transport_error(exc, "QR login")


async def _wait_for_qr_scan(qr_login: Any, timeout: float, qr_text: str) -> None:
    wait_task = asyncio.create_task(qr_login.wait(timeout=timeout))
    try:
        await asyncio.sleep(0)
        _display_qr(qr_text)
        await wait_task
    finally:
        if not wait_task.done():
            wait_task.cancel()
        # Retrieve a task exception when displaying the QR itself failed, and
        # finish cancellation cleanly without masking the original exception.
        with suppress(asyncio.CancelledError, Exception):
            await wait_task


def _display_qr(qr_text: str) -> None:
    print(
        "In Telegram on an already logged-in phone: Settings → Devices → Link Desktop Device → Scan QR Code.",
        flush=True,
    )
    print("Point that built-in Telegram scanner at this terminal QR code; do not use the ordinary camera.", flush=True)
    print(f"This QR login window is up to {max(1, int(QR_LOGIN_WINDOW_SECONDS))} seconds.", flush=True)
    print(qr_text, flush=True)


def _qr_wait_timeout(qr_login: Any, remaining_window: float) -> float:
    """Bound a wait by both our login window and Telegram's token expiry."""
    expires = _aware_utc(getattr(qr_login, "expires", None))
    if expires is None:
        return remaining_window
    token_remaining = (expires - datetime.now(UTC)).total_seconds()
    return max(0.01, min(remaining_window, token_remaining))


async def _refresh_qr(qr_login: Any) -> None:
    print("The QR code expired; generating a fresh local QR code.", flush=True)
    try:
        await qr_login.recreate()
    except Exception as exc:
        _raise_login_transport_error(exc, "QR refresh")


def _raise_qr_expired() -> None:
    raise TelegramAuthorizationError(
        "The QR code expired after the 60-second Telegram QR login window; run telegram-login --qr again"
    ) from None


def _raise_login_2fa_error(exc: Exception) -> None:
    wait_seconds = _flood_wait_seconds(exc)
    if wait_seconds is not None:
        raise TelegramRateLimitError(wait_seconds) from None
    if _is_connection_failure(exc):
        raise TelegramConnectionError("Telegram 2FA sign-in failed; check network connectivity") from None
    if "passwordhashinvalid" in exc.__class__.__name__.casefold():
        raise TelegramAuthorizationError("Telegram 2FA password was invalid") from None
    raise TelegramAuthorizationError("Telegram 2FA sign-in failed") from None


def _is_qr_expiration_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.casefold()
    return "authtokenexpired" in name or "tokenexpired" in name


def _render_qr_terminal(data: str) -> str:
    """Render QR data as a local Unicode matrix without printing the encoded data."""
    try:
        import qrcode
    except ImportError as exc:
        raise TelegramError("QR login requires the qrcode package; run uv sync") from exc
    try:
        code = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=4,
        )
        code.add_data(data)
        code.make(fit=True)
        matrix = code.get_matrix()
    except Exception as exc:
        raise TelegramError("Could not render the Telegram QR code locally") from exc
    black_cell = "\033[40m  "
    white_cell = "\033[47m  "
    reset = "\033[0m"
    return "\n".join("".join(black_cell if cell else white_cell for cell in row) + reset for row in matrix)


def build_telegram_reader(preferences: Preferences) -> TelethonReader:
    return TelethonReader(
        load_telegram_credentials(preferences),
        max_flood_wait_seconds=preferences.sources.telegram.max_flood_wait_seconds,
    )


def _raise_login_transport_error(exc: Exception, operation: str) -> None:
    wait_seconds = _flood_wait_seconds(exc)
    if wait_seconds is not None:
        raise TelegramRateLimitError(wait_seconds) from None
    if _is_connection_failure(exc):
        raise TelegramConnectionError(f"Telegram {operation} failed; check network connectivity") from None
    raise TelegramAuthorizationError(f"Telegram {operation} failed") from None


def _is_connection_failure(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError)):
        return True
    name = exc.__class__.__name__.casefold()
    return "connection" in name or "timeout" in name or "network" in name


def _format_code_delivery(response: Any) -> str:
    """Format only non-sensitive delivery metadata returned by Telegram."""
    timeout = getattr(response, "timeout", None)
    try:
        retry_timeout = f"{max(0, int(timeout))}s" if timeout is not None else "not provided"
    except (TypeError, ValueError):
        retry_timeout = "not provided"
    return (
        "Telegram code delivery: "
        f"delivery_type={_delivery_type_label(getattr(response, 'type', None))}; "
        f"next_delivery_type={_delivery_type_label(getattr(response, 'next_type', None))}; "
        f"retry_timeout={retry_timeout}"
    )


def _delivery_type_label(value: Any) -> str:
    if value is None:
        return "none"
    name = type(value).__name__
    for prefix in ("SentCodeType", "CodeType"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    if not name:
        return "unknown"
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).casefold()


def _telethon_imports() -> tuple[Any, Any]:
    try:
        from telethon import TelegramClient, errors
    except ImportError as exc:
        raise TelegramError("Telethon is not installed; run uv sync") from exc
    return TelegramClient, errors


def _create_telethon_client(
    client_class: Any,
    credentials: TelegramCredentials,
    *,
    receive_updates: bool,
    max_flood_wait_seconds: int,
) -> Any:
    try:
        return client_class(
            str(credentials.session_path),
            credentials.api_id,
            credentials.api_hash,
            receive_updates=receive_updates,
            sequential_updates=True,
            flood_sleep_threshold=max_flood_wait_seconds,
        )
    except Exception:
        raise TelegramError("Telegram local session could not be opened; check session directory permissions") from None


def _prepare_session_directory(session_path: Path) -> None:
    parent = session_path.parent
    try:
        existed = parent.exists()
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name != "posix":
            return
        if not existed:
            parent.chmod(0o700)
        if stat.S_IMODE(parent.stat().st_mode) & 0o077:
            raise TelegramError(
                "Telegram session directory permissions are too broad; restrict the directory to the local user"
            )
    except TelegramError:
        raise
    except OSError:
        raise TelegramError("Telegram session directory could not be prepared securely") from None


def _secure_session_files(session_path: Path, *, strict: bool = False) -> None:
    main_file = session_path if session_path.name.endswith(".session") else Path(f"{session_path}.session")
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = Path(f"{main_file}{suffix}")
        if candidate.exists():
            try:
                candidate.chmod(0o600)
                if os.name == "posix" and stat.S_IMODE(candidate.stat().st_mode) & 0o077:
                    raise OSError("session permissions remain too broad")
            except OSError:
                if strict:
                    raise TelegramError("Telegram session file permissions could not be restricted") from None


def _entity_usernames(entity: Any) -> list[str]:
    usernames: list[str] = []
    primary = getattr(entity, "username", None)
    if isinstance(primary, str) and primary:
        usernames.append(primary.lstrip("@"))
    for item in getattr(entity, "usernames", None) or []:
        username = getattr(item, "username", None)
        if isinstance(username, str) and username and getattr(item, "active", True):
            usernames.append(username.lstrip("@"))
    return list(dict.fromkeys(usernames))


def _serialize_message(source: str, entity: Any, message: Any, published_at: datetime) -> TelegramMessage:
    message_id = int(getattr(message, "id"))
    channel_id = int(getattr(entity, "id"))
    text = getattr(message, "raw_text", None) or getattr(message, "message", None) or ""
    return TelegramMessage(
        channel_username=source,
        channel_id=channel_id,
        message_id=message_id,
        message_url=f"https://t.me/{source}/{message_id}",
        text=str(text),
        published_at=published_at,
        edited_at=_aware_utc(getattr(message, "edit_date", None)),
        forwarded_from=_forwarded_from(message),
        entity_urls=tuple(_message_urls(message)),
    )


def _message_urls(message: Any) -> list[str]:
    urls: list[str] = []
    try:
        entities_text = message.get_entities_text()
    except (AttributeError, TypeError, ValueError):
        entities_text = []
    for entity, displayed_text in entities_text:
        target = getattr(entity, "url", None) or displayed_text
        if isinstance(target, str) and target.startswith(("http://", "https://")):
            urls.append(target)
    return list(dict.fromkeys(urls))


def _forwarded_from(message: Any) -> str | None:
    forward = getattr(message, "forward", None)
    if forward is None:
        return None
    name = getattr(forward, "from_name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    sender_id = getattr(forward, "sender_id", None)
    if sender_id is not None:
        return f"peer:{sender_id}"
    chat = getattr(forward, "chat", None)
    username = getattr(chat, "username", None)
    if isinstance(username, str) and username:
        return f"@{username}"
    return "forwarded"


def _aware_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _flood_wait_seconds(exc: Exception) -> int | None:
    if exc.__class__.__name__ not in {"FloodWaitError", "FloodWait"}:
        return None
    value = getattr(exc, "seconds", None) or getattr(exc, "value", None)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1
