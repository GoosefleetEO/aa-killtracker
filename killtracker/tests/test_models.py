from datetime import timedelta

# from unittest.mock import patch, Mock
from django.utils.timezone import now

from allianceauth.eveonline.models import EveAllianceInfo

from ..models import Killmail, Tracker, Webhook
from .testdata.helpers import (
    load_evesde, load_eveentities, load_evealliances, load_killmails
)
from ..utils import NoSocketsTestCase, set_test_logger


MODULE_PATH = 'killtracker.models'
logger = set_test_logger(MODULE_PATH, __file__)


class TestTracker(NoSocketsTestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        load_evesde()        
        load_evealliances()
        load_eveentities()
        cls.webhook = Webhook(name='dummy', url='dummy', is_default=True)        
    
    def test_can_match_all(self):
        load_killmails({10000001, 10000002})
        tracker = Tracker.objects.create(name='Test')
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002}
        self.assertEqual(result, expected)

    def test_can_filter_max_age(self):
        load_killmails({10000001, 10000002})
        killmail = Killmail.objects.get(id=10000002)
        killmail.time = now() - timedelta(hours=1, seconds=1)
        killmail.save()        
        tracker = Tracker.objects.create(name='Test', max_age=1)
        result = tracker.calculate_killmails()
        expected = {10000001}
        self.assertEqual(result, expected)

    def test_can_filter_high_sec_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})        
        tracker = Tracker.objects.create(name='Test', exclude_high_sec=True)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000003, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_low_sec_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})        
        tracker = Tracker.objects.create(name='Test', exclude_low_sec=True)
        result = tracker.calculate_killmails()
        expected = {10000002, 10000003, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_null_sec_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name='Test', exclude_null_sec=True)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_w_space_kills(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name='Test', exclude_w_space=True)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002, 10000003}
        self.assertEqual(result, expected)

    def test_can_filter_min_attackers(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name='Test', min_attackers=3)
        result = tracker.calculate_killmails()
        expected = {10000001}
        self.assertEqual(result, expected)

    # todo: max_jumps

    def test_can_filter_max_attackers(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name='Test', max_attackers=2)
        result = tracker.calculate_killmails()
        expected = {10000002, 10000003, 10000004}
        self.assertEqual(result, expected)

    def test_can_filter_min_value(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(name='Test', min_value=1000000000)
        result = tracker.calculate_killmails()
        expected = {10000004}
        self.assertEqual(result, expected)

    def test_can_filter_max_distance(self):
        load_killmails({10000001, 10000002, 10000003, 10000004})
        tracker = Tracker.objects.create(
            name='Test', origin_solar_system_id=30045349, max_distance=6, 
        )
        result = tracker.calculate_killmails()
        expected = {10000001}
        self.assertEqual(result, expected)

    def test_can_filter_attacker_alliance(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name='Test')
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.exclude_attacker_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000005}
        self.assertEqual(result, expected)

    def test_can_required_attacker_alliances(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name='Test')
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3011)
        tracker.required_attacker_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000005}
        self.assertEqual(result, expected)

    def test_can_required_victim_alliances(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name='Test')
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.require_victim_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000005}
        self.assertEqual(result, expected)

    def test_can_filter_nullsec_and_attacker_alliance(self):
        load_killmails({10000001, 10000002, 10000003, 10000004, 10000005})
        tracker = Tracker.objects.create(name='Test', exclude_null_sec=True)
        excluded_alliance = EveAllianceInfo.objects.get(alliance_id=3001)
        tracker.required_attacker_alliances.add(excluded_alliance)
        result = tracker.calculate_killmails()
        expected = {10000001, 10000002, 10000004}
        self.assertEqual(result, expected)
