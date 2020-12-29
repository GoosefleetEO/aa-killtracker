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

# Wether app sets the name and avatar icon of a webhook.
# When False the webhook will use it's own values as set on the platform
KILLTRACKER_WEBHOOK_SET_AVATAR = clean_setting("KILLTRACKER_WEBHOOK_SET_AVATAR", True)


#####################
# INTERNAL SETTINGS

# Max duration to wait for new killmails from redisq in seconds
KILLTRACKER_REDISQ_TTW = clean_setting("KILLTRACKER_REDISQ_TTW", 5)

# Maximum duration in seconds for a killtracker run.
# Important to ensure that the current run finishes before CRON starts the next one
KILLTRACKER_MAX_DURATION_PER_RUN = clean_setting("KILLTRACKER_MAX_DURATION_PER_RUN", 50)

# Tasks hard timeout
KILLTRACKER_TASKS_TIMEOUT = clean_setting("KILLTRACKER_TASKS_TIMEOUT", 1800)
