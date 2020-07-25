from datetime import timedelta
from unittest.mock import Mock, patch
from bravado.exception import HTTPNotFound

import dhooks_lite

from django.utils.timezone import now

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo

from eveuniverse.models import (
    EveConstellation,
    EveEntity,
    EveGroup,
    EveRegion,
    EveSolarSystem,
    EveType,
)
from killtracker.core.killmails import EntityCount

from . import BravadoOperationStub
from ..models import EveKillmail, Tracker, Webhook
from .testdata.helpers import (
    load_eveuniverse,
    load_eveentities,
    load_evealliances,
    load_evecorporations,
    load_killmail,
    load_eve_killmails,
)
from ..utils import NoSocketsTestCase, set_test_logger


MODULE_PATH = "killtracker.models"
logger = set_test_logger(MODULE_PATH, __file__)


def esi_get_route_origin_destination(origin, destination, **kwargs) -> list:
    routes = {
        30003067: {
            30003087: [
                30003067,
                30003068,
                30003069,
                30003070,
                30003071,
                30003091,
                30003086,
                30003087,
            ],
            30003070: [30003067, 30003068, 30003069, 30003070],
            30003067: [30003067],
        },
    }
    if origin in routes and destination in routes[origin]:
        return BravadoOperationStub(routes[origin][destination])
    else:
        raise HTTPNotFound(Mock(**{"response.status_code": 404}))


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


class TestEveKillmailManager(TestCaseBase):
    def test_create_from_killmail(self):
        killmail = load_killmail(10000001)
        eve_killmail = EveKillmail.objects.create_from_killmail(killmail)

        self.assertIsInstance(eve_killmail, EveKillmail)
        self.assertEqual(eve_killmail.id, 10000001)
        self.assertEqual(eve_killmail.solar_system_id, 30004984)
        self.assertAlmostEqual(eve_killmail.time, now(), delta=timedelta(seconds=30))

        self.assertEqual(eve_killmail.victim.alliance_id, 3011)
        self.assertEqual(eve_killmail.victim.character_id, 1011)
        self.assertEqual(eve_killmail.victim.corporation_id, 2011)
        self.assertEqual(eve_killmail.victim.damage_taken, 434)
        self.assertEqual(eve_killmail.victim.ship_type_id, 603)

        self.assertEqual(eve_killmail.attackers.count(), 3)

        attacker_1 = eve_killmail.attackers.first()
        self.assertEqual(attacker_1.alliance_id, 3001)
        self.assertEqual(attacker_1.character_id, 1001)
        self.assertEqual(attacker_1.corporation_id, 2001)
        self.assertEqual(attacker_1.damage_done, 434)
        self.assertEqual(attacker_1.security_status, -10)
        self.assertEqual(attacker_1.ship_type_id, 34562)
        self.assertEqual(attacker_1.weapon_type_id, 2977)

        self.assertEqual(eve_killmail.zkb.location_id, 50012306)
        self.assertEqual(eve_killmail.zkb.fitted_value, 10000)
        self.assertEqual(eve_killmail.zkb.total_value, 10000)
        self.assertEqual(eve_killmail.zkb.points, 1)
        self.assertFalse(eve_killmail.zkb.is_npc)
        self.assertFalse(eve_killmail.zkb.is_solo)
        self.assertFalse(eve_killmail.zkb.is_awox)

    def test_update_or_create_from_killmail(self):
        killmail = load_killmail(10000001)

        # first time will be created
        eve_killmail, created = EveKillmail.objects.update_or_create_from_killmail(
            killmail
        )
        self.assertTrue(created)
        self.assertEqual(eve_killmail.solar_system_id, 30004984)

        # update record
        eve_killmail.solar_system = EveEntity.objects.get(id=30045349)
        eve_killmail.save()
        eve_killmail.refresh_from_db()
        self.assertEqual(eve_killmail.solar_system_id, 30045349)

        # 2nd time will be updated
        eve_killmail, created = EveKillmail.objects.update_or_create_from_killmail(
            killmail
        )
        self.assertEqual(eve_killmail.id, 10000001)
        self.assertFalse(created)
        self.assertEqual(eve_killmail.solar_system_id, 30004984)

    @patch("killtracker.managers.KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS", 1)
    def test_delete_stale(self):
        load_eve_killmails([10000001, 10000002, 10000003])
        km = EveKillmail.objects.get(id=10000001)
        km.time = now() - timedelta(days=1, seconds=1)
        km.save()

        total_count, details = EveKillmail.objects.delete_stale()

        self.assertEqual(details["killtracker.EveKillmail"], 1)
        self.assertEqual(EveKillmail.objects.count(), 2)
        self.assertTrue(EveKillmail.objects.filter(id=10000002).exists())
        self.assertTrue(EveKillmail.objects.filter(id=10000003).exists())


