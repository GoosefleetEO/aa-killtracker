class KilltrackerException(Exception):
    """Exception from Killtracker"""


class WebhookBlocked(KilltrackerException):
    """Webhook is temporarily blocked"""

    DEFAULT_RESET_AFTER = 0

    def __init__(self, reset_after: float = None, *args, **kwargs) -> None:
        """
        Parameters:
        - reset_after: time in seconds until this blockage will be reset
        """
        super().__init__(*args, **kwargs)
        if reset_after is None:
            reset_after = self.DEFAULT_RESET_AFTER
        self._reset_after = float(reset_after)

    @property
    def reset_after(self) -> float:
        return self._reset_after


class WebhookRateLimitReached(WebhookBlocked):
    DEFAULT_RESET_AFTER = 5.0


class WebhookTooManyRequests(WebhookBlocked):
    DEFAULT_RESET_AFTER = 600.0
