from datetime import timedelta
from unittest.mock import patch

import dhooks_lite

from django.utils.timezone import now

from allianceauth.eveonline.models import EveAllianceInfo

from ..models import Killmail, Tracker, TrackedKillmail, Webhook
from .testdata.helpers import (
    load_eveuniverse,
    load_eveentities,
    load_evealliances,
    load_killmails,
    killmails_data,
)
from ..utils import NoSocketsTestCase, set_test_logger


MODULE_PATH = "killtracker.models"
logger = set_test_logger(MODULE_PATH, __file__)


class CacheStub:
    """Stub for replacing Django cache"""

    def get(self, key, default=None, version=None):
        return None

    def get_or_set(self, key, default, timeout=None):
        return default()

    def set(self, key, value, timeout=None):
        return None


class ResponseStub:
    """Stub for replacing requests Response"""

    def __init__(self, data) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._data


class TestCaseBase(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        load_eveuniverse()
        load_evealliances()
        load_eveentities()
        cls.webhook_1 = Webhook.objects.create(
            name="Webhook 1", url="http://www.example.com/webhook_1", is_default=True
        )
        cls.webhook_2 = Webhook.objects.create(
            name="Webhook 2", url="http://www.example.com/webhook_2", is_enabled=False
        )


@patch("killtracker.managers.requests", spec=True)
class TestKillmails(TestCaseBase):
    def test_fetch_from_zkb_normal(self, mock_requests):
        mock_requests.get.return_value = ResponseStub(
            {"package": killmails_data[10000001]}
        )

        killmail = Killmail.objects.fetch_from_zkb()

        self.assertIsNotNone(killmail)
        self.assertTrue(Killmail.objects.filter(id=killmail.id).exists())
        self.assertEqual(killmail.id, 10000001)
        self.assertEqual(killmail.solar_system_id, 30004984)

        self.assertEqual(killmail.victim.alliance_id, 3011)
        self.assertEqual(killmail.victim.character_id, 1011)
        self.assertEqual(killmail.victim.corporation_id, 2011)
        self.assertEqual(killmail.victim.damage_taken, 434)
        self.assertEqual(killmail.victim.ship_type_id, 603)

        self.assertEqual(killmail.attackers.count(), 3)

        attacker_1 = killmail.attackers.filter(is_final_blow=True).first()
        self.assertEqual(attacker_1.alliance_id, 3001)
        self.assertEqual(attacker_1.character_id, 1001)
        self.assertEqual(attacker_1.corporation_id, 2001)
        self.assertEqual(attacker_1.damage_done, 434)
        self.assertEqual(attacker_1.security_status, -10)
        self.assertEqual(attacker_1.ship_type_id, 34562)
        self.assertEqual(attacker_1.weapon_type_id, 2977)

        self.assertEqual(killmail.zkb.location_id, 50012306)
        self.assertEqual(killmail.zkb.fitted_value, 10000)
        self.assertEqual(killmail.zkb.total_value, 10000)
        self.assertEqual(killmail.zkb.points, 1)
        self.assertFalse(killmail.zkb.is_npc)
        self.assertFalse(killmail.zkb.is_solo)
        self.assertFalse(killmail.zkb.is_awox)

    def test_fetch_from_zkb_no_data(self, mock_requests):
        mock_requests.get.return_value = ResponseStub({"package": None})

        killmail = Killmail.objects.fetch_from_zkb()
        self.assertIsNone(killmail)


@patch("eveuniverse.models.cache", new=CacheStub())
class TestTrackerCalculate(TestCaseBase):
    def test_can_match_all(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name="Test")
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002, 10000003, 10000004, 10000005}
        self.assertEqual(result, expected)

    def test_can_filter_max_age(self):
        load_killmails({10000001, 10000002})
        killmail = Killmail.objects.get(id=10000002)
        killmail.time = now() - timedelta(hours=1, seconds=1)
        killmail.save()
        tracker = Tracker.objects.create(name="Test", max_age=1)
        result = tracker.calculate_killmails()
        expected = {10000001}
        self.assertEqual(result, expected)

    def test_can_filter_high_sec_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name="Test", exclude_high_sec=True)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000003, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_low_sec_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name="Test", exclude_low_sec=True)
        result = tracker.calculate_killmails()
        expected = {10000002, 10000003, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_null_sec_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name="Test", exclude_null_sec=True)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_w_space_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name="Test", exclude_w_space=True)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002, 10000003}
        self.assertEqual(result, expected)

    def test_can_filter_min_attackers(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name="Test", min_attackers=3)
        result = tracker.calculate_killmails()
        expected = {10000001}
        self.assertEqual(result, expected)

    def test_can_filter_max_attackers(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name="Test", max_attackers=2)
        result = tracker.calculate_killmails()
        expected = {10000002, 10000003, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_min_value(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name="Test", min_value=1000000000)
        result = tracker.calculate_killmails()
        expected = {10000004}
        self.assertEqual(result, expected)

    def test_can_filter_max_distance(self):
        load_killmails({10000101, 10000102, 10000103})
        tracker = Tracker.objects.create(
            name="Test", origin_solar_system_id=30003067, max_distance=2,
        )
        result = tracker.calculate_killmails()
        expected = {10000102, 10000103}
        self.assertEqual(result, expected)

    def test_can_filter_max_jumps(self):
        load_killmails({10000101, 10000102, 10000103})
        tracker = Tracker.objects.create(
            name="Test", origin_solar_system_id=30003067, max_jumps=3,
        )
        result = tracker.calculate_killmails()
        expected = {10000102, 10000103}
        self.assertEqual(result, expected)

    def test_can_filter_attacker_alliance(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name="Test")
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.exclude_attacker_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000005}
        self.assertEqual(result, expected)

    def test_can_required_attacker_alliances(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name="Test")
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3011)
        tracker.required_attacker_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000005}
        self.assertEqual(result, expected)

    def test_can_required_victim_alliances(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name="Test")
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.require_victim_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000005}
        self.assertEqual(result, expected)

    def test_can_filter_nullsec_and_attacker_alliance(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name="Test", exclude_null_sec=True)
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.required_attacker_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002, 10000004}
        self.assertEqual(result, expected)

    def test_creates_correct_results(self):
        load_killmails({10000001, 10000002, 10000003})
        tracker_1 = Tracker.objects.create(
            name="Low Sec Only",
            exclude_high_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
        )
        tracker_2 = Tracker.objects.create(
            name="High Sec Only",
            exclude_low_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
        )
        tracker_1.calculate_killmails()
        tracker_2.calculate_killmails()

        self.assertEqual(TrackedKillmail.objects.filter(tracker=tracker_1).count(), 3)

        result_1_1 = TrackedKillmail.objects.get(
            tracker=tracker_1, killmail_id=10000001
        )
        self.assertTrue(result_1_1.is_low_sec)
        self.assertTrue(result_1_1.is_matching)
        self.assertFalse(result_1_1.date_sent)

        result_1_2 = TrackedKillmail.objects.get(
            tracker=tracker_1, killmail_id=10000002
        )
        self.assertTrue(result_1_2.is_high_sec)
        self.assertFalse(result_1_2.is_matching)
        self.assertFalse(result_1_1.date_sent)

        result_1_3 = TrackedKillmail.objects.get(
            tracker=tracker_1, killmail_id=10000003
        )
        self.assertTrue(result_1_3.is_null_sec)
        self.assertFalse(result_1_3.is_matching)
        self.assertFalse(result_1_1.date_sent)

        self.assertEqual(TrackedKillmail.objects.filter(tracker=tracker_2).count(), 3)

        result_2_1 = TrackedKillmail.objects.get(
            tracker=tracker_2, killmail_id=10000001
        )
        self.assertTrue(result_2_1.is_low_sec)
        self.assertFalse(result_2_1.is_matching)
        self.assertFalse(result_1_1.date_sent)

        result_2_2 = TrackedKillmail.objects.get(
            tracker=tracker_2, killmail_id=10000002
        )
        self.assertTrue(result_2_2.is_high_sec)
        self.assertTrue(result_2_2.is_matching)
        self.assertFalse(result_1_1.date_sent)

        result_2_3 = TrackedKillmail.objects.get(
            tracker=tracker_2, killmail_id=10000003
        )
        self.assertTrue(result_2_3.is_null_sec)
        self.assertFalse(result_2_3.is_matching)
        self.assertFalse(result_1_1.date_sent)

    def test_runs_only_once(self):
        load_killmails({10000001, 10000002, 10000003})
        tracker = Tracker.objects.create(
            name="Low Sec Only",
            exclude_high_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
        )

        # run first
        results = tracker.calculate_killmails()
        self.assertSetEqual(results, {10000001})

        # run second
        results = tracker.calculate_killmails()
        self.assertSetEqual(results, set())


