from datetime import timedelta
import json
from unittest.mock import Mock, patch

from bravado.exception import HTTPNotFound

import dhooks_lite
from requests.exceptions import HTTPError

from django.core.cache import cache
from django.contrib.auth.models import Group
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
from ..exceptions import WebhookTooManyRequests
from ..models import EveKillmail, EveKillmailCharacter, Tracker, Webhook
from .testdata.helpers import load_killmail, load_eve_killmails, LoadTestDataMixin
from ..utils import app_labels, NoSocketsTestCase, set_test_logger, JSONDateTimeDecoder


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
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self) -> None:
        self.webhook_1.main_queue.clear()
        self.webhook_1.error_queue.clear()

    def test_reset_failed_messages(self):
        message = "Test message"
        self.webhook_1.error_queue.enqueue(message)
        self.webhook_1.error_queue.enqueue(message)
        self.assertEqual(self.webhook_1.error_queue.size(), 2)
        self.assertEqual(self.webhook_1.main_queue.size(), 0)
        self.webhook_1.reset_failed_messages()
        self.assertEqual(self.webhook_1.error_queue.size(), 0)
        self.assertEqual(self.webhook_1.main_queue.size(), 2)

    def test_discord_message_asjson_normal(self):
        embed = dhooks_lite.Embed(description="my_description")
        result = Webhook._discord_message_asjson(
            content="my_content",
            username="my_username",
            avatar_url="my_avatar_url",
            embeds=[embed],
        )
        message_python = json.loads(result, cls=JSONDateTimeDecoder)
        expected = {
            "content": "my_content",
            "embeds": [{"description": "my_description", "type": "rich"}],
            "username": "my_username",
            "avatar_url": "my_avatar_url",
        }
        self.assertDictEqual(message_python, expected)

    def test_discord_message_asjson_empty(self):
        with self.assertRaises(ValueError):
            Webhook._discord_message_asjson("")


class TestEveKillmailManager(LoadTestDataMixin, NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def test_create_from_killmail(self):
        killmail = load_killmail(10000001)
        eve_killmail = EveKillmail.objects.create_from_killmail(killmail)

        self.assertIsInstance(eve_killmail, EveKillmail)
        self.assertEqual(eve_killmail.id, 10000001)
        self.assertEqual(eve_killmail.solar_system, EveEntity.objects.get(id=30004984))
        self.assertAlmostEqual(eve_killmail.time, now(), delta=timedelta(seconds=60))

        self.assertEqual(eve_killmail.victim.alliance, EveEntity.objects.get(id=3011))
        self.assertEqual(eve_killmail.victim.character, EveEntity.objects.get(id=1011))
        self.assertEqual(
            eve_killmail.victim.corporation, EveEntity.objects.get(id=2011)
        )
        self.assertEqual(eve_killmail.victim.faction, EveEntity.objects.get(id=500004))
        self.assertEqual(eve_killmail.victim.damage_taken, 434)
        self.assertEqual(eve_killmail.victim.ship_type, EveEntity.objects.get(id=603))

        attacker_ids = list(eve_killmail.attackers.values_list("pk", flat=True))
        self.assertEqual(len(attacker_ids), 3)

        attacker = eve_killmail.attackers.get(pk=attacker_ids[0])
        self.assertEqual(attacker.alliance, EveEntity.objects.get(id=3001))
        self.assertEqual(attacker.character, EveEntity.objects.get(id=1001))
        self.assertEqual(attacker.corporation, EveEntity.objects.get(id=2001))
        self.assertEqual(attacker.faction, EveEntity.objects.get(id=500001))
        self.assertEqual(attacker.damage_done, 434)
        self.assertEqual(attacker.security_status, -10)
        self.assertEqual(attacker.ship_type, EveEntity.objects.get(id=34562))
        self.assertEqual(attacker.weapon_type, EveEntity.objects.get(id=2977))
        self.assertTrue(attacker.is_final_blow)

        attacker = eve_killmail.attackers.get(pk=attacker_ids[1])
        self.assertEqual(attacker.alliance, EveEntity.objects.get(id=3001))
        self.assertEqual(attacker.character, EveEntity.objects.get(id=1002))
        self.assertEqual(attacker.corporation, EveEntity.objects.get(id=2001))
        self.assertEqual(attacker.faction, EveEntity.objects.get(id=500001))
        self.assertEqual(attacker.damage_done, 50)
        self.assertEqual(attacker.security_status, -10)
        self.assertEqual(attacker.ship_type, EveEntity.objects.get(id=3756))
        self.assertEqual(attacker.weapon_type, EveEntity.objects.get(id=2488))
        self.assertFalse(attacker.is_final_blow)

        attacker = eve_killmail.attackers.get(pk=attacker_ids[2])
        self.assertEqual(attacker.alliance, EveEntity.objects.get(id=3001))
        self.assertEqual(attacker.character, EveEntity.objects.get(id=1003))
        self.assertEqual(attacker.corporation, EveEntity.objects.get(id=2001))
        self.assertEqual(attacker.faction, EveEntity.objects.get(id=500001))
        self.assertEqual(attacker.damage_done, 99)
        self.assertEqual(attacker.security_status, 5)
        self.assertEqual(attacker.ship_type, EveEntity.objects.get(id=3756))
        self.assertEqual(attacker.weapon_type, EveEntity.objects.get(id=2488))
        self.assertFalse(attacker.is_final_blow)

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

    def test_has_require_victim_ship_types(self):
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_victim_ship_types.add(self.type_svipul)
        self.assertTrue(tracker.has_type_clause)


class TestSaveMethod(LoadTestDataMixin, NoSocketsTestCase):
    def test_black_color_is_none(self):
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, color="#000000"
        )
        tracker.refresh_from_db()
        self.assertFalse(tracker.color)


