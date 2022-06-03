import json
from datetime import timedelta
from unittest.mock import patch

import dhooks_lite
from bs4 import BeautifulSoup
from markdown import markdown
from requests.exceptions import HTTPError

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.utils.timezone import now
from eveuniverse.models import (
    EveConstellation,
    EveEntity,
    EveRegion,
    EveSolarSystem,
    EveType,
)

from app_utils.django import app_labels
from app_utils.json import JSONDateTimeDecoder
from app_utils.testing import NoSocketsTestCase

from ..core.killmails import Killmail
from ..exceptions import WebhookTooManyRequests
from ..models import EveKillmail, EveKillmailVictim, Tracker, Webhook
from .testdata.helpers import LoadTestDataMixin, load_eve_killmails, load_killmail

MODULE_PATH = "killtracker.models"


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
        obj = EveKillmailVictim(character=EveEntity.objects.get(id=1001))
        self.assertEqual(str(obj), "Bruce Wayne")

    def test_str_corporation(self):
        obj = EveKillmailVictim(corporation=EveEntity.objects.get(id=2001))
        self.assertEqual(str(obj), "Wayne Technologies")

    def test_str_alliance(self):
        obj = EveKillmailVictim(alliance=EveEntity.objects.get(id=3001))
        self.assertEqual(str(obj), "Wayne Enterprise")

    def test_str_faction(self):
        obj = EveKillmailVictim(faction=EveEntity.objects.get(id=500001))
        self.assertEqual(str(obj), "Caldari State")


@patch(MODULE_PATH + ".Webhook.enqueue_message")
class TestTrackerGenerateKillmailMessage(LoadTestDataMixin, TestCase):
    def setUp(self) -> None:
        self.tracker = Tracker.objects.create(name="My Tracker", webhook=self.webhook_1)

    def test_should_generate_message(self, mock_enqueue_message):
        # given
        self.tracker.origin_solar_system_id = 30003067
        self.tracker.save()
        svipul = EveType.objects.get(name="Svipul")
        self.tracker.require_attackers_ship_types.add(svipul)
        self.tracker.require_attackers_ship_types.add(
            EveType.objects.get(name="Gnosis")
        )
        killmail = load_killmail(10000101)
        killmail_json = Killmail.from_json(killmail.asjson())
        # when
        self.tracker.generate_killmail_message(killmail_json)
        # then
        _, kwargs = mock_enqueue_message.call_args
        content = kwargs["content"]
        self.assertIn("My Tracker", content)
        embed = kwargs["embeds"][0]
        self.assertEqual(embed.title, "Haras | Svipul | Killmail")
        self.assertEqual(
            embed.thumbnail.url, svipul.icon_url(size=self.tracker.ICON_SIZE)
        )
        html = markdown(embed.description)
        description = "".join(
            BeautifulSoup(html, features="html.parser").findAll(text=True)
        )
        lines = description.splitlines()
        self.assertEqual(
            (
                "Lex Luthor (LexCorp) lost their Svipul in Haras (The Bleak Lands) "
                "worth 10.00k ISK."
            ),
            lines[0],
        )
        self.assertEqual(
            "Final blow by Bruce Wayne (Wayne Technologies) in a Svipul.", lines[1]
        )