@patch(MODULE_PATH + ".dhooks_lite.Webhook.execute")
@patch(MODULE_PATH + ".sleep", new=lambda x: None)
@patch("eveuniverse.models.cache", new=CacheStub())
class TestTrackerSendMatching(TestCaseBase):
    def setUp(self) -> None:
        load_killmails({10000001, 10000002, 10000003})
        self.tracker_1 = Tracker.objects.create(
            name="Low Sec Only",
            exclude_high_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=self.webhook_1,
        )
        self.tracker_2 = Tracker.objects.create(
            name="High Sec Only",
            exclude_low_sec=True,
            exclude_null_sec=True,
            exclude_w_space=True,
            webhook=self.webhook_1,
        )
        self.tracker_1.calculate_killmails()
        self.tracker_2.calculate_killmails()

    def test_log_warning_when_no_webhook_configured(self, mock_execute):
        tracker = Tracker.objects.create(name="Missing webhook")
        tracker.webhook = None
        tracker.save()
        tracker.calculate_killmails()
        result = tracker.send_matching_to_webhook()
        self.assertEqual(result, 0)

    def test_normal(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)

        self.tracker_1.send_matching_to_webhook()

        self.assertEqual(mock_execute.call_count, 1)
        sent_qs = TrackedKillmail.objects.exclude(date_sent__isnull=True)
        self.assertEqual(sent_qs.count(), 1)
        self.assertEqual(sent_qs.first().killmail_id, 10000001)

    def test_dont_send_to_disabeled_webhook(self, mock_execute):
        tracker = Tracker.objects.create(
            name="Disabled webhook", webhook=self.webhook_2
        )
        tracker.calculate_killmails()
        result = tracker.send_matching_to_webhook()
        self.assertEqual(result, 0)
