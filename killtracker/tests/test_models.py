from unittest.mock import patch, Mock

from ..models import Tracker, TrackerKillmail
from ..utils import NoSocketsTestCase, set_test_logger


MODULE_PATH = 'killtracker.models'
logger = set_test_logger(MODULE_PATH, __file__)




