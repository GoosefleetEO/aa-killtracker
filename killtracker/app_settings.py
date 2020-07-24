from .utils import clean_setting


# ignore killmails that are older than the given number in minutes
# sometimes killmails appear belated on ZKB,
# this feature ensures they don't create new alerts
KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER = clean_setting(
    "KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER", 60
)

# Maximum number of killmails retrieved from ZKB by task run
KILLTRACKER_MAX_KILLMAILS_PER_RUN = clean_setting(
    "KILLTRACKER_MAX_KILLMAILS_PER_RUN", 250
)

# Killmails older than set number of days will be purged from the database.
# If you want to keep all killmails set this to 0.
KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS = clean_setting(
    "KILLTRACKER_PURGE_KILLMAILS_AFTER_DAYS", 30
)

# whether killmails retrieved from ZKB are stored in the database
KILLTRACKER_STORING_KILLMAILS_ENABLED = clean_setting(
    "KILLTRACKER_STORING_KILLMAILS_ENABLED", False
)
