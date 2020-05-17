from django.contrib import admin

from .models import EveEntity, Killmail, Webhook


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
