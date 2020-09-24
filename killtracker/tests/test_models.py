from datetime import timedelta
from unittest.mock import Mock, patch
from bravado.exception import HTTPNotFound

import dhooks_lite

from django.core.cache import cache
from django.test import TestCase
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
from ..core.killmails import Killmail
from ..models import EveKillmail, EveKillmailCharacter, Tracker, Webhook
from .testdata.helpers import load_killmail, load_eve_killmails, LoadTestDataMixin
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


class TestWebhookQueue(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_queue_features(self):
        self.webhook_1.add_killmail_to_queue(load_killmail(10000001))
        self.assertEqual(self.webhook_1.queue_size(), 1)
        self.webhook_1.clear_queue()
        self.assertEqual(self.webhook_1.queue_size(), 0)


class TestEveKillmailManager(LoadTestDataMixin, NoSocketsTestCase):
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

        _, details = EveKillmail.objects.delete_stale()

        self.assertEqual(details["killtracker.EveKillmail"], 1)
        self.assertEqual(EveKillmail.objects.count(), 2)
        self.assertTrue(EveKillmail.objects.filter(id=10000002).exists())
        self.assertTrue(EveKillmail.objects.filter(id=10000003).exists())

    @patch("killtracker.managers.KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS", 0)
    def test_dont_delete_stale_when_turned_off(self):
        load_eve_killmails([10000001, 10000002, 10000003])
        km = EveKillmail.objects.get(id=10000001)
        km.time = now() - timedelta(days=1, seconds=1)
        km.save()

        self.assertIsNone(EveKillmail.objects.delete_stale())
        self.assertEqual(EveKillmail.objects.count(), 3)

    def test_load_entities(self):
        load_eve_killmails([10000001, 10000002])
        self.assertEqual(EveKillmail.objects.all().load_entities(), 0)


class TestHasLocalizationClause(LoadTestDataMixin, NoSocketsTestCase):
    def test_has_localization_filter_1(self):
        tracker = Tracker(name="Test", webhook=self.webhook_1, exclude_high_sec=True)
        self.assertTrue(tracker.has_localization_clause)

        tracker = Tracker(name="Test", webhook=self.webhook_1, exclude_low_sec=True)
        self.assertTrue(tracker.has_localization_clause)

        tracker = Tracker(name="Test", webhook=self.webhook_1, exclude_null_sec=True)
        self.assertTrue(tracker.has_localization_clause)

        tracker = Tracker(name="Test", webhook=self.webhook_1, exclude_w_space=True)
        self.assertTrue(tracker.has_localization_clause)

        tracker = Tracker(name="Test", webhook=self.webhook_1, require_max_distance=10)
        self.assertTrue(tracker.has_localization_clause)

        tracker = Tracker(name="Test", webhook=self.webhook_1, require_max_jumps=10)
        self.assertTrue(tracker.has_localization_clause)

    def test_has_no_matching_clause(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        self.assertFalse(tracker.has_localization_clause)

    def test_has_localization_filter_3(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_regions.add(EveRegion.objects.get(id=10000014))
        self.assertTrue(tracker.has_localization_clause)

    def test_has_localization_filter_4(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_constellations.add(EveConstellation.objects.get(id=20000169))
        self.assertTrue(tracker.has_localization_clause)

    def test_has_localization_filter_5(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_solar_systems.add(EveSolarSystem.objects.get(id=30001161))
        self.assertTrue(tracker.has_localization_clause)


class TestHasTypeClause(LoadTestDataMixin, NoSocketsTestCase):
    def test_has_no_matching_clause(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        self.assertFalse(tracker.has_type_clause)

    def test_has_require_attackers_ship_groups(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_attackers_ship_groups.add(self.type_svipul.eve_group)
        self.assertTrue(tracker.has_type_clause)

    def test_has_require_attackers_ship_types(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_attackers_ship_types.add(self.type_svipul)
        self.assertTrue(tracker.has_type_clause)

    def test_has_require_victim_ship_groups(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_victim_ship_groups.add(self.type_svipul.eve_group)
        self.assertTrue(tracker.has_type_clause)


class TestTrackerCalculate(LoadTestDataMixin, NoSocketsTestCase):
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

    @patch(MODULE_PATH + ".KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER", 60)
    def test_excludes_older_killmails(self):
        tracker = Tracker.objects.create(
            name="Test",
            webhook=self.webhook_1,
        )
        killmail_1 = load_killmail(10000001)
        killmail_2 = load_killmail(10000002)
        killmail_2.time = now() - timedelta(hours=1, seconds=1)
        results = set()
        for killmail in [killmail_1, killmail_2]:
            if tracker.process_killmail(killmail):
                results.add(killmail.id)

        expected = {10000001}
        self.assertSetEqual(results, expected)

    def test_can_process_killmail_without_solar_system(self):
        tracker = Tracker.objects.create(
            name="Test", exclude_high_sec=True, webhook=self.webhook_1
        )
        self.assertIsNotNone(tracker.process_killmail(load_killmail(10000402)))

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
        self.assertSetEqual(set(killmail.tracker_info.matching_ship_type_ids), {34562})

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
        self.assertSetEqual(set(killmail.tracker_info.matching_ship_type_ids), {34562})

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


class TestTrackerCalculateTrackerInfo(LoadTestDataMixin, NoSocketsTestCase):
    def setUp(self) -> None:
        self.tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)

    @patch("eveuniverse.models.esi")
    def test_basics(self, mock_esi):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )
        self.tracker.origin_solar_system_id = 30003067
        self.tracker.save()

        killmail = self.tracker.process_killmail(load_killmail(10000101))

        self.assertTrue(killmail.tracker_info)
        self.assertEqual(killmail.tracker_info.tracker_pk, self.tracker.pk)
        self.assertEqual(killmail.tracker_info.jumps, 7)
        self.assertAlmostEqual(killmail.tracker_info.distance, 5.85, delta=0.01)
        self.assertEqual(
            killmail.tracker_info.main_org,
            EntityCount(id=3001, category=EntityCount.CATEGORY_ALLIANCE, count=3),
        )
        self.assertEqual(
            killmail.tracker_info.main_ship_group,
            EntityCount(
                id=419,
                category=EntityCount.CATEGORY_INVENTORY_GROUP,
                name="Combat Battlecruiser",
                count=2,
            ),
        )

    def test_main_org_corporation_is_main(self):
        killmail = self.tracker.process_killmail(load_killmail(10000403))
        self.assertEqual(
            killmail.tracker_info.main_org,
            EntityCount(id=2001, category=EntityCount.CATEGORY_CORPORATION, count=2),
        )

    def test_main_org_prioritize_alliance_over_corporation(self):
        killmail = self.tracker.process_killmail(load_killmail(10000401))
        self.assertEqual(
            killmail.tracker_info.main_org,
            EntityCount(id=3001, category=EntityCount.CATEGORY_ALLIANCE, count=2),
        )

    def test_main_org_is_none_if_only_one_attacker(self):
        killmail = self.tracker.process_killmail(load_killmail(10000005))
        self.assertIsNone(killmail.tracker_info.main_org)

    def test_main_org_is_none_if_faction(self):
        killmail = self.tracker.process_killmail(load_killmail(10000302))
        self.assertIsNone(killmail.tracker_info.main_org)

    def test_main_ship_group_above_threshold(self):
        killmail = self.tracker.process_killmail(load_killmail(10006001))
        self.assertEqual(
            killmail.tracker_info.main_ship_group,
            EntityCount(
                id=419, category="inventory_group", name="Combat Battlecruiser", count=2
            ),
        )

    def test_main_ship_group_return_none_if_below_threshold(self):
        killmail = self.tracker.process_killmail(load_killmail(10006002))
        self.assertIsNone(killmail.tracker_info.main_ship_group)

    def test_main_org_above_threshold(self):
        killmail = self.tracker.process_killmail(load_killmail(10006003))
        self.assertEqual(
            killmail.tracker_info.main_org,
            EntityCount(id=2001, category="corporation", count=2),
        )

    def test_main_org_return_none_if_below_threshold(self):
        killmail = self.tracker.process_killmail(load_killmail(10006004))
        self.assertIsNone(killmail.tracker_info.main_org)


@patch(MODULE_PATH + ".sleep", new=lambda x: x)
@patch(MODULE_PATH + ".Webhook.send_killmail")
class TestWebhookSendQueuedMessages(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        self.webhook = Webhook.objects.create(
            name="Dummy", url="http://www.example.com"
        )
        self.webhook.clear_queue()

    def test_one_message(self, mock_send_killmail):
        """
        when one mesage in queue
        then send it and returns 1
        """
        mock_send_killmail.return_value = True
        self.webhook.add_killmail_to_queue(load_killmail(10000001))

        result = self.webhook.send_queued_killmails()

        self.assertEqual(result, 1)
        self.assertTrue(mock_send_killmail.called)
        self.assertEqual(self.webhook.queue_size(), 0)

    def test_three_message(self, mock_send_killmail):
        """
        when three mesages in queue
        then sends them and returns 3
        """
        mock_send_killmail.return_value = True
        self.webhook.add_killmail_to_queue(load_killmail(10000001))
        self.webhook.add_killmail_to_queue(load_killmail(10000002))
        self.webhook.add_killmail_to_queue(load_killmail(10000003))

        result = self.webhook.send_queued_killmails()

        self.assertEqual(result, 3)
        self.assertEqual(mock_send_killmail.call_count, 3)
        self.assertEqual(self.webhook.queue_size(), 0)

    def test_no_messages(self, mock_send_killmail):
        """
        when no message in queue
        then do nothing and return 0
        """
        mock_send_killmail.return_value = True
        result = self.webhook.send_queued_killmails()

        self.assertEqual(result, 0)
        self.assertFalse(mock_send_killmail.called)
        self.assertEqual(self.webhook.queue_size(), 0)

    def test_failed_message(self, mock_send_killmail):
        """
        given one message in queue
        when sending fails
        then re-queues message and return 0
        """
        mock_send_killmail.return_value = False
        self.webhook.add_killmail_to_queue(load_killmail(10000001))

        result = self.webhook.send_queued_killmails()

        self.assertEqual(result, 0)
        self.assertTrue(mock_send_killmail.called)
        self.assertEqual(self.webhook.queue_size(), 1)


@patch(MODULE_PATH + ".dhooks_lite.Webhook.execute")
class TestWebhookSendKillmail(LoadTestDataMixin, NoSocketsTestCase):
    def setUp(self) -> None:
        self.tracker = Tracker.objects.create(name="My Tracker", webhook=self.webhook_1)

    @patch("eveuniverse.models.esi")
    def test_normal(self, mock_esi, mock_execute):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)

        self.tracker.origin_solar_system_id = 30003067
        self.tracker.save()
        svipul = EveType.objects.get(id=34562)
        self.tracker.require_attackers_ship_types.add(svipul)
        gnosis = EveType.objects.get(id=3756)
        self.tracker.require_attackers_ship_types.add(gnosis)
        killmail = self.tracker.process_killmail(load_killmail(10000101))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)
        _, kwargs = mock_execute.call_args
        self.assertEqual(kwargs["username"], "Killtracker")
        self.assertIn("My Tracker", kwargs["content"])
        embed = kwargs["embeds"][0]
        self.assertIn("| Killmail", embed.title)
        self.assertIn("Combat Battlecruiser", embed.description)
        self.assertIn("Tracked ship types", embed.description)

    def test_send_as_fleetkill(self, mock_execute):
        self.tracker.identify_fleets = True
        self.tracker.save()

        killmail = self.tracker.process_killmail(load_killmail(10000101))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)
        _, kwargs = mock_execute.call_args
        embed = kwargs["embeds"][0]
        self.assertIn("| Fleetkill", embed.title)

    def test_can_add_intro_text(self, mock_execute):
        killmail = self.tracker.process_killmail(load_killmail(10000101))
        self.webhook_1.send_killmail(killmail, intro_text="Intro Text")

        self.assertTrue(mock_execute.called, True)
        _, kwargs = mock_execute.call_args
        self.assertIn("Intro Text", kwargs["content"])

    def test_without_tracker_info(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        killmail = load_killmail(10000001)

        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)

    def test_can_ping_everybody(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        tracker = Tracker.objects.create(
            name="Test",
            webhook=self.webhook_1,
            ping_type=Tracker.PING_TYPE_EVERYBODY,
        )
        killmail = tracker.process_killmail(load_killmail(10000001))

        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        _, kwargs = mock_execute.call_args
        self.assertIn("@everybody", kwargs["content"])

    def test_can_ping_here(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        self.tracker.ping_type = Tracker.PING_TYPE_HERE
        self.tracker.save()

        killmail = self.tracker.process_killmail(load_killmail(10000001))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        _, kwargs = mock_execute.call_args
        self.assertIn("@here", kwargs["content"])

    def test_can_ping_nobody(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        self.tracker.ping_type = Tracker.PING_TYPE_NONE
        self.tracker.save()

        killmail = self.tracker.process_killmail(load_killmail(10000001))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        _, kwargs = mock_execute.call_args
        self.assertNotIn("@everybody", kwargs["content"])
        self.assertNotIn("@here", kwargs["content"])

    def test_can_disable_posting_name(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        self.tracker.s_posting_name = False
        self.tracker.save()

        killmail = self.tracker.process_killmail(load_killmail(10000001))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        _, kwargs = mock_execute.call_args
        self.assertNotIn("Ping Nobody", kwargs["content"])

    def test_can_send_npc_killmail(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        killmail = self.tracker.process_killmail(load_killmail(10000301))

        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)

    def test_when_send_ok_returns_true(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)
        killmail = self.tracker.process_killmail(load_killmail(10000001))

        result = self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(result)
        self.assertTrue(mock_execute.called, True)

    def test_when_send_not_ok_returns_false(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=404)
        killmail = self.tracker.process_killmail(load_killmail(10000001))

        result = self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertFalse(result)
        self.assertTrue(mock_execute.called, True)

    @patch(MODULE_PATH + ".Killmail.create_from_zkb_api")
    def test_can_send_test_message(self, mock_execute, mock_create_from_zkb_api):
        def my_create_from_zkb_api(killmail_id):
            return load_killmail(killmail_id)

        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=404)
        mock_create_from_zkb_api.side_effect = my_create_from_zkb_api

        self.webhook_1.send_test_message(killmail_id=10000001)
        self.assertTrue(mock_execute.called, True)

    def test_can_handle_victim_without_character(self, mock_execute):
        killmail = self.tracker.process_killmail(load_killmail(10000501))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)

    def test_can_handle_victim_without_corporation(self, mock_execute):
        killmail = self.tracker.process_killmail(load_killmail(10000502))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)

    def test_can_handle_final_attacker_with_no_character(self, mock_execute):
        killmail = self.tracker.process_killmail(load_killmail(10000503))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)

    def test_can_handle_matching_type_ids(self, mock_execute):
        svipul = EveType.objects.get(id=34562)
        self.tracker.require_attackers_ship_types.add(svipul)
        killmail = self.tracker.process_killmail(load_killmail(10000001))
        self.webhook_1.send_killmail(Killmail.from_json(killmail.asjson()))

        self.assertTrue(mock_execute.called, True)


class TestEveKillmailCharacter(LoadTestDataMixin, NoSocketsTestCase):
    def test_str_character(self):
        obj = EveKillmailCharacter(character=EveEntity.objects.get(id=1001))
        self.assertEqual(str(obj), "Bruce Wayne")

    def test_str_corporation(self):
        obj = EveKillmailCharacter(corporation=EveEntity.objects.get(id=2001))
        self.assertEqual(str(obj), "Wayne Technologies")

    def test_str_alliance(self):
        obj = EveKillmailCharacter(alliance=EveEntity.objects.get(id=3001))
        self.assertEqual(str(obj), "Wayne Enterprise")

    def test_str_faction(self):
        obj = EveKillmailCharacter(faction=EveEntity.objects.get(id=500001))
        self.assertEqual(str(obj), "Caldari State")
