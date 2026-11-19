"""
Each person's Uniloom sign-in. The access and refresh tokens are encrypted with a key file
(Fernet) before they reach the database, so a copy of the database alone, such as a
backup, gives nobody a way in. Everything else in a row is what refreshing and revoking
need, and none of it is secret.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from config.config import Config
from src.database import Database, get_database

logger = logging.getLogger(__name__)


@dataclass
class Account:
    user_id: str  # Discord
    subject: str  # uniAuth's id for the person
    name: str
    issuer: str
    client_id: str
    token_endpoint: str
    revocation_endpoint: str
    resource: str  # the MCP URL the tokens are for (RFC 8707)
    access_token: str
    refresh_token: str
    expires_at: datetime  # when the access token stops working
    scope: str
    connected_at: datetime
    used_at: datetime


def _load_key(path: str) -> bytes:
    """
    The key in path, or a new one written there that only the bot's user can read. The new
    key is written to a temporary file first and then put in place, so a crash can't leave
    an empty key behind. An empty one (from before that) can't have sealed anything, so it is replaced.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    try:
        with open(path, "rb") as handle:
            key = handle.read().strip()
        if key:
            return key
        os.remove(path)
    except FileNotFoundError:
        pass
    key = Fernet.generate_key()
    temporary = os.path.join(directory, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        # link fails when another process put a key there first, and then that one is used
        os.link(temporary, path)
    except FileExistsError:
        with open(path, "rb") as handle:
            key = handle.read().strip()
    finally:
        os.remove(temporary)
    return key


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


class AccountStore:
    def __init__(self, database: Optional[Database] = None, key_path: Optional[str] = None):
        self.db = database or get_database()
        self._fernet = Fernet(_load_key(key_path or Config.UNILOOM_TOKEN_KEY_PATH))

    def _seal(self, access_token: str, refresh_token: str) -> str:
        data = json.dumps({"access_token": access_token, "refresh_token": refresh_token}).encode()
        return self._fernet.encrypt(data).decode("ascii")

    def _row_to_account(self, row) -> Optional[Account]:
        try:
            secrets = json.loads(self._fernet.decrypt(row["secrets"].encode("ascii")))
        except (InvalidToken, ValueError):
            # Sealed with another key, so the key file was lost or replaced. The tokens are
            # useless now and the person only has to connect again.
            logger.warning("A Uniloom sign-in could not be decrypted and was removed")
            self.db.execute("DELETE FROM uniloom_accounts WHERE user_id = ?", (row["user_id"],))
            return None
        return Account(
            user_id=row["user_id"], subject=row["subject"], name=row["name"], issuer=row["issuer"],
            client_id=row["client_id"], token_endpoint=row["token_endpoint"],
            revocation_endpoint=row["revocation_endpoint"], resource=row["resource"],
            access_token=secrets["access_token"], refresh_token=secrets["refresh_token"],
            expires_at=_time(row["expires_at"]), scope=row["scope"],
            connected_at=_time(row["connected_at"]), used_at=_time(row["used_at"]))

    def get(self, user_id: str) -> Optional[Account]:
        rows = self.db.query("SELECT * FROM uniloom_accounts WHERE user_id = ?", (str(user_id),))
        return self._row_to_account(rows[0]) if rows else None

    def exists(self, user_id: str) -> bool:
        """True when the person has a sign-in, without decrypting it."""
        return bool(self.db.query("SELECT 1 FROM uniloom_accounts WHERE user_id = ?", (str(user_id),)))

    def save(self, account: Account) -> None:
        """Keep a new sign-in. One person has one, so connecting again replaces the old one."""
        self.db.execute(
            "INSERT INTO uniloom_accounts (user_id, subject, name, issuer, client_id, token_endpoint, "
            "revocation_endpoint, resource, secrets, scope, expires_at, connected_at, used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET subject = excluded.subject, name = excluded.name, "
            "issuer = excluded.issuer, client_id = excluded.client_id, token_endpoint = excluded.token_endpoint, "
            "revocation_endpoint = excluded.revocation_endpoint, resource = excluded.resource, "
            "secrets = excluded.secrets, scope = excluded.scope, expires_at = excluded.expires_at, "
            "connected_at = excluded.connected_at, used_at = excluded.used_at",
            (str(account.user_id), account.subject, account.name, account.issuer, account.client_id,
             account.token_endpoint, account.revocation_endpoint, account.resource,
             self._seal(account.access_token, account.refresh_token), account.scope,
             account.expires_at.isoformat(), account.connected_at.isoformat(), account.used_at.isoformat()))

    def update_tokens(self, user_id: str, access_token: str, refresh_token: str, expires_at: datetime,
                      scope: str) -> None:
        """
        The tokens from a refresh. uniAuth cancels the sign-in when a used refresh token comes
        back, so the new one has to be written before anything else uses the account.
        """
        self.db.execute(
            "UPDATE uniloom_accounts SET secrets = ?, expires_at = ?, scope = ?, used_at = ? WHERE user_id = ?",
            (self._seal(access_token, refresh_token), expires_at.isoformat(), scope,
             datetime.now(timezone.utc).isoformat(), str(user_id)))

    def delete(self, user_id: str) -> Optional[Account]:
        """Forget a sign-in. Returns it, so its refresh token can still be revoked."""
        account = self.get(user_id)
        self.db.execute("DELETE FROM uniloom_accounts WHERE user_id = ?", (str(user_id),))
        return account


def forget_account(user_id: str, database: Optional[Database] = None) -> None:
    """Remove a sign-in without reading it, for /forget. /forget revokes it at uniAuth first."""
    (database or get_database()).execute("DELETE FROM uniloom_accounts WHERE user_id = ?", (str(user_id),))
