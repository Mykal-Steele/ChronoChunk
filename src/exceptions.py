class BotError(Exception):
    """Base exception for all bot errors"""

class ConfigError(BotError):
    """Something's wrong with the config"""

class UserDataError(BotError):
    """Something went wrong with user data"""

class AIError(BotError):
    """AI service messed up"""
    def __init__(self, message: str, retry_ok: bool = True):
        self.retry_ok = retry_ok  # if True, we can try the request again
        super().__init__(message)

class RateLimitError(BotError):
    """Yo chill with the requests"""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, try again in {retry_after:.1f} seconds")


class RequestBusy(BotError):
    """The bot is already handling as many replies as it can."""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Busy, try again in {retry_after:.1f} seconds")


class ReplyLimitError(BotError):
    """One reply tried to make too many model calls or cost too much."""
