from .utils import clean_setting

# when true will automatically be with every solar system
KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS = clean_setting(
    "KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS", 30
)