class TestTrackerCalculate(LoadTestDataMixin, NoSocketsTestCase):
    @classmethod
    def _matching_killmail_ids(cls, tracker: Tracker, killmail_ids: set) -> set:
        return {
            killmail.id for killmail in cls._matching_killmails(tracker, killmail_ids)
        }

    @staticmethod
    def _matching_killmails(tracker: Tracker, killmail_ids: set) -> list:
        results = list()
        for killmail_id in killmail_ids:
            killmail = load_killmail(killmail_id)
            new_killmail = tracker.process_killmail(killmail)
            if new_killmail:
                results.append(new_killmail)
        return results

    def test_can_match_all(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        results = self._matching_killmail_ids(tracker, killmail_ids)
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
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000001, 10000003, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_low_sec_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", exclude_low_sec=True, webhook=self.webhook_1
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000002, 10000003, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_null_sec_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", exclude_null_sec=True, webhook=self.webhook_1
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_w_space_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", exclude_w_space=True, webhook=self.webhook_1
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000003}
        self.assertSetEqual(results, expected)

    def test_can_filter_min_attackers(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", require_min_attackers=3, webhook=self.webhook_1
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000001}
        self.assertSetEqual(results, expected)

    def test_can_filter_max_attackers(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", require_max_attackers=2, webhook=self.webhook_1
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000002, 10000003, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_filter_min_value(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(
            name="Test", require_min_value=1000, webhook=self.webhook_1
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
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
        results = self._matching_killmail_ids(tracker, killmail_ids)
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
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000102, 10000103}
        self.assertSetEqual(results, expected)

    def test_can_filter_attacker_alliance(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.exclude_attacker_alliances.add(
            EveAllianceInfo.objects.get(alliance_id=3001)
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_filter_attacker_corporation(self):
        killmail_ids = {10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.exclude_attacker_corporations.add(
            EveCorporationInfo.objects.get(corporation_id=2001)
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_attacker_alliance(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_attacker_alliances.add(
            EveAllianceInfo.objects.get(alliance_id=3011)
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_attacker_corporation(self):
        killmail_ids = {10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_attacker_corporations.add(
            EveCorporationInfo.objects.get(corporation_id=2011)
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_victim_alliances(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_victim_alliances.add(
            EveAllianceInfo.objects.get(alliance_id=3001)
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_required_victim_corporation(self):
        killmail_ids = {10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_victim_corporations.add(
            EveCorporationInfo.objects.get(corporation_id=2001)
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000005}
        self.assertSetEqual(results, expected)

    def test_can_filter_nullsec_and_attacker_alliance(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005}
        tracker = Tracker.objects.create(
            name="Test", exclude_null_sec=True, webhook=self.webhook_1
        )
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.require_attacker_alliances.add(excluded_alliance)
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000004}
        self.assertSetEqual(results, expected)

    def test_can_require_region(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_regions.add(EveRegion.objects.get(id=10000014))
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000003}
        self.assertSetEqual(results, expected)

    def test_can_require_constellation(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_constellations.add(EveConstellation.objects.get(id=20000169))
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000003}
        self.assertSetEqual(results, expected)

    def test_can_require_solar_system(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        tracker.require_solar_systems.add(EveSolarSystem.objects.get(id=30001161))
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000003}
        self.assertSetEqual(results, expected)

    def test_can_require_attackers_ship_groups(self):
        killmail_ids = {10000101, 10000201}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        frigate = EveGroup.objects.get(id=25)
        td3s = EveGroup.objects.get(id=1305)
        tracker.require_attackers_ship_groups.add(frigate)
        tracker.require_attackers_ship_groups.add(td3s)
        results = self._matching_killmails(tracker, killmail_ids)
        self.assertEqual(len(results), 1)
        killmail = results[0]
        self.assertEqual(killmail.id, 10000101)
        self.assertListEqual(killmail.tracker_info.matching_ship_type_ids, [34562])

    def test_can_require_victim_ship_group(self):
        killmail_ids = {10000101, 10000201}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        td3s = EveGroup.objects.get(id=1305)
        tracker.require_victim_ship_groups.add(td3s)
        results = self._matching_killmails(tracker, killmail_ids)
        self.assertEqual(len(results), 1)
        killmail = results[0]
        self.assertEqual(killmail.id, 10000101)
        self.assertListEqual(killmail.tracker_info.matching_ship_type_ids, [34562])

    def test_can_require_victim_ship_types(self):
        killmail_ids = {10000101, 10000201}
        tracker = Tracker.objects.create(name="Test", webhook=self.webhook_1)
        svipul = EveType.objects.get(id=34562)
        tracker.require_victim_ship_types.add(svipul)
        results = self._matching_killmails(tracker, killmail_ids)
        self.assertEqual(len(results), 1)
        killmail = results[0]
        self.assertEqual(killmail.id, 10000101)
        self.assertListEqual(killmail.tracker_info.matching_ship_type_ids, [34562])

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
        results = self._matching_killmails(tracker, killmail_ids)
        self.assertEqual(len(results), 1)
        killmail = results[0]
        self.assertEqual(killmail.id, 10000101)
        self.assertListEqual(killmail.tracker_info.matching_ship_type_ids, [34562])

    def test_can_exclude_npc_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005, 10000301}
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, exclude_npc_kills=True
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
        expected = {10000001, 10000002, 10000003, 10000004, 10000005}
        self.assertSetEqual(results, expected)

    def test_can_require_npc_kills(self):
        killmail_ids = {10000001, 10000002, 10000003, 10000004, 10000005, 10000301}
        tracker = Tracker.objects.create(
            name="Test", webhook=self.webhook_1, require_npc_kills=True
        )
        results = self._matching_killmail_ids(tracker, killmail_ids)
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


class TestTrackerEnqueueKillmail(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        self.tracker = Tracker.objects.create(name="My Tracker", webhook=self.webhook_1)
        self.webhook_1.main_queue.clear()

    @patch(MODULE_PATH + ".KILLTRACKER_WEBHOOK_SET_AVATAR", True)
    @patch("eveuniverse.models.esi")
    def test_normal(self, mock_esi):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )
        self.tracker.origin_solar_system_id = 30003067
        self.tracker.save()
        svipul = EveType.objects.get(id=34562)
        self.tracker.require_attackers_ship_types.add(svipul)
        gnosis = EveType.objects.get(id=3756)
        self.tracker.require_attackers_ship_types.add(gnosis)
        killmail = self.tracker.process_killmail(load_killmail(10000101))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())

        self.assertEqual(message["username"], "Killtracker")
        self.assertIsNotNone(message["avatar_url"])
        self.assertIn("My Tracker", message["content"])
        embed = message["embeds"][0]
        self.assertIn("| Killmail", embed["title"])
        self.assertIn("Combat Battlecruiser", embed["description"])
        self.assertIn("Tracked ship types", embed["description"])

    @patch(MODULE_PATH + ".KILLTRACKER_WEBHOOK_SET_AVATAR", False)
    @patch("eveuniverse.models.esi")
    def test_disabled_avatar(self, mock_esi):
        mock_esi.client.Routes.get_route_origin_destination.side_effect = (
            esi_get_route_origin_destination
        )
        self.tracker.origin_solar_system_id = 30003067
        self.tracker.save()
        svipul = EveType.objects.get(id=34562)
        self.tracker.require_attackers_ship_types.add(svipul)
        gnosis = EveType.objects.get(id=3756)
        self.tracker.require_attackers_ship_types.add(gnosis)
        killmail = self.tracker.process_killmail(load_killmail(10000101))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())
        self.assertNotIn("username", message)
        self.assertNotIn("avatar_url", message)
        self.assertIn("My Tracker", message["content"])

    def test_send_as_fleetkill(self):
        self.tracker.identify_fleets = True
        self.tracker.save()
        killmail = self.tracker.process_killmail(load_killmail(10000101))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())
        self.assertIn("| Fleetkill", message["embeds"][0]["title"])

    def test_can_add_intro_text(self):
        killmail = self.tracker.process_killmail(load_killmail(10000101))

        self.tracker.generate_killmail_message(killmail, intro_text="Intro Text")

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())
        self.assertIn("Intro Text", message["content"])

    def test_without_tracker_info(self):
        killmail = load_killmail(10000001)

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)

    def test_can_ping_everybody(self):
        tracker = Tracker.objects.create(
            name="Test",
            webhook=self.webhook_1,
            ping_type=Tracker.ChannelPingType.EVERYBODY,
        )

        killmail = tracker.process_killmail(load_killmail(10000001))

        tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())
        self.assertIn("@everybody", message["content"])

    def test_can_ping_here(self):
        self.tracker.ping_type = Tracker.ChannelPingType.HERE
        self.tracker.save()

        killmail = self.tracker.process_killmail(load_killmail(10000001))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())
        self.assertIn("@here", message["content"])

    def test_can_ping_nobody(self):
        self.tracker.ping_type = Tracker.ChannelPingType.NONE
        self.tracker.save()
        killmail = self.tracker.process_killmail(load_killmail(10000001))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())
        self.assertNotIn("@everybody", message["content"])
        self.assertNotIn("@here", message["content"])

    def test_can_disable_posting_name(self):
        self.tracker.s_posting_name = False
        self.tracker.save()
        killmail = self.tracker.process_killmail(load_killmail(10000001))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)
        message = json.loads(self.webhook_1.main_queue.dequeue())
        self.assertNotIn("Ping Nobody", message["content"])

    def test_can_send_npc_killmail(self):
        killmail = self.tracker.process_killmail(load_killmail(10000301))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)

    def test_can_handle_victim_without_character(self):
        killmail = self.tracker.process_killmail(load_killmail(10000501))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)

    def test_can_handle_victim_without_corporation(self):
        killmail = self.tracker.process_killmail(load_killmail(10000502))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)

    def test_can_handle_final_attacker_with_no_character(self):
        killmail = self.tracker.process_killmail(load_killmail(10000503))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)

    def test_can_handle_matching_type_ids(self):
        svipul = EveType.objects.get(id=34562)
        self.tracker.require_attackers_ship_types.add(svipul)
        killmail = self.tracker.process_killmail(load_killmail(10000001))

        self.tracker.generate_killmail_message(Killmail.from_json(killmail.asjson()))

        self.assertEqual(self.webhook_1.main_queue.size(), 1)


