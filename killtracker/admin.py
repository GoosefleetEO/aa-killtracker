from django.contrib import admin
from django.db.models.functions import Lower

from allianceauth.eveonline.models import EveAllianceInfo
from evesde.models import EveSolarSystem, EveGroup

from .models import EveEntity, Killmail, Webhook, Tracker

EVE_CATEGORY_ID_SHIPS = 6


@admin.register(EveSolarSystem)
class EveSolarSystemAdmin(admin.ModelAdmin):
    ordering = ['solar_system_name']
    search_fields = ['solar_system_name']


@admin.register(EveEntity)
class EveEntityAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'category', 'last_updated')
    list_filter = ('category',)
    

@admin.register(Killmail)
class KillmailAdmin(admin.ModelAdmin):
    list_select_related = True
    list_display = ('id', 'time', 'solar_system', '_victim_ship_type', 'victim', )

    def _victim_ship_type(self, obj):
        return obj.victim.ship_type
    
    _victim_ship_type.admin_order_field = 'victim__ship_type__name'


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'is_default',)


@admin.register(Tracker)
class Tracker(admin.ModelAdmin):
    list_display = ('name', 'webhook')
    autocomplete_fields = ['origin_solar_system']
    filter_horizontal = (
        'exclude_attacker_alliances',
        'required_attacker_alliances',
        'require_victim_alliances',
        'require_attackers_ship_groups'
    )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """overriding this formfield to have sorted lists in the form"""
        if db_field.name == 'exclude_attacker_alliances':
            kwargs['queryset'] = EveAllianceInfo.objects.all()\
                .order_by(Lower('alliance_name'))
        elif db_field.name == 'required_attacker_alliances':
            kwargs['queryset'] = EveAllianceInfo.objects.all()\
                .order_by(Lower('alliance_name'))
        elif db_field.name == 'require_victim_alliances':
            kwargs['queryset'] = EveAllianceInfo.objects.all()\
                .order_by(Lower('alliance_name'))
        elif db_field.name == 'require_attackers_ship_groups':
            kwargs['queryset'] = \
                EveGroup.objects.filter(category_id=EVE_CATEGORY_ID_SHIPS)\
                .order_by(Lower('group_name'))
        return super().formfield_for_manytomany(db_field, request, **kwargs)
