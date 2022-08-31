import dhooks_lite

from app_utils.testing import NoSocketsTestCase

from killtracker.core import discord_messages

from ..testdata.factories import (
    KillmailAttackerFactory,
    KillmailFactory,
    KillmailVictimFactory,
    TrackerFactory,
)
from ..testdata.helpers import LoadTestDataMixin


class TestCreateEmbed(LoadTestDataMixin, NoSocketsTestCase):
    def test_should_create_normal_embed(self):
        # given
        tracker = TrackerFactory()
        attacker = KillmailAttackerFactory(
            character_id=1011, corporation_id=2011, alliance_id=3011
        )
        victim = KillmailVictimFactory(
            character_id=1001, corporation_id=2001, alliance_id=3001
        )
        killmail = KillmailFactory(victim=victim, attackers=[attacker])
        # when
        embed = discord_messages.create_embed(tracker, killmail)
        # then
        self.assertIsInstance(embed, dhooks_lite.Embed)

    def test_should_create_normal_for_killmail_without_value(self):
        # given
        tracker = TrackerFactory()
        attacker = KillmailAttackerFactory(
            character_id=1011, corporation_id=2011, alliance_id=3011
        )
        victim = KillmailVictimFactory(
            character_id=1001, corporation_id=2001, alliance_id=3001
        )
        killmail = KillmailFactory(
            victim=victim, attackers=[attacker], zkb__total_value=None
        )
        # when
        embed = discord_messages.create_embed(tracker, killmail)
        # then
        self.assertIsInstance(embed, dhooks_lite.Embed)
