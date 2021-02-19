from app_utils.django import clean_setting


# ignore killmails that are older than the given number in minutes
# sometimes killmails appear belated on ZKB,
# this feature ensures they don't create new alerts
KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER = clean_setting(
    "KILLTRACKER_KILLMAIL_MAX_AGE_FOR_TRACKER", 60
)

# Maximum number of killmails retrieved from ZKB by task run
KILLTRACKER_MAX_KILLMAILS_PER_RUN = clean_setting(
    "KILLTRACKER_MAX_KILLMAILS_PER_RUN", 200
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

# Tasks hard timeout
KILLTRACKER_TASKS_TIMEOUT = clean_setting("KILLTRACKER_TASKS_TIMEOUT", 1800)

# delay in seconds between every message sent to Discord
# this needs to be >= 1 to prevent 429 Too Many Request errors
KILLTRACKER_DISCORD_SEND_DELAY = clean_setting(
    "KILLTRACKER_DISCORD_SEND_DELAY", default_value=2, min_value=1, max_value=900
)

# Maximum retries when generating a message from a killmail
KILLTRACKER_GENERATE_MESSAGE_MAX_RETRIES = clean_setting(
    "KILLTRACKER_GENERATE_MESSAGE_MAX_RETRIES", 3
)

# Delay when retrying to generate a message in seconds
KILLTRACKER_GENERATE_MESSAGE_RETRY_COUNTDOWN = clean_setting(
    "KILLTRACKER_GENERATE_MESSAGE_RETRY_COUNTDOWN", 10
)

# Cache duration for objects in tasks in seconds
KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT = clean_setting(
    "KILLTRACKER_TASK_OBJECTS_CACHE_TIMEOUT", 60
)

# Minimum delay when retrying a task
KILLTRACKER_TASK_MINIMUM_RETRY_DELAY = clean_setting(
    "KILLTRACKER_TASK_MINIMUM_RETRY_DELAY", default_value=0.05, min_value=0.0
)