class TestTrackerCalculate(TestCaseBase):
    @staticmethod
    def _calculate_results(tracker: Tracker, killmail_ids: set) -> set:
        results = set()
        for killmail_id in killmail_ids:
            killmail = load_killmail(killmail_id)
            if tracker.process_killmail(killmail):
                results.add(killmail_id)

        return results

    def test_can_match_all(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000003, 10000004, 10000005}
        self.assertSetEqual(results, expected)

    @patch("eveuniverse.models.esi")
    def test_returns_augmented_killmail(self, mock_esi):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )

        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, origin_solar_system_id=30003067
        )
        killmail = load_killmail(10000101)
        killmail_plus = tracker.process_killmail(killmail)
        self.assertTrue(killmail_plus.tracker_info)
        self.assertEqual(killmail_plus.tracker_info.tracker_pk, tracker.pk)
        self.assertEqual(killmail_plus.tracker_info.jumps, 7)
        self.assertAlmostEqual(killmail_plus.tracker_info.distance, 5.85, delta=0.01)
        self.assertEqual(
            killmail_plus.tracker_info.main_org,
            EntityCount(id=3001, category=EntityCount.CATEGORY_ALLIANCE, count=3),
        )
        self.assertEqual(
            killmail_plus.tracker_info.main_ship_group,
            EntityCount(
                id=419,
                category=EntityCount.CATEGORY_INVENTORY_GROUP,
                name="Combat Battlecruiser",
                count=2,
            ),
        )

    @patch(MODULE_PATH + ".KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER", 60)
    def test_excludes_older_killmails(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1,)
        killmail_1 = load_killmail(10000001)
        killmail_2 = load_killmail(10000002)
        killmail_2.time = now() - timedelta(hours=1, seconds=1)
        results = set()
        for killmail in [killmail_1, killmail_2]:
            if tracker.process_killmail(killmail):
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
            name="Test", require_min_value=1000, webhook=self.webhook_1
        )
        results = self._calculate_results(tracker, killmail_ids)
        expected = {10000004}
        self.assertSetEqual(results, expected)

    @patch("eveuniverse.models.esi")
    def test_can_filter_max_jumps(self, mock_esi):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )

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

    @patch("eveuniverse.models.esi")
    def test_can_filter_max_distance(self, mock_esi):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )

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

        killmail = tracker.process_killmail(load_killmail(10000101))
        self.assertSetEqual(killmail.tracker_info.matching_ship_type_ids, {34562})

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

        killmail = tracker.process_killmail(load_killmail(10000101))
        self.assertSetEqual(killmail.tracker_info.matching_ship_type_ids, {34562})

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


@patch(MODULE_PATH + ".dhooks_lite.Webhook.execute")
class TestWebhookSendKillmail(TestCaseBase):
    @patch("eveuniverse.models.esi")
    def test_normal(self, mock_esi, mock_execute):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)

        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, origin_solar_system_id=30003067,
        )
        svipul = EveType.objects.get(id=34562)
        tracker.require_attackers_ship_types.add(svipul)
        gnosis = EveType.objects.get(id=3756)
        tracker.require_attackers_ship_types.add(gnosis)
        killmail = load_killmail(10000101)
        killmail_plus = tracker.process_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        self.assertTrue(mock_execute.called, True)
        args, kwargs = mock_execute.call_args
        self.assertEqual(kwargs["username"], "Killtracker")
        self.assertIn("Test", kwargs["content"])
        embed = kwargs["embeds"][0]
        self.assertIn("Combat Battlecruiser", embed.description)
        self.assertIn("Tracked ship types", embed.description)

    def test_without_tracker_info(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        killmail = load_killmail(10000001)

        self.webhook_1.send_killmail(killmail)

        self.assertTrue(mock_execute.called, True)

    def test_can_ping_everybody(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, ping_type=Tracker.PING_TYPE_EVERYBODY,
        )

        killmail = load_killmail(10000001)
        killmail_plus = tracker.process_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertIn("@everybody", kwargs["content"])

    def test_can_ping_here(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, ping_type=Tracker.PING_TYPE_HERE,
        )

        killmail = load_killmail(10000001)
        killmail_plus = tracker.process_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertIn("@here", kwargs["content"])

    def test_can_ping_nobody(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, ping_type=Tracker.PING_TYPE_NONE,
        )

        killmail = load_killmail(10000001)
        killmail_plus = tracker.process_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertNotIn("@everybody", kwargs["content"])
        self.assertNotIn("@here", kwargs["content"])

    def test_can_disable_posting_name(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Ping Nobody", webhook=self.webhook_1, is_posting_name=False,
        )

        killmail = load_killmail(10000001)
        killmail_plus = tracker.process_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        _, kwargs = mock_execute.call_args
        self.assertNotIn("Ping Nobody", kwargs["content"])

    def test_can_send_npc_killmail(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)

        killmail = load_killmail(10000301)
        killmail_plus = tracker.process_killmail(killmail)

        self.webhook_1.send_killmail(killmail_plus)

        self.assertTrue(mock_execute.called, True)

    def test_when_send_ok_returns_true(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)

        killmail = load_killmail(10000001)
        killmail_plus = tracker.process_killmail(killmail)

        result = self.webhook_1.send_killmail(killmail_plus)

        self.assertTrue(result)
        self.assertTrue(mock_execute.called, True)

    def test_when_send_not_ok_returns_false(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=404)
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)

        killmail = load_killmail(10000001)
        killmail_plus = tracker.process_killmail(killmail)

        result = self.webhook_1.send_killmail(killmail_plus)

        self.assertFalse(result)
        self.assertTrue(mock_execute.called, True)
