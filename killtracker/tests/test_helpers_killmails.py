import unittest
from unittest.mock import patch

from . import ResponseStub
from ..helpers.killmails import Killmail
from .testdata.helpers import killmails_data, load_killmail
from ..utils import NoSocketsTestCase, set_test_logger


MODULE_PATH = "killtracker.helpers.killmails"
logger = set_test_logger(MODULE_PATH, __file__)
unittest.util._MAX_LENGTH = 1000


@patch(MODULE_PATH + ".requests", spec=True)
class TestKillmailsFetch(NoSocketsTestCase):
    def test_fetch_from_zkb_normal(self, mock_requests):
        mock_requests.get.return_value = ResponseStub(
            {"package": killmails_data[10000001]}
        )

        killmail = Killmail.fetch_from_zkb_redisq()

        self.assertIsNotNone(killmail)
        self.assertEqual(killmail.id, 10000001)
        self.assertEqual(killmail.solar_system_id, 30004984)

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

    def test_fetch_from_zkb_no_data(self, mock_requests):
        mock_requests.get.return_value = ResponseStub({"package": None})

        killmail = Killmail.fetch_from_zkb_redisq()
        self.assertIsNone(killmail)


class TestKillmailsBasics(NoSocketsTestCase):
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

    def test_entity_ids(self):
        killmail = load_killmail(10000001)
        result = killmail.entity_ids()
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
        }
        self.assertSetEqual(result, expected)
