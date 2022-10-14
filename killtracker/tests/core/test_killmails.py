import unittest
from datetime import timedelta
from unittest.mock import patch

import requests_mock
from redis.exceptions import LockError

from django.core.cache import cache
from django.test import TestCase
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now

from app_utils.esi_testing import BravadoOperationStub
from app_utils.testing import NoSocketsTestCase

from killtracker.core.killmails import (
    ZKB_API_URL,
    ZKB_REDISQ_URL,
    EntityCount,
    Killmail,
)
from killtracker.exceptions import KillmailDoesNotExist

from .. import CacheStub
from ..testdata.factories import KillmailFactory
from ..testdata.helpers import killmails_data, load_killmail

MODULE_PATH = "killtracker.core.killmails"
unittest.util._MAX_LENGTH = 1000


@requests_mock.Mocker()
@patch(MODULE_PATH + ".get_redis_client")
class TestCreateFromZkbRedisq(NoSocketsTestCase):
    def test_should_return_killmail(self, requests_mocker, mock_redis):
        # given
        requests_mocker.register_uri(
            "GET",
            ZKB_REDISQ_URL,
            status_code=200,
            json={"package": killmails_data()[10000001]},
        )
        # when
        killmail = Killmail.create_from_zkb_redisq()
        # then
        self.assertIsNotNone(killmail)
        self.assertEqual(killmail.id, 10000001)
        self.assertEqual(killmail.solar_system_id, 30004984)
        self.assertAlmostEqual(killmail.time, now(), delta=timedelta(seconds=120))
        self.assertEqual(killmail.victim.alliance_id, 3011)
        self.assertEqual(killmail.victim.character_id, 1011)
        self.assertEqual(killmail.victim.corporation_id, 2011)
        self.assertEqual(killmail.victim.damage_taken, 434)
        self.assertEqual(killmail.victim.ship_type_id, 603)
        self.assertEqual(len(killmail.attackers), 3)

        attacker_1 = killmail.attackers[0]
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

    def test_should_return_none_when_zkb_returns_empty_package(
        self, requests_mocker, mock_redis
    ):
        # given
        requests_mocker.register_uri(
            "GET", ZKB_REDISQ_URL, status_code=200, json={"package": None}
        )
        # when
        killmail = Killmail.create_from_zkb_redisq()
        # then
        self.assertIsNone(killmail)

    def test_should_handle_zkb_data_has_no_solar_system(
        self, requests_mocker, mock_redis
    ):
        # given
        requests_mocker.register_uri(
            "GET",
            ZKB_REDISQ_URL,
            status_code=200,
            json={"package": killmails_data()[10000402]},
        )
        # when
        killmail = Killmail.create_from_zkb_redisq()
        # then
        self.assertIsNotNone(killmail)

    def test_should_return_none_when_zkb_returns_429_error(
        self, requests_mocker, mock_redis
    ):
        # given
        requests_mocker.register_uri(
            "GET", ZKB_REDISQ_URL, status_code=429, text="429 too many requests"
        )
        # when
        killmail = Killmail.create_from_zkb_redisq()
        # then
        self.assertIsNone(killmail)

    def test_should_return_none_when_zkb_returns_general_error(
        self, requests_mocker, mock_redis
    ):
        # given
        requests_mocker.register_uri(
            "GET",
            ZKB_REDISQ_URL,
            status_code=200,
            text="""Your IP has been banned because of excessive errors.

You can only have one request to listen.php in flight at any time, otherwise you will generate a too many requests error (429). If you have too many of these errors you will be banned automatically.""",
        )
        # when
        killmail = Killmail.create_from_zkb_redisq()
        # then
        self.assertIsNone(killmail)

    def test_should_return_none_when_zkb_does_not_return_json(
        self, requests_mocker, mock_redis
    ):
        # given
        requests_mocker.register_uri(
            "GET", ZKB_REDISQ_URL, status_code=200, text="this is not JSON"
        )
        # when
        killmail = Killmail.create_from_zkb_redisq()
        # then
        self.assertIsNone(killmail)

    def test_should_return_none_if_lock_not_acquired(self, requests_mocker, mock_redis):
        # given
        mock_redis.return_value.lock.side_effect = LockError
        # when
        killmail = Killmail.create_from_zkb_redisq()
        # then
        self.assertIsNone(killmail)


