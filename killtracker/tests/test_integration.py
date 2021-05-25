import functools
from unittest.mock import patch

import dhooks_lite
import requests_mock

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings

from .. import tasks
from ..core.killmails import ZKB_REDISQ_URL
from ..models import Tracker
from .testdata.helpers import LoadTestDataMixin, killmails_data

PACKAGE_PATH = "killtracker"


@override_settings(CELERY_ALWAYS_EAGER=True)
@patch(PACKAGE_PATH + ".tasks.is_esi_online", lambda: True)
@patch(PACKAGE_PATH + ".tasks.send_messages_to_webhook.retry")
@patch(PACKAGE_PATH + ".tasks.run_killtracker.retry")
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
        """generic retry that will call the function given in task
        and ignore other parameters
        """
        kwargs["task"]()

    def test_normal_case(
        self,
        mock_execute,
        run_killtracker_retry,
        send_messages_to_webhook_retry,
        requests_mocker,
    ):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        run_killtracker_retry.side_effect = functools.partial(
            self.my_retry, task=tasks.run_killtracker
        )
        send_messages_to_webhook_retry.side_effect = functools.partial(
            self.my_retry,
            task=functools.partial(
                tasks.send_messages_to_webhook, webhook_pk=self.webhook_1.pk
            ),
        )
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
