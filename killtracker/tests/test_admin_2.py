"""
from django.core.exceptions import ValidationError
from django.contrib.admin.sites import AdminSite

from ..admin import TrackerAdmin
from ..models import Tracker, Webhook
from app_utils.testing import NoSocketsTestCase


class TestTrackerAdmin(NoSocketsTestCase):
    def setUpClass(cls):
        super().setUpClass()
        cls.modeladmin = TrackerAdmin(model=Tracker, admin_site=AdminSite())
        cls.webhook_1 = Webhook.objects.create(
            name="Webhook 1", url="http://www.example.com/webhook_1", is_enabled=True
        )

    def test_clean_no_issue(self):
        tracker = Tracker(name="Test", webhook=self.webhook_1)
        self.modeladmin.form.clean()

    def test_clean_need_origin_for_max_jumps(self):
        tracker = Tracker(name="Test", webhook=self.webhook_1, require_max_jumps=10)
        with self.assertRaises(ValidationError):
            tracker.clean()

    def test_clean_need_origin_for_max_distance(self):
        tracker = Tracker(name="Test", webhook=self.webhook_1, require_max_distance=10)
        with self.assertRaises(ValidationError):
            tracker.clean()
"""
