from unittest.mock import patch

from django.test import TestCase
from django.test.utils import override_settings

from ..exceptions import WebhookRateLimitReached, WebhookTooManyRequests
from ..models import EveKillmail, Tracker, Webhook
from .testdata.helpers import load_killmail, load_eve_killmails, LoadTestDataMixin
from ..tasks import (
    delete_stale_killmails,
    run_tracker,
    send_killmails_to_webhook,
    run_killtracker,
    store_killmail,
    send_test_message_to_webhook,
)
from ..utils import NoSocketsTestCase, generate_invalid_pk


MODULE_PATH = "killtracker.tasks"


class TestTrackerBase(LoadTestDataMixin, NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tracker_1 = Tracker.objects.create(
            name="Low Sec Only",
            exclude_high_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook_1,
        )
        cls.tracker_2 = Tracker.objects.create(
            name="High Sec Only",
            exclude_low_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=cls.webhook_1,
        )


@override_settings(CELERY_ALWAYS_EAGER=True)
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
        self.assertEqual(mock_store_killmail.si.call_count, 0)
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
        self.assertEqual(mock_store_killmail.si.call_count, 3)
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
        self,
        mock_logger,
        mock_send_killmails_to_webhook,
        mock_add_killmail_to_queue,
    ):
        killmail = load_killmail(10000001)
        killmail_json = killmail.asjson()

        run_tracker(self.tracker_1.pk, killmail_json)

        self.assertEqual(mock_add_killmail_to_queue.call_count, 1)
        self.assertEqual(mock_send_killmails_to_webhook.delay.call_count, 1)
        self.assertFalse(mock_logger.error.called)


