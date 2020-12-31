class KilltrackerException(Exception):
    """Exception from Killtracker"""


class WebhookBlocked(KilltrackerException):
    """Webhook is temporarily blocked"""

    DEFAULT_RESET_AFTER = 0

    def __init__(self, reset_after: int = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if reset_after is None:
            reset_after = self.DEFAULT_RESET_AFTER
        self._reset_after = reset_after

    @property
    def reset_after(self) -> int:
        return self._reset_after


class WebhookRateLimitReached(WebhookBlocked):
    DEFAULT_RESET_AFTER = 5


class WebhookTooManyRequests(WebhookBlocked):
    DEFAULT_RESET_AFTER = 600
