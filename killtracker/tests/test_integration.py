from unittest.mock import patch

import dhooks_lite
import requests_mock

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings

from ..core.killmails import ZKB_REDISQ_URL
from ..models import Tracker
from .. import tasks
from .testdata.helpers import killmails_data, LoadTestDataMixin

PACKAGE_PATH = "killtracker"


@override_settings(CELERY_ALWAYS_EAGER=True)
@patch(PACKAGE_PATH + ".tasks.send_messages_to_webhook.retry")
@patch(PACKAGE_PATH + ".models.dhooks_lite.Webhook.execute", spec=True)
@requests_mock.Mocker()
class TestIntegration(LoadTestDataMixin, TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cache.clear()
        cls.tracker_1 = Tracker.objects.create(
            name="My Tracker",
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook_1,
        )

    def my_retry(self, *args, **kwargs):
        tasks.send_messages_to_webhook.delay(self.webhook_1.pk)

    def test_normal_case(self, mock_execute, mock_retry, requests_mocker):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        mock_retry.side_effect = self.my_retry
        requests_mocker.register_uri(
            "GET",
            ZKB_REDISQ_URL,
            [
                {"status_code": 200, "json": {"package": killmails_data()[10000001]}},
                {"status_code": 200, "json": {"package": killmails_data()[10000002]}},
                {"status_code": 200, "json": {"package": killmails_data()[10000003]}},
                {"status_code": 200, "json": {"package": None}},
            ],
        )

        tasks.run_killtracker.delay()
        self.assertEqual(mock_execute.call_count, 2)

        _, kwargs = mock_execute.call_args_list[0]
        self.assertIn("My Tracker", kwargs["content"])
        self.assertIn("10000001", kwargs["embeds"][0].url)

        _, kwargs = mock_execute.call_args_list[1]
        self.assertIn("My Tracker", kwargs["content"])
        self.assertIn("10000002", kwargs["embeds"][0].url)
