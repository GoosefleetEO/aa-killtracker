from datetime import timedelta
from unittest.mock import patch

import dhooks_lite

from django.utils.timezone import now

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo

from eveuniverse.models import (
    EveConstellation,
    EveGroup,
    EveRegion,
    EveSolarSystem,
    EveType,
)

from . import ResponseStub
from ..models import Killmail, Tracker, Webhook
from .testdata.helpers import (
    load_eveuniverse,
    load_eveentities,
    load_evealliances,
    load_evecorporations,
    killmails_data,
    load_temp_killmail,
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


class TestCaseBase(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        load_eveuniverse()
        load_evealliances()
        load_evecorporations()
        load_eveentities()
        cls.webhook_1 = Webhook.objects.create(
            name="Webhook 1", url="http://www.example.com/webhook_1", is_enabled=True
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
    @staticmethod
    def _calculate_results(tracker: Tracker, killmail_ids: set) -> set:
        results = set()
        for killmail_id in killmail_ids:
            killmail = load_temp_killmail(killmail_id)
            if tracker.calculate_killmail(killmail):
                results.add(killmail_id)

        return results

    def test_can_match_all(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000003, 10000004, 10000005}
        self.assertSetEqual(results, expected)

    def test_returns_augmented_killmail(self):
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, origin_solar_system_id=30003067
        )
        killmail = load_temp_killmail(10000101)
        killmail_plus = tracker.calculate_killmail(killmail)
        self.assertTrue(killmail_plus.tracker_info)
        self.assertEqual(killmail_plus.tracker_info.tracker_pk, tracker.pk)
        self.assertEqual(killmail_plus.tracker_info.jumps, 7)
        self.assertAlmostEqual(killmail_plus.tracker_info.distance, 5.85, delta=0.01)

    @patch(MODULE_PATH + ".KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER", 1)
    def test_excludes_older_killmails(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1,)
        killmail_1 = load_temp_killmail(10000001)
        killmail_2 = load_temp_killmail(10000002)
        killmail_2.time = now() - timedelta(hours=1, seconds=1)
        results = set()
        for killmail in [killmail_1, killmail_2]:
            if tracker.calculate_killmail(killmail):
                results.add(killmail.id)

        expected = {10000001}
        self.assertSetEqual(results, expected)

    def test_can_filter_high_sec_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", exclude_high_sec=True, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001, 10000003, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_low_sec_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", exclude_low_sec=True, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000002, 10000003, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_null_sec_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", exclude_null_sec=True, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_w_space_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", exclude_w_space=True, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000003}
        self.assertSetEqual(results, expected)

    def test_can_filter_min_attackers(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", require_min_attackers=3, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001}
        self.assertSetEqual(results, expected)

    def test_can_filter_max_attackers(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", require_max_attackers=2, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000002, 10000003, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_min_value(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", require_min_value=1000000000, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_max_distance(self):
        killmail_ids = {10000101, 10000102, 10000103}
        tracker = Tracker.objects.create(
            name="Test",
            origin_solar_system_id=30003067,
            require_max_distance=2,
            webhook=self.webhook_1,
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000102, 10000103}
        self.assertSetEqual(results, expected)

    def test_can_filter_max_jumps(self):
        killmail_ids = {10000101, 10000102, 10000103}
        tracker = Tracker.objects.create(
            name="Test",
            origin_solar_system_id=30003067,
            require_max_jumps=3,
            webhook=self.webhook_1,
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000102, 10000103}
        self.assertSetEqual(results, expected)

    def test_can_filter_attacker_alliance(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.exclude_attacker_alliances.add(
            EveAllianceInfo.objects.get(alliance_id=3001)
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_filter_attacker_corporation(self):
        killmail_ids = {10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.exclude_attacker_corporations.add(
            EveCorporationInfo.objects.get(corporation_id=2001)
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_attacker_alliance(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_attacker_alliances.add(
            EveAllianceInfo.objects.get(alliance_id=3011)
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_attacker_corporation(self):
        killmail_ids = {10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_attacker_corporations.add(
            EveCorporationInfo.objects.get(corporation_id=2011)
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_victim_alliances(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_victim_alliances.add(
            EveAllianceInfo.objects.get(alliance_id=3001)
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_victim_corporation(self):
        killmail_ids = {10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_victim_corporations.add(
            EveCorporationInfo.objects.get(corporation_id=2001)
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_filter_nullsec_and_attacker_alliance(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(
            name="Test", exclude_null_sec=True, webhook=self.webhook_1
        )
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.require_attacker_alliances.add(excluded_alliance)
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_require_region(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_regions.add(EveRegion.objects.get(id=10000014))
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000003}
        self.assertSetEqual(results, expected)

    def test_can_require_constellation(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_constellations.add(EveConstellation.objects.get(id=20000169))
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000003}
        self.assertSetEqual(results, expected)

    def test_can_require_solar_system(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_solar_systems.add(EveSolarSystem.objects.get(id=30001161))
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000003}
        self.assertSetEqual(results, expected)

    def test_can_require_attackers_ship_groups(self):
        killmail_ids = {10000101, 10000201}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        frigate = EveGroup.objects.get(id=25)
        td3s = EveGroup.objects.get(id=1305)
        tracker.require_attackers_ship_groups.add(frigate)
        tracker.require_attackers_ship_groups.add(td3s)
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000101}
        self.assertSetEqual(results, expected)

    def test_can_require_victim_ship_group(self):
        killmail_ids = {10000101, 10000201}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        td3s = EveGroup.objects.get(id=1305)
        tracker.require_victim_ship_groups.add(td3s)
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000101}
        self.assertSetEqual(results, expected)

    def test_can_require_attackers_ship_types(self):
        """
        when filtering for attackers with ship groups of Frigate, TD3
        then tracker finds killmail that has attacker with TD3 and no attacker with frigate
        and ignores killmail that attackers with neither
        """
        killmail_ids = {10000101, 10000201}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        svipul = EveType.objects.get(id=34562)
        tracker.require_attackers_ship_types.add(svipul)
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000101}
        self.assertSetEqual(results, expected)

    def test_can_exclude_npc_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005, 10000301}
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, exclude_npc_kills=True
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000003, 10000004, 10000005}
        self.assertSetEqual(results, expected)

    def test_can_require_npc_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005, 10000301}
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, require_npc_kills=True
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000301}
        self.assertSetEqual(results, expected)


@patch("eveuniverse.models.cache", new=CacheStub())
@patch(MODULE_PATH + ".dhooks_lite.Webhook.execute")
class TestWebhookSendKillmail(TestCaseBase):
    def test_normal(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1,)
        killmail = load_temp_killmail(10000001)
        killmail_plus = tracker.calculate_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        self.assertTrue(mock_execute.called, True)

    def test_without_tracker_info(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        killmail = load_temp_killmail(10000001)

        self.webhook_1.send_killmail(killmail)

        self.assertTrue(mock_execute.called, True)

    def test_can_ping_everybody(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, ping_type=Tracker.PING_TYPE_EVERYBODY,
        )

        killmail = load_temp_killmail(10000001)
        killmail_plus = tracker.calculate_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertIn("@everybody", kwargs["content"])

    def test_can_ping_here(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, ping_type=Tracker.PING_TYPE_HERE,
        )

        killmail = load_temp_killmail(10000001)
        killmail_plus = tracker.calculate_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertIn("@here", kwargs["content"])

    def test_can_ping_nobody(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, ping_type=Tracker.PING_TYPE_NONE,
        )

        killmail = load_temp_killmail(10000001)
        killmail_plus = tracker.calculate_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertNotIn("@everybody", kwargs["content"])
        self.assertNotIn("@here", kwargs["content"])

    def test_can_disable_posting_name(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Ping Nobody", webhook=self.webhook_1, is_posting_name=False,
        )

        killmail = load_temp_killmail(10000001)
        killmail_plus = tracker.calculate_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertNotIn("Ping Nobody", kwargs["content"])

    def test_can_send_npc_killmail(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)

        killmail = load_temp_killmail(10000301)
        killmail_plus = tracker.calculate_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        self.assertTrue(mock_execute.called, True)

    def test_when_send_ok_returns_true(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)

        killmail = load_temp_killmail(10000001)
        killmail_plus = tracker.calculate_killmail(killmail)

        result = self.webhook_1.send_killmail(killmail_plus)

        self.assertTrue(result)
        self.assertTrue(mock_execute.called, True)

    def test_when_send_not_ok_returns_false(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=404)
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)

        killmail = load_temp_killmail(10000001)
        killmail_plus = tracker.calculate_killmail(killmail)

        result = self.webhook_1.send_killmail(killmail_plus)

        self.assertFalse(result)
        self.assertTrue(mock_execute.called, True)
