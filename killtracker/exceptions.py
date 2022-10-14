class KilltrackerException(Exception):
    """Exception from Killtracker"""


class WebhookTooManyRequests(KilltrackerException):
    """Webhook is temporarily blocked."""

    DEFAULT_RESET_AFTER = 600

    def __init__(self, retry_after: int = None) -> None:
        """
        Parameters:
        - retry_after: time in seconds until this blockage will be reset
        """
        super().__init__()
        if retry_after is None:
            retry_after = self.DEFAULT_RESET_AFTER
        self._reset_after = int(retry_after)

    @property
    def retry_after(self) -> int:
        return self._reset_after


class KillmailDoesNotExist(KilltrackerException):
    """Killmail does not exist in storage."""
