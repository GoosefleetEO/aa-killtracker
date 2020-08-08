from unittest.mock import patch

import dhooks_lite

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings

from . import ResponseStub
from ..models import Tracker
from ..tasks import run_killtracker
from .testdata.helpers import killmails_data, LoadTestDataMixin

PACKAGE_PATH = "killtracker"


@override_settings(CELERY_ALWAYS_EAGER=True)
@patch(PACKAGE_PATH + ".models.sleep", new=lambda x: None)
@patch(PACKAGE_PATH + ".models.dhooks_lite.Webhook.execute", spec=True)
@patch(PACKAGE_PATH + ".core.killmails.requests", spec=True)
class TestIntegration(LoadTestDataMixin, TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cache.clear()
        cls.tracker_1 = Tracker.objects.create(
            name="Low Sec Only",
            exclude_high_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook_1,
        )

    @staticmethod
    def my_redisq(*args, **kwargs):
        my_killmails_data = killmails_data()
        for killmail_id in [10000001, 10000002, 10000003, None]:
            if killmail_id:
                yield ResponseStub({"package": my_killmails_data[killmail_id]})
            else:
                yield ResponseStub({"package": None})

    def test_normal_case(self, mock_requests, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        mock_requests.get.side_effect = self.my_redisq()

        run_killtracker()
        self.assertTrue(mock_execute.called, True)
        _, kwargs = mock_execute.call_args
        self.assertIn("Low Sec Only", kwargs["content"])
