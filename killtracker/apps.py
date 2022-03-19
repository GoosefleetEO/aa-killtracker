from django.apps import AppConfig

from . import __version__


class KillmailsConfig(AppConfig):
    name = "killtracker"
    label = "killtracker"
    verbose_name = f"Killtracker v{__version__}"
    default_auto_field = "django.db.models.BigAutoField"
