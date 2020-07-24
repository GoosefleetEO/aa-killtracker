# flake8: noqa
""" this generated the list of current tracker conditions for the documentation"""

# init and setup django project
import inspect
import os
import sys

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
myauth_dir = os.path.dirname(os.path.dirname(os.path.dirname(currentdir))) + "/myauth"
sys.path.insert(0, myauth_dir)

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myauth.settings.local")
django.setup()

# normal includes
from killtracker.models import Tracker

my_fields = [
    f
    for f in Tracker._meta.get_fields()
    if f.name.split("_")[0] in ["exclude", "require"]
]

for field in my_fields:
    field_name = field.name.replace("_", " ")
    print(f"{field_name}|{field.help_text}")
