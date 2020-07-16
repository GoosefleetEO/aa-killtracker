from datetime import timedelta
from unittest.mock import patch

from django.db.models import Max
from django.utils.timezone import now

from ..models import Killmail, Tracker, TrackedKillmail, Webhook
from .testdata.helpers import (
    load_eveuniverse,
    load_eveentities,
    load_evealliances,
    load_killmails,
)
from ..tasks import run_tracker, send_alerts_to_webhook, run_killtracker
from ..utils import NoSocketsTestCase, set_test_logger


MODULE_PATH = "killtracker.tasks"
logger = set_test_logger(MODULE_PATH, __file__)


class CacheStub:
    """Stub for replacing Django cache"""

    def get(self, key, default=None, version=None):
        return None

    def get_or_set(self, key, default, timeout=None):
        return default()

    def set(self, key, value, timeout=None):
        return None


def generate_invalid_pk(MyModel):
    return MyModel.objects.aggregate(Max("pk"))["pk__max"] + 1


class TestTrackerBase(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        load_eveuniverse()
        load_evealliances()
        load_eveentities()
        cls.webhook = Webhook.objects.create(name="dummy", url="dummy", is_default=True)

        cls.tracker_1 = Tracker.objects.create(
            name="Low Sec Only",
            exclude_high_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook,
        )
        cls.tracker_2 = Tracker.objects.create(
            name="High Sec Only",
            exclude_low_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook,
        )
        load_killmails({10000001, 10000002, 10000003})


@patch(MODULE_PATH + ".send_alerts_to_webhook")
class TestRunTracker(TestTrackerBase):
    @patch(MODULE_PATH + ".logger")
    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_alerts_to_webhook
    ):
        run_tracker(generate_invalid_pk(Tracker))

        self.assertFalse(mock_send_alerts_to_webhook.delay.called)
        self.assertTrue(mock_logger.warning.called)

    def test_run_normal(self, mock_send_alerts_to_webhook):
        run_tracker(self.tracker_1.pk)
        self.assertEqual(
            TrackedKillmail.objects.filter(tracker=self.tracker_1).count(), 3
        )
        self.assertEqual(
            TrackedKillmail.objects.filter(tracker=self.tracker_2).count(), 0
        )
        self.assertEqual(mock_send_alerts_to_webhook.delay.call_count, 1)


@patch(MODULE_PATH + ".Tracker.send_matching_to_webhook")
class TestSendAlertsToWebhook(TestTrackerBase):
    @patch(MODULE_PATH + ".logger")
    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_matching_to_webhook
    ):
        send_alerts_to_webhook(generate_invalid_pk(Webhook))

        self.assertFalse(mock_send_matching_to_webhook.called)
        self.assertTrue(mock_logger.warning.called)

    def test_run_normal(self, mock_send_matching_to_webhook):
        send_alerts_to_webhook(self.webhook.pk)
        self.assertEqual(mock_send_matching_to_webhook.call_count, 2)


@patch(MODULE_PATH + ".Killmail.objects.fetch_from_zkb")
@patch(MODULE_PATH + ".run_tracker")
class TestRunKilltracker(TestTrackerBase):
    @staticmethod
    def my_fetch_from_zkb():
        for killmail in Killmail.objects.all():
            yield killmail

    def test_normal(self, mock_run_tracker, mock_fetch_from_zkb):
        mock_fetch_from_zkb.side_effect = self.my_fetch_from_zkb

        run_killtracker(max_killmails_in_total=3)
        self.assertEqual(mock_run_tracker.delay.call_count, 2)

    @patch("killtracker.managers.KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS", 1)
    def test_delete_stale_killmails(self, mock_run_tracker, mock_fetch_from_zkb):
        km = Killmail.objects.get(id=10000001)
        km.time = now() - timedelta(days=1, seconds=1)
        km.save()

        run_killtracker(max_killmails_in_total=3)

        self.assertEqual(Killmail.objects.count(), 2)
        self.assertTrue(Killmail.objects.filter(id=10000002).exists())
        self.assertTrue(Killmail.objects.filter(id=10000003).exists())
