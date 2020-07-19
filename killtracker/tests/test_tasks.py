from unittest.mock import patch

from django.db.models import Max

from ..models import Tracker, Webhook
from .testdata.helpers import (
    load_eveuniverse,
    load_eveentities,
    load_evealliances,
    load_killmail,
)
from ..tasks import run_tracker, send_killmails_to_webhook, run_killtracker
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
        cls.webhook = Webhook.objects.create(name="dummy", url="dummy")

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


@patch(MODULE_PATH + ".Killmail.fetch_from_zkb_redisq")
@patch(MODULE_PATH + ".run_tracker")
class TestRunKilltracker(TestTrackerBase):
    @staticmethod
    def my_fetch_from_zkb():
        for killmail_id in [10000001, 10000002, 10000003, None]:
            if killmail_id:
                yield load_killmail(killmail_id)
            else:
                yield None

    def test_normal(self, mock_run_tracker, mock_fetch_from_zkb):
        mock_fetch_from_zkb.side_effect = self.my_fetch_from_zkb()

        run_killtracker()
        self.assertEqual(mock_run_tracker.delay.call_count, 6)

    """
    @patch("killtracker.managers.KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS", 1)
    def test_delete_stale_killmails(self, mock_run_tracker, mock_fetch_from_zkb):
        km = EveKillmail.objects.get(id=10000001)
        km.time = now() - timedelta(days=1, seconds=1)
        km.save()

        run_killtracker(max_killmails_in_total=3)

        self.assertEqual(EveKillmail.objects.count(), 2)
        self.assertTrue(EveKillmail.objects.filter(id=10000002).exists())
        self.assertTrue(EveKillmail.objects.filter(id=10000003).exists())
    """


@patch("killtracker.models.Webhook.add_killmail_to_queue")
@patch(MODULE_PATH + ".send_killmails_to_webhook")
@patch(MODULE_PATH + ".logger")
class TestRunTracker(TestTrackerBase):
    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_killmails_to_webhook, mock_add_killmail_to_queue
    ):
        run_tracker(generate_invalid_pk(Tracker), 1, "dummy")

        self.assertFalse(mock_send_killmails_to_webhook.delay.called)
        self.assertTrue(mock_logger.warning.called)

    def test_run_normal(
        self, mock_logger, mock_send_killmails_to_webhook, mock_add_killmail_to_queue
    ):
        killmail = load_killmail(10000001)
        killmail_json = killmail.asjson()
        run_tracker(self.tracker_1.pk, killmail.id, killmail_json)
        self.assertEqual(mock_add_killmail_to_queue.call_count, 1)
        self.assertEqual(mock_send_killmails_to_webhook.delay.call_count, 1)
        self.assertFalse(mock_logger.warning.called)


@patch(MODULE_PATH + ".Webhook.send_queued_killmails")
@patch(MODULE_PATH + ".logger")
class TestSendAlertsToWebhook(TestTrackerBase):
    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_queued_killmails
    ):
        send_killmails_to_webhook(generate_invalid_pk(Webhook))

        self.assertFalse(mock_send_queued_killmails.called)
        self.assertTrue(mock_logger.warning.called)

    def test_run_normal(self, mock_logger, mock_send_queued_killmails):
        send_killmails_to_webhook(self.webhook.pk)
        self.assertEqual(mock_send_queued_killmails.call_count, 1)
        self.assertFalse(mock_logger.warning.called)
