from django.urls import path

from . import views

app_name = "killtracker"

urlpatterns = [
    path(
        "admin_killtracker_toogle_npc/<int:object_id>/",
        views.admin_killtracker_toogle_npc,
        name="admin_killtracker_toogle_npc",
    ),
    path(
        "admin_killtracker_toogle_npc/",
        views.admin_killtracker_toogle_npc,
        name="admin_killtracker_toogle_npc",
    ),
]
