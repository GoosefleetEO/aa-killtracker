"""Create discord messages from killmails."""

import dhooks_lite
from requests.exceptions import HTTPError

from eveuniverse.helpers import EveEntityNameResolver
from eveuniverse.models import EveEntity, EveSolarSystem

from allianceauth.eveonline.evelinks import dotlan, eveimageserver, zkillboard
from allianceauth.services.hooks import get_extension_logger
from app_utils.django import app_labels
from app_utils.logging import LoggerAddTag
from app_utils.urls import static_file_absolute_url
from app_utils.views import humanize_value

from .. import __title__
from ..models import Tracker
from .killmails import ZKB_KILLMAIL_BASEURL, Killmail

ICON_SIZE = 128


logger = LoggerAddTag(get_extension_logger(__name__), __title__)


def create_content(tracker: Tracker, intro_text: str = None) -> str:
    """Create content for Discord message for a killmail."""

    intro_parts = []

    if tracker.ping_type == Tracker.ChannelPingType.EVERYBODY:
        intro_parts.append("@everybody")
    elif tracker.ping_type == Tracker.ChannelPingType.HERE:
        intro_parts.append("@here")

    if tracker.ping_groups.exists():
        if "discord" in app_labels():
            DiscordUser = _import_discord_user()
            for group in tracker.ping_groups.all():
                try:
                    role = DiscordUser.objects.group_to_role(group)
                except HTTPError:
                    logger.warning(
                        "Failed to get Discord roles. Can not ping groups.",
                        exc_info=True,
                    )
                else:
                    if role:
                        intro_parts.append(f"<@&{role['id']}>")

        else:
            logger.warning(
                "Discord service needs to be installed in order "
                "to use groups ping features."
            )

    if tracker.is_posting_name:
        intro_parts.append(f"Tracker **{tracker.name}**:")

    intro_parts_2 = []
    if intro_text:
        intro_parts_2.append(intro_text)
    if intro_parts:
        intro_parts_2.append(" ".join(intro_parts))

    return "\n".join(intro_parts_2)


def _import_discord_user():
    from allianceauth.services.modules.discord.models import DiscordUser

    return DiscordUser


