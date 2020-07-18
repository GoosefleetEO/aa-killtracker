"""
from django.utils.translation import ugettext_lazy as _

from allianceauth.services.hooks import MenuItemHook, UrlHook
from allianceauth import hooks

from . import urls


class KillmailsMenuItem(MenuItemHook):    
    def __init__(self):
        # setup menu entry for sidebar
        MenuItemHook.__init__(
            self,
            _('killtracker'),
            'fa fa-cube fa-fw',
            'killtracker:index',
            navactive=['killtracker:index']
        )

    def render(self, request):
        if request.user.has_perm('killtracker.basic_access'):
            return MenuItemHook.render(self, request)
        return ''


@hooks.register('menu_item_hook')
def register_menu():
    return KillmailsMenuItem()


@hooks.register('url_hook')
def register_urls():
    return UrlHook(urls, 'killtracker', r'^killtracker/')
"""