@patch(MODULE_PATH + ".send_killmails_to_webhook.retry")
@patch(MODULE_PATH + ".Webhook.send_killmail")
@patch(MODULE_PATH + ".logger")
class TestSendKillmailsToWebhook(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        self.webhook_1.clear_queue()

    def my_retry(self, *args, **kwargs):
        send_killmails_to_webhook(self.webhook_1.pk)

    def test_one_message(self, mock_logger, mock_send_killmail, mock_retry):
        """
        when one mesage in queue
        then send it and returns 1
        """
        mock_retry.side_effect = self.my_retry
        mock_send_killmail.return_value = True
        self.webhook_1.add_killmail_to_queue(load_killmail(10000001))

        send_killmails_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_killmail.call_count, 1)
        self.assertEqual(self.webhook_1.queue_size(), 0)
        self.assertFalse(mock_logger.error.called)

    def test_three_message(self, mock_logger, mock_send_killmail, mock_retry):
        """
        when three mesages in queue
        then sends them and returns 3
        """

        mock_retry.side_effect = self.my_retry
        mock_send_killmail.return_value = True
        self.webhook_1.add_killmail_to_queue(load_killmail(10000001))
        self.webhook_1.add_killmail_to_queue(load_killmail(10000002))
        self.webhook_1.add_killmail_to_queue(load_killmail(10000003))

        send_killmails_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_killmail.call_count, 3)
        self.assertEqual(self.webhook_1.queue_size(), 0)

    def test_no_messages(self, mock_logger, mock_send_killmail, mock_retry):
        """
        when no message in queue
        then do nothing and return 0
        """
        mock_retry.side_effect = self.my_retry
        mock_send_killmail.return_value = True

        send_killmails_to_webhook(self.webhook_1.pk)

        self.assertEqual(mock_send_killmail.call_count, 0)
        self.assertEqual(self.webhook_1.queue_size(), 0)

    def test_failed_message(self, mock_logger, mock_send_killmail, mock_retry):
        """
        given one message in queue
        when sending fails
        then re-queues message and return 0

        mock_send_killmail.return_value = False
        self.webhook_1.add_killmail_to_queue(load_killmail(10000001))

        result = send_killmails_to_webhook(self.webhook_1.pk)

        self.assertEqual(result, 0)
        self.assertTrue(mock_send_killmail.called)
        self.assertEqual(self.webhook_1.queue_size(), 1)
        """

    def test_retry_on_rate_limit(self, mock_logger, mock_send_killmail, mock_retry):
        """
        when WebhookRateLimitReached exception is raised
        then message was send and retry once
        """
        mock_retry.side_effect = lambda countdown: None
        mock_send_killmail.side_effect = WebhookRateLimitReached(10)
        self.webhook_1.add_killmail_to_queue(load_killmail(10000001))

        send_killmails_to_webhook(self.webhook_1.pk)

        self.assertTrue(mock_retry.called)
        self.assertEqual(mock_retry.call_args[1]["countdown"], 10)
        self.assertEqual(mock_send_killmail.call_count, 1)
        self.assertEqual(self.webhook_1.queue_size(), 0)

    def test_retry_on_too_many_requests(
        self, mock_logger, mock_send_killmail, mock_retry
    ):
        """
        when WebhookTooManyRequests exception is raised
        then message is re-queued and retry once
        """
        mock_retry.side_effect = lambda countdown: None
        mock_send_killmail.side_effect = WebhookTooManyRequests(10)
        self.webhook_1.add_killmail_to_queue(load_killmail(10000001))

        send_killmails_to_webhook(self.webhook_1.pk)

        self.assertTrue(mock_retry.called)
        self.assertEqual(mock_retry.call_args[1]["countdown"], 10)
        self.assertEqual(mock_send_killmail.call_count, 1)
        self.assertEqual(self.webhook_1.queue_size(), 1)

    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_killmail, mock_retry
    ):
        mock_retry.side_effect = self.my_retry

        send_killmails_to_webhook(generate_invalid_pk(Webhook))

        self.assertFalse(mock_send_killmail.called)
        self.assertTrue(mock_logger.error.called)

    def test_log_info_if_not_enabled(self, mock_logger, mock_send_killmail, mock_retry):
        my_webhook = Webhook.objects.create(
            name="disabled", url="dummy-url-2", is_enabled=False
        )
        send_killmails_to_webhook(my_webhook.pk)

        self.assertFalse(mock_send_killmail.called)
        self.assertTrue(mock_logger.info.called)


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


@override_settings(CELERY_ALWAYS_EAGER=True)
@patch(MODULE_PATH + ".Killmail.create_from_zkb_api")
@patch(MODULE_PATH + ".Webhook.send_killmail")
@patch(MODULE_PATH + ".logger")
class TestSendTestKillmailsToWebhook(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        self.webhook_1.clear_queue()

    @staticmethod
    def my_create_from_zkb_api(killmail_id):
        return load_killmail(killmail_id)

    def test_log_warning_when_pk_is_invalid(
        self, mock_logger, mock_send_killmail, mock_create_from_zkb_api
    ):
        mock_create_from_zkb_api.side_effect = self.my_create_from_zkb_api

        send_test_message_to_webhook(generate_invalid_pk(Webhook))

        self.assertEqual(mock_send_killmail.call_count, 0)
        self.assertTrue(mock_logger.error.called)

    def test_run_normal(
        self, mock_logger, mock_send_killmail, mock_create_from_zkb_api
    ):
        mock_create_from_zkb_api.side_effect = self.my_create_from_zkb_api

        send_test_message_to_webhook(self.webhook_1.pk, killmail_id=10000001)
        self.assertEqual(mock_send_killmail.call_count, 1)
        self.assertFalse(mock_logger.error.called)


@patch(MODULE_PATH + ".EveKillmail.objects.delete_stale")
class TestDeleteStaleKillmails(TestTrackerBase):
    def test_normal(self, mock_delete_stale):
        mock_delete_stale.return_value = (1, {"killtracker.EveKillmail": 1})
        delete_stale_killmails()
        self.assertTrue(mock_delete_stale.called)
