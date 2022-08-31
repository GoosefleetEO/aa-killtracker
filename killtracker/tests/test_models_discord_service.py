import json
from unittest.mock import patch

from requests.exceptions import HTTPError

from django.contrib.auth.models import Group
from django.test import TestCase

from app_utils.django import app_labels

from killtracker.core.killmails import Killmail
from killtracker.models import Tracker

from .testdata.factories import TrackerFactory
from .testdata.helpers import LoadTestDataMixin, load_killmail

DISCORD_MESSAGES_PATH = "killtracker.core.discord_messages"

if "discord" in app_labels():

    @patch(DISCORD_MESSAGES_PATH + "._import_discord_user")
    class TestGroupPings(LoadTestDataMixin, TestCase):
        @classmethod
        def setUpClass(cls):
            super().setUpClass()
            cls.group_1 = Group.objects.create(name="Dummy Group 1")
            cls.group_2 = Group.objects.create(name="Dummy Group 2")

        def setUp(self):
            self.tracker = TrackerFactory(
                webhook=self.webhook_1, exclude_null_sec=True, exclude_w_space=True
            )

        @staticmethod
        def _my_group_to_role(group: Group) -> dict:
            if not isinstance(group, Group):
                raise TypeError("group must be of type Group")

            return {"id": group.pk, "name": group.name}

        def test_can_ping_one_group(self, mock_import_discord_user):
            mock_import_discord_user.return_value.objects.group_to_role.side_effect = (
                self._my_group_to_role
            )
            self.tracker.ping_groups.add(self.group_1)
            killmail = self.tracker.process_killmail(load_killmail(10000101))

            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(
                mock_import_discord_user.return_value.objects.group_to_role.called
            )
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertIn(f"<@&{self.group_1.pk}>", message["content"])

        def test_can_ping_multiple_groups(self, mock_import_discord_user):
            mock_import_discord_user.return_value.objects.group_to_role.side_effect = (
                self._my_group_to_role
            )
            self.tracker.ping_groups.add(self.group_1)
            self.tracker.ping_groups.add(self.group_2)

            killmail = self.tracker.process_killmail(load_killmail(10000101))
            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(
                mock_import_discord_user.return_value.objects.group_to_role.called
            )
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertIn(f"<@&{self.group_1.pk}>", message["content"])
            self.assertIn(f"<@&{self.group_2.pk}>", message["content"])

        def test_can_combine_with_channel_ping(self, mock_import_discord_user):
            mock_import_discord_user.return_value.objects.group_to_role.side_effect = (
                self._my_group_to_role
            )
            self.tracker.ping_groups.add(self.group_1)
            self.tracker.ping_type = Tracker.ChannelPingType.HERE
            self.tracker.save()

            killmail = self.tracker.process_killmail(load_killmail(10000101))
            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(
                mock_import_discord_user.return_value.objects.group_to_role.called
            )
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertIn(f"<@&{self.group_1.pk}>", message["content"])
            self.assertIn("@here", message["content"])

        def test_can_handle_error_from_discord(self, mock_import_discord_user):
            mock_import_discord_user.return_value.objects.group_to_role.side_effect = (
                HTTPError
            )
            self.tracker.ping_groups.add(self.group_1)

            killmail = self.tracker.process_killmail(load_killmail(10000101))
            self.tracker.generate_killmail_message(
                Killmail.from_json(killmail.asjson())
            )

            self.assertTrue(
                mock_import_discord_user.return_value.objects.group_to_role.called
            )
            self.assertEqual(self.webhook_1.main_queue.size(), 1)
            message = json.loads(self.webhook_1.main_queue.dequeue())
            self.assertNotIn(f"<@&{self.group_1.pk}>", message["content"])
