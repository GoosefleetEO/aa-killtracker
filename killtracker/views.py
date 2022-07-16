from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from allianceauth.services.hooks import get_extension_logger
from app_utils.logging import LoggerAddTag

from . import __title__
from .constants import SESSION_KEY_TOOGLE_NPC

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


@login_required
@staff_member_required
def admin_killtracker_toogle_npc(request, object_id: int = None):
    """Enable or disable the toogle to show NPC types."""
    request.session[SESSION_KEY_TOOGLE_NPC] = not request.session.get(
        SESSION_KEY_TOOGLE_NPC, False
    )
    if object_id:
        return redirect("admin:killtracker_tracker_change", object_id)
    return redirect("admin:killtracker_tracker_add")
