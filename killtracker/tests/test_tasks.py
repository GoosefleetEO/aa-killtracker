from unittest.mock import patch

from django.db.models import Max

from ..models import EveKillmail, Tracker, Webhook
from .testdata.helpers import (
    load_eveuniverse,
    load_eveentities,
    load_evealliances,
    load_killmail,
    load_eve_killmails,
)
from ..tasks import (
    run_tracker,
    send_killmails_to_webhook,
    run_killtracker,
    store_killmail,
)
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


@patch(MODULE_PATH + ".delete_stale_killmails")
@patch(MODULE_PATH + ".store_killmail")
@patch(MODULE_PATH + ".Killmail.create_from_zkb_redisq")
@patch(MODULE_PATH + ".run_tracker")
class TestRunKilltracker(TestTrackerBase):
    @staticmethod
    def my_fetch_from_zkb():
        for killmail_id in [10000001, 10000002, 10000003, None]:
            if killmail_id:
                yield load_killmail(killmail_id)
            else:
                yield None

    @patch(MODULE_PATH + ".KILLTRACKER_STORING_KILLMAILS_ENABLED", False)
    def test_normal(
        self,
        mock_run_tracker,
        mock_create_from_zkb_redisq,
        mock_store_killmail,
        mock_delete_stale_killmails,
    ):
        mock_create_from_zkb_redisq.side_effect = self.my_fetch_from_zkb()

        run_killtracker()
        self.assertEqual(mock_run_tracker.delay.call_count, 6)
        self.assertEqual(mock_store_killmail.delay.call_count, 0)
        self.assertFalse(mock_delete_stale_killmails.delay.called)

    @patch(MODULE_PATH + ".KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS", 30)
    @patch(MODULE_PATH + ".KILLTRACKER_STORING_KILLMAILS_ENABLED", True)
    def test_can_store_killmails(
        self,
        mock_run_tracker,
        mock_create_from_zkb_redisq,
        mock_store_killmail,
        mock_delete_stale_killmails,
    ):
        mock_create_from_zkb_redisq.side_effect = self.my_fetch_from_zkb()

        run_killtracker()
        self.assertEqual(mock_run_tracker.delay.call_count, 6)
        self.assertEqual(mock_store_killmail.delay.call_count, 3)
        self.assertTrue(mock_delete_stale_killmails.delay.called)


@patch("killtracker.models.Webhook.add_killmail_to_queue")
@patch(MODULE_PATH + ".send_killmails_to_webhook")
@patch(MODULE_PATH + ".logger")
class TestRunTracker(TestTrackerBase):
    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_killmails_to_webhook, mock_add_killmail_to_queue
    ):
        run_tracker(generate_invalid_pk(Tracker), "dummy")

        self.assertFalse(mock_send_killmails_to_webhook.delay.called)
        self.assertTrue(mock_logger.error.called)
        self.assertFalse(mock_add_killmail_to_queue.called)

    def test_run_normal(
        self, mock_logger, mock_send_killmails_to_webhook, mock_add_killmail_to_queue,
    ):
        killmail = load_killmail(10000001)
        killmail_json = killmail.asjson()

        run_tracker(self.tracker_1.pk, killmail_json)

        self.assertEqual(mock_add_killmail_to_queue.call_count, 1)
        self.assertEqual(mock_send_killmails_to_webhook.delay.call_count, 1)
        self.assertFalse(mock_logger.error.called)


@patch(MODULE_PATH + ".Webhook.send_queued_killmails")
@patch(MODULE_PATH + ".logger")
class TestSendAlertsToWebhook(TestTrackerBase):
    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_queued_killmails
    ):
        send_killmails_to_webhook(generate_invalid_pk(Webhook))

        self.assertFalse(mock_send_queued_killmails.called)
        self.assertTrue(mock_logger.error.called)

    def test_run_normal(self, mock_logger, mock_send_queued_killmails):
        send_killmails_to_webhook(self.webhook.pk)
        self.assertEqual(mock_send_queued_killmails.call_count, 1)
        self.assertFalse(mock_logger.error.called)


@patch(MODULE_PATH + ".logger")
class TestStoreKillmail(TestTrackerBase):
    def test_normal(self, mock_logger):
        killmail = load_killmail(10000001)
        killmail_json = killmail.asjson()
        store_killmail(killmail_json)

        self.assertTrue(EveKillmail.objects.filter(id=10000001).exists())
        self.assertFalse(mock_logger.warning.called)

    def test_already_exists(self, mock_logger):
        load_eve_killmails([10000001])
        killmail = load_killmail(10000001)
        killmail_json = killmail.asjson()
        store_killmail(killmail_json)

        self.assertTrue(mock_logger.warning.called)