class TestKillmailSerialization(NoSocketsTestCase):
    def test_dict_serialization(self):
        killmail = load_killmail(10000001)
        dct_1 = killmail.asdict()
        killmail_2 = Killmail.from_dict(dct_1)
        self.maxDiff = None
        self.assertEqual(killmail, killmail_2)

    def test_json_serialization(self):
        killmail = load_killmail(10000001)
        json_1 = killmail.asjson()
        killmail_2 = Killmail.from_json(json_1)
        self.maxDiff = None
        self.assertEqual(killmail, killmail_2)


class TestKillmailBasics(NoSocketsTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.killmail = load_killmail(10000001)

    def test_str(self):
        self.assertEqual(str(self.killmail), "Killmail(id=10000001)")

    def test_repr(self):
        self.assertEqual(repr(self.killmail), "Killmail(id=10000001)")

    def test_entity_ids(self):
        result = self.killmail.entity_ids()
        expected = {
            1011,
            2011,
            3011,
            603,
            30004984,
            1001,
            1002,
            1003,
            2001,
            3001,
            34562,
            2977,
            3756,
            2488,
            500001,
            500004,
        }
        self.assertSetEqual(result, expected)

    def test_should_return_attacker_alliance_ids(self):
        # when
        result = self.killmail.attackers_distinct_alliance_ids()
        # then
        self.assertSetEqual(set(result), {3001})

    def test_should_return_attacker_corporation_ids(self):
        # when
        result = self.killmail.attackers_distinct_corporation_ids()
        # then
        self.assertSetEqual(set(result), {2001})

    def test_should_return_attacker_character_ids(self):
        # when
        result = self.killmail.attackers_distinct_character_ids()
        # then
        self.assertSetEqual(set(result), {1001, 1002, 1003})

    def test_attackers_ships_types(self):
        self.assertListEqual(
            self.killmail.attackers_ship_type_ids(), [34562, 3756, 3756]
        )

    def test_ships_types(self):
        self.assertSetEqual(self.killmail.ship_type_distinct_ids(), {603, 34562, 3756})


class TestEntityCount(NoSocketsTestCase):
    def test_is_alliance(self):
        alliance = EntityCount(1, EntityCount.CATEGORY_ALLIANCE)
        corporation = EntityCount(2, EntityCount.CATEGORY_CORPORATION)

        self.assertTrue(alliance.is_alliance)
        self.assertFalse(corporation.is_alliance)

    def test_is_corporation(self):
        alliance = EntityCount(1, EntityCount.CATEGORY_ALLIANCE)
        corporation = EntityCount(2, EntityCount.CATEGORY_CORPORATION)

        self.assertFalse(alliance.is_corporation)
        self.assertTrue(corporation.is_corporation)


@patch(MODULE_PATH + ".cache", CacheStub())
@patch(MODULE_PATH + ".esi")
@requests_mock.Mocker()
class TestCreateFromZkbApi(NoSocketsTestCase):
    def test_normal(self, mock_esi, requests_mocker):
        killmail_id = 10000001
        killmail_data = killmails_data()[killmail_id]
        zkb_api_data = [
            {"killmail_id": killmail_data["killID"], "zkb": killmail_data["zkb"]}
        ]
        requests_mocker.register_uri(
            "GET",
            f"{ZKB_API_URL}killID/{killmail_id}/",
            status_code=200,
            json=zkb_api_data,
        )
        killmail_data["killmail"]["killmail_time"] = parse_datetime(
            killmail_data["killmail"]["killmail_time"]
        )
        mock_esi.client.Killmails.get_killmails_killmail_id_killmail_hash.return_value = BravadoOperationStub(
            killmail_data["killmail"]
        )

        killmail = Killmail.create_from_zkb_api(killmail_id)
        self.assertIsNotNone(killmail)
        self.assertEqual(killmail.id, killmail_id)
        self.assertAlmostEqual(killmail.time, now(), delta=timedelta(seconds=120))

        self.assertEqual(killmail.victim.alliance_id, 3011)
        self.assertEqual(killmail.victim.character_id, 1011)
        self.assertEqual(killmail.victim.corporation_id, 2011)
        self.assertEqual(killmail.victim.damage_taken, 434)
        self.assertEqual(killmail.victim.ship_type_id, 603)

        self.assertEqual(len(killmail.attackers), 3)

        attacker_1 = killmail.attackers[0]
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


class TestKillmailStorage(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_should_store_and_retrieve_killmail(self):
        # given
        killmail_1 = KillmailFactory()
        # when
        killmail_1.save()
        killmail_2 = Killmail.get(id=killmail_1.id)
        # then
        self.assertEqual(killmail_1, killmail_2)

    def test_should_raise_error_when_killmail_does_not_exist(self):
        # when/then
        with self.assertRaises(KillmailDoesNotExist):
            Killmail.get(id=99)
