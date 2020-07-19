class ResponseStub:
    """Stub for replacing requests Response"""

    def __init__(self, data) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._data
