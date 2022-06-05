import datetime as dt

import factory
import factory.fuzzy

from django.utils.timezone import now

from killtracker.app_settings import KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER
from killtracker.core.killmails import (
    Killmail,
    KillmailAttacker,
    KillmailPosition,
    KillmailVictim,
    KillmailZkb,
    _KillmailCharacter,
)
from killtracker.models import Tracker, Webhook

from .load_eveuniverse import eveuniverse_testdata

_SOLAR_SYSTEM_IDS = {obj["id"] for obj in eveuniverse_testdata["EveSolarSystem"]}


class KillmailCharacterFactory(factory.Factory):
    class Meta:
        model = _KillmailCharacter

    character_id = factory.Sequence(lambda n: 90_000_001 + n)
    corporation_id = factory.Sequence(lambda n: 98_000_001 + n)
    alliance_id = factory.Sequence(lambda n: 99_000_001 + n)
    ship_type_id = 3756  # Gnosis


class KillmailVictimFactory(KillmailCharacterFactory):
    class Meta:
        model = KillmailVictim

    damage_taken = factory.fuzzy.FuzzyInteger(1_000_000)


class KillmailAttackerFactory(KillmailCharacterFactory):
    class Meta:
        model = KillmailAttacker

    damage_done = factory.fuzzy.FuzzyInteger(1_000_000)
    security_status = factory.fuzzy.FuzzyFloat(-10.0, 5)
    weapon_type_id = 2977


class KillmailPositionFactory(factory.Factory):
    class Meta:
        model = KillmailPosition

    x = factory.fuzzy.FuzzyFloat(-10_000, 10_000)
    y = factory.fuzzy.FuzzyFloat(-10_000, 10_000)
    z = factory.fuzzy.FuzzyFloat(-10_000, 10_000)


class KillmailZkbFactory(factory.Factory):
    class Meta:
        model = KillmailZkb

    location_id = factory.Sequence(lambda n: n + 60_000_000)
    hash = factory.fuzzy.FuzzyText()
    fitted_value = factory.fuzzy.FuzzyFloat(10_000, 100_000_000)
    total_value = factory.LazyAttribute(lambda o: o.fitted_value)
    points = factory.fuzzy.FuzzyInteger(1000)
    is_npc = False
    is_solo = False
    is_awox = False


class KillmailFactory(factory.Factory):
    class Meta:
        model = Killmail

    class Params:
        # max age of a killmail in seconds
        max_age = KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER

    id = factory.Sequence(lambda n: n + 1800000000001)
    victim = factory.SubFactory(KillmailVictimFactory)
    position = factory.SubFactory(KillmailPositionFactory)
    zkb = factory.SubFactory(KillmailZkbFactory)
    solar_system_id = factory.fuzzy.FuzzyChoice(_SOLAR_SYSTEM_IDS)

    @factory.lazy_attribute
    def time(self):
        return factory.fuzzy.FuzzyDateTime(
            now() - dt.timedelta(seconds=self.max_age - 5)
        ).fuzz()

    @factory.lazy_attribute
    def attackers(self):
        amount = factory.fuzzy.FuzzyInteger(1, 10).fuzz()
        my_attackers = [KillmailAttackerFactory() for _ in range(amount)]
        my_attackers[0].is_final_blow = True
        return my_attackers


class WebhookFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Webhook
        django_get_or_create = ("name",)

    name = factory.Faker("name")
    url = factory.Faker("uri")


class TrackerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tracker
        django_get_or_create = ("name",)

    name = factory.Faker("name")
    webhook = factory.SubFactory(WebhookFactory)