def create_embed(tracker: Tracker, killmail: Killmail) -> dhooks_lite.Embed:
    """Create Discord embed for a killmail."""

    resolver = EveEntity.objects.bulk_resolve_names(ids=killmail.entity_ids())

    # victim
    if killmail.victim.alliance_id:
        victim_organization = resolver.to_name(killmail.victim.alliance_id)
        victim_org_url = zkillboard.alliance_url(killmail.victim.alliance_id)
        victim_org_icon_url = eveimageserver.alliance_logo_url(
            killmail.victim.alliance_id, size=ICON_SIZE
        )
    elif killmail.victim.corporation_id:
        victim_organization = resolver.to_name(killmail.victim.corporation_id)
        victim_org_url = zkillboard.corporation_url(killmail.victim.corporation_id)
        victim_org_icon_url = eveimageserver.corporation_logo_url(
            killmail.victim.corporation_id, size=ICON_SIZE
        )
    else:
        victim_organization = None
        victim_org_url = None
        victim_org_icon_url = None

    if killmail.victim.corporation_id:
        victim_corporation_zkb_link = _corporation_zkb_link(
            tracker, killmail.victim.corporation_id, resolver
        )
    else:
        victim_corporation_zkb_link = ""

    if killmail.victim.character_id:
        victim_character_zkb_link = _character_zkb_link(
            tracker, killmail.victim.character_id, resolver
        )
        victim_str = f"{victim_character_zkb_link} ({victim_corporation_zkb_link})"
    elif killmail.victim.corporation_id:
        victim_str = victim_corporation_zkb_link
    else:
        victim_str = ""

    # final attacker
    for attacker in killmail.attackers:
        if attacker.is_final_blow:
            final_attacker = attacker
            break
    else:
        final_attacker = None

    if final_attacker:
        if final_attacker.corporation_id:
            final_attacker_corporation_zkb_link = _corporation_zkb_link(
                tracker, final_attacker.corporation_id, resolver
            )
        else:
            final_attacker_corporation_zkb_link = ""

        if final_attacker.character_id and final_attacker.corporation_id:
            final_attacker_character_zkb_link = _character_zkb_link(
                tracker, final_attacker.character_id, resolver
            )
            final_attacker_str = (
                f"{final_attacker_character_zkb_link} "
                f"({final_attacker_corporation_zkb_link})"
            )
        elif final_attacker.corporation_id:
            final_attacker_str = f"{final_attacker_corporation_zkb_link}"
        elif final_attacker.faction_id:
            final_attacker_str = f"**{resolver.to_name(final_attacker.faction_id)}**"
        else:
            final_attacker_str = "(Unknown final_attacker)"

        final_attacker_ship_type_name = resolver.to_name(final_attacker.ship_type_id)

    else:
        final_attacker_str = ""
        final_attacker_ship_type_name = ""

    if killmail.solar_system_id:
        solar_system, _ = EveSolarSystem.objects.get_or_create_esi(
            id=killmail.solar_system_id
        )
        solar_system_link = tracker.webhook.create_message_link(
            name=solar_system.name, url=dotlan.solar_system_url(solar_system.name)
        )
        region_name = solar_system.eve_constellation.eve_region.name
        solar_system_text = f"{solar_system_link} ({region_name})"
    else:
        solar_system_text = ""

    # self info
    show_as_fleetkill = False
    distance_text = ""
    main_org_text = ""
    main_org_name = ""
    main_org_icon_url = eveimageserver.alliance_logo_url(1, size=ICON_SIZE)
    main_ship_group_text = ""
    tracked_ship_types_text = ""
    embed_color = None
    if killmail.tracker_info:
        show_as_fleetkill = tracker.identify_fleets
        embed_color = int(tracker.color[1:], 16) if tracker.color else None
        if tracker.origin_solar_system:
            origin_solar_system_link = tracker.webhook.create_message_link(
                name=tracker.origin_solar_system.name,
                url=dotlan.solar_system_url(tracker.origin_solar_system.name),
            )
            if killmail.tracker_info.distance is not None:
                distance_str = f"{killmail.tracker_info.distance:,.1f}"
            else:
                distance_str = "?"

            if killmail.tracker_info.jumps is not None:
                jumps_str = killmail.tracker_info.jumps
            else:
                jumps_str = "?"

            distance_text = (
                f"\nDistance from {origin_solar_system_link}: "
                f"{distance_str} LY | {jumps_str} jumps"
            )

        # main group
        main_org = killmail.tracker_info.main_org
        if main_org:
            main_org_name = resolver.to_name(main_org.id)
            if main_org.is_corporation:
                main_org_link = _corporation_zkb_link(tracker, main_org.id, resolver)
                main_org_icon_url = eveimageserver.corporation_logo_url(
                    main_org.id, size=ICON_SIZE
                )
            else:
                main_org_link = _alliance_zkb_link(tracker, main_org.id, resolver)
                main_org_icon_url = eveimageserver.alliance_logo_url(
                    main_org.id, size=ICON_SIZE
                )
            main_org_text = f" | Main group: {main_org_link} ({main_org.count})"
        else:
            show_as_fleetkill = False

        # main ship group
        main_ship_group = killmail.tracker_info.main_ship_group
        if main_ship_group:
            main_ship_group_text = f"\nMain ship class: **{main_ship_group.name}**"

        # tracked attacker ships
        matching_ship_type_ids = killmail.tracker_info.matching_ship_type_ids
        if matching_ship_type_ids:
            ship_types_text = "**, **".join(
                sorted(
                    [resolver.to_name(type_id) for type_id in matching_ship_type_ids]
                )
            )
            tracked_ship_types_text = (
                f"\nTracked ship types involved: **{ship_types_text}**"
            )

    victim_ship_type_name = resolver.to_name(killmail.victim.ship_type_id)
    total_value = (
        humanize_value(killmail.zkb.total_value) if killmail.zkb.total_value else "?"
    )
    description = (
        f"{victim_str} lost their **{victim_ship_type_name}** "
        f"in {solar_system_text} "
        f"worth **{total_value}** ISK.\n"
        f"Final blow by {final_attacker_str} "
        f"in a **{final_attacker_ship_type_name}**.\n"
        f"Attackers: **{len(killmail.attackers):,}**{main_org_text}"
        f"{main_ship_group_text}"
        f"{tracked_ship_types_text}"
        f"{distance_text}"
    )
    solar_system_name = resolver.to_name(killmail.solar_system_id)
    if show_as_fleetkill:
        title = f"{solar_system_name} | {main_org_name} | Fleetkill"
    else:
        title = f"{solar_system_name} | {victim_ship_type_name} | Killmail"

    if show_as_fleetkill:
        thumbnail_url = main_org_icon_url
    else:
        thumbnail_url = eveimageserver.type_icon_url(
            killmail.victim.ship_type_id, size=ICON_SIZE
        )

    zkb_killmail_url = f"{ZKB_KILLMAIL_BASEURL}{killmail.id}/"
    # TODO This is a workaround for Embed.Author.name. Address in dhooks_lite
    author = (
        dhooks_lite.Author(
            name=victim_organization if victim_organization else "?",
            url=victim_org_url,
            icon_url=victim_org_icon_url,
        )
        if victim_organization and victim_org_url and victim_org_icon_url
        else None
    )
    zkb_icon_url = static_file_absolute_url("killtracker/zkb_icon.png")
    embed = dhooks_lite.Embed(
        author=author,
        description=description,
        title=title,
        url=zkb_killmail_url,
        thumbnail=dhooks_lite.Thumbnail(url=thumbnail_url),
        footer=dhooks_lite.Footer(text="zKillboard", icon_url=zkb_icon_url),
        timestamp=killmail.time,
        color=embed_color,
    )
    return embed


def _character_zkb_link(
    tracker, entity_id: int, resolver: EveEntityNameResolver
) -> str:
    return tracker.webhook.create_message_link(
        name=resolver.to_name(entity_id), url=zkillboard.character_url(entity_id)
    )


def _corporation_zkb_link(
    tracker, entity_id: int, resolver: EveEntityNameResolver
) -> str:
    return tracker.webhook.create_message_link(
        name=resolver.to_name(entity_id), url=zkillboard.corporation_url(entity_id)
    )


def _alliance_zkb_link(tracker, entity_id: int, resolver: EveEntityNameResolver) -> str:
    return tracker.webhook.create_message_link(
        name=resolver.to_name(entity_id), url=zkillboard.alliance_url(entity_id)
    )
