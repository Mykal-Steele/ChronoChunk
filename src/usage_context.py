"""
Who a model call is for, and what it is doing. Set once where a request comes in and
read where the call is made, so nothing in between has to pass it along. asyncio
copies it into every task a request starts, so work that finishes in the background
is still counted against the right person.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Tuple

from src.user_store import clean_user_id

# (user id, feature). An empty user id means the call belongs to nobody in particular.
_current: ContextVar[Tuple[str, str]] = ContextVar("usage_context", default=("", "chat"))


def set_user(user_id) -> None:
    """Mark everything this request does from here on as this user's."""
    _current.set((clean_user_id(user_id), "chat"))


def current() -> Tuple[str, str]:
    """The user id and feature of the request being handled."""
    return _current.get()


def label(name: str) -> None:
    """Label every model call the current task makes from here on. For a task that does one job."""
    user_id, _ = _current.get()
    _current.set((user_id, name))


@contextmanager
def feature(name: str) -> Iterator[None]:
    """Label the model calls made inside the block, for example "facts" or "pdf_notes"."""
    user_id, _ = _current.get()
    token = _current.set((user_id, name))
    try:
        yield
    finally:
        _current.reset(token)
