from .utils import clean_setting

# when true will automatically be with every solar system
KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS = clean_setting(
    "KILLTRACKER_KILLMAIL_STALE_AFTER_DAYS", 30
)

KILLTRACKER_MAX_KILLMAILS_PER_RUN = clean_setting(
    "KILLTRACKER_MAX_KILLMAILS_PER_RUN", 50
)

KILLTRACKER_MAX_KILLMAILS_PER_BATCH = clean_setting(
    "KILLTRACKER_MAX_KILLMAILS_PER_BATCH", 5
)

# ignore killmails that are older than the given number in hours
# sometimes killmails appear belated on ZKB,
# this feature ensures they don't create new alerts
KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER = clean_setting(
    "KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER", 1
)