@patch(MODULE_PATH + ".dhooks_lite.Webhook.execute")
class TestWebhookSendMessage(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        self.message = Webhook._discord_message_asjson(content="Test message")
        cache.delete(self.webhook_1._blocked_cache_key())

    def test_when_send_ok_returns_true(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=200)

        response = self.webhook_1.send_message_to_webhook(self.message)

        self.assertTrue(response.status_ok)
        self.assertTrue(mock_execute.called)

    def test_when_send_not_ok_returns_false(self, mock_execute):
        mock_execute.return_value = dhooks_lite.WebhookResponse(dict(), status_code=404)

        response = self.webhook_1.send_message_to_webhook(self.message)

        self.assertFalse(response.status_ok)
        self.assertTrue(mock_execute.called)

    def test_too_many_requests_1(self, mock_execute):
        """when 429 received, then set blocker and raise exception"""
        mock_execute.return_value = dhooks_lite.WebhookResponse(
            headers={"x-ratelimit-remaining": "5", "x-ratelimit-reset-after": "60"},
            status_code=429,
            content={
                "global": False,
                "message": "You are being rate limited.",
                "retry_after": 2000,
            },
        )

        try:
            self.webhook_1.send_message_to_webhook(self.message)
        except Exception as ex:
            self.assertIsInstance(ex, WebhookTooManyRequests)
            self.assertEqual(ex.retry_after, 2002)
        else:
            self.fail("Did not raise excepted exception")

        self.assertTrue(mock_execute.called)
        self.assertAlmostEqual(
            cache.ttl(self.webhook_1._blocked_cache_key()), 2002, delta=5
        )

    def test_too_many_requests_2(self, mock_execute):
        """when 429 received and no retry value in response, then use default"""
        mock_execute.return_value = dhooks_lite.WebhookResponse(
            headers={"x-ratelimit-remaining": "5", "x-ratelimit-reset-after": "60"},
            status_code=429,
            content={
                "global": False,
                "message": "You are being rate limited.",
            },
        )

        try:
            self.webhook_1.send_message_to_webhook(self.message)
        except Exception as ex:
            self.assertIsInstance(ex, WebhookTooManyRequests)
            self.assertEqual(ex.retry_after, 600)
        else:
            self.fail("Did not raise excepted exception")

        self.assertTrue(mock_execute.called)


if "discord" in app_labels():

    @patch(MODULE_PATH + ".DiscordUser", spec=True)
    class TestGroupPings(LoadTestDataMixin, TestCase):
        @classmethod
        def setUpClass(cls):
            super().setUpClass()
            cls.group_1 = Group.objects.create(name="Dummy Group 1")
            cls.group_2 = Group.objects.create(name="Dummy Group 2")

        def setUp(self):
            self.tracker = Tracker.objects.create(
                name="My Tracker",
                webhook=self.webhook_1,
                exclude_null_sec=True,
                exclude_w_space=True,
            )

        @staticmethod
        def _my_group_to_role(group: Group) -> dict:
            if not isinstance(group, Group):
                raise TypeError("group must be of type Group")

            return {"id": group.pk, "name": group.name}

        def test_can_ping_one_group(self, mock_DiscordUser):
            mock_DiscordUser.objects.group_to_role.side_effect = self._my_group_to_role
            self.tracker.ping_groups.add(self.group_1)
            killmail = self.tracker.process_killmail(load_killmail(10000101))

            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(mock_DiscordUser.objects.group_to_role.called)
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertIn(f"<@&{self.group_1.pk}>", message["content"])

        def test_can_ping_multiple_groups(self, mock_DiscordUser):
            mock_DiscordUser.objects.group_to_role.side_effect = self._my_group_to_role
            self.tracker.ping_groups.add(self.group_1)
            self.tracker.ping_groups.add(self.group_2)

            killmail = self.tracker.process_killmail(load_killmail(10000101))
            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(mock_DiscordUser.objects.group_to_role.called)
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertIn(f"<@&{self.group_1.pk}>", message["content"])
            self.assertIn(f"<@&{self.group_2.pk}>", message["content"])

        def test_can_combine_with_channel_ping(self, mock_DiscordUser):
            mock_DiscordUser.objects.group_to_role.side_effect = self._my_group_to_role
            self.tracker.ping_groups.add(self.group_1)
            self.tracker.ping_type = Tracker.ChannelPingType.HERE
            self.tracker.save()

            killmail = self.tracker.process_killmail(load_killmail(10000101))
            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(mock_DiscordUser.objects.group_to_role.called)
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertIn(f"<@&{self.group_1.pk}>", message["content"])
            self.assertIn("@here", message["content"])

        def test_can_handle_error_from_discord(self, mock_DiscordUser):
            mock_DiscordUser.objects.group_to_role.side_effect = HTTPError
            self.tracker.ping_groups.add(self.group_1)

            killmail = self.tracker.process_killmail(load_killmail(10000101))
            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(mock_DiscordUser.objects.group_to_role.called)
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertNotIn(f"<@&{self.group_1.pk}>", message["content"])


class TestEveKillmail(LoadTestDataMixin, NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        killmail = load_killmail(10000001)
        cls.eve_killmail = EveKillmail.objects.create_from_killmail(killmail)

    def test_str(self):
        self.assertEqual(str(self.eve_killmail), "ID:10000001")

    def test_repr(self):
        self.assertEqual(repr(self.eve_killmail), "EveKillmail(id=10000001)")


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
