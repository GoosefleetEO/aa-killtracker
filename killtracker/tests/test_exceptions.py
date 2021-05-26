from django.test import TestCase

from ..exceptions import WebhookTooManyRequests


class TestExceptions(TestCase):
    def test_exception(self):
        ex = WebhookTooManyRequests(10)
        self.assertEqual(ex.retry_after, 10)

        ex = WebhookTooManyRequests()
        self.assertEqual(ex.retry_after, 600)
