class KilltrackerException(Exception):
    """Exception from Killtracker"""


class WebhookBlocked(KilltrackerException):
    """Webhook is temporarily blocked"""

    def __init__(self, seconds: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._seconds = seconds

    @property
    def seconds(self) -> int:
        return self._seconds
