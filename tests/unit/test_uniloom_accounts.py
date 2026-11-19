"""Each person's Uniloom sign-in, kept encrypted (docs/uniloom-plan.md, section 2)."""
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from src.uniloom.accounts import Account, AccountStore

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def _account(user_id="760", access="access-secret", refresh="refresh-secret"):
    return Account(user_id=user_id, subject="u-1", name="Oakar", issuer="https://auth.example/api/auth",
                   client_id="https://chrono.example/oauth/client.json",
                   token_endpoint="https://auth.example/api/auth/oauth2/token",
                   revocation_endpoint="https://auth.example/api/auth/oauth2/revoke",
                   resource="https://loom.example/mcp",
                   access_token=access, refresh_token=refresh, expires_at=NOW + timedelta(minutes=15),
                   scope="openid profile offline_access", connected_at=NOW, used_at=NOW)


@pytest.fixture
def store(database, tmp_path):
    return AccountStore(database, key_path=str(tmp_path / "uniloom.key"))


def test_an_account_comes_back_as_it_was_saved(store):
    store.save(_account())
    assert store.get("760") == _account()
    assert store.get("222") is None


def test_the_tokens_never_reach_the_database_in_the_clear(store, database):
    store.save(_account())
    raw = " ".join(str(value) for row in database.query("SELECT * FROM uniloom_accounts") for value in row)
    assert "access-secret" not in raw and "refresh-secret" not in raw


def test_the_key_file_is_only_readable_by_the_bot(store, tmp_path):
    store.save(_account())
    mode = stat.S_IMODE(os.stat(tmp_path / "uniloom.key").st_mode)
    assert mode == 0o600


def test_new_tokens_replace_the_old_ones(store):
    store.save(_account())
    later = NOW + timedelta(hours=1)
    store.update_tokens("760", "access-2", "refresh-2", later, "openid offline_access")
    account = store.get("760")
    assert (account.access_token, account.refresh_token, account.expires_at) == ("access-2", "refresh-2", later)


def test_tokens_that_cannot_be_read_count_as_not_connected(store, database, tmp_path):
    store.save(_account())
    # The key file was lost and a new one made: the old tokens are useless, so they go
    other = AccountStore(database, key_path=str(tmp_path / "other.key"))
    assert other.get("760") is None
    assert database.query("SELECT count(*) FROM uniloom_accounts")[0][0] == 0


def test_deleting_hands_back_what_was_there_for_revoking(store):
    store.save(_account())
    taken = store.delete("760")
    assert taken is not None and taken.refresh_token == "refresh-secret"
    assert store.get("760") is None
    assert store.delete("760") is None


def test_forgetting_someone_removes_their_sign_in_too(store):
    from src.user_data_manager import UserDataManager
    store.save(_account())
    UserDataManager().forget_user("760")
    assert not store.exists("760")
