from enum import IntEnum

SESSION_KEY_TOOGLE_NPC = "killtracker_toogle_npc"
SESSION_KEY_USES_NPC = "killtracker_uses_npc"


class EveCategoryId(IntEnum):
    ENTITY = 11
    FIGHTER = 87
    SHIP = 6
    STRUCTURE = 65


class EveGroupId(IntEnum):
    MINING_DRONE = 101
    ORBITAL_INFRASTRUCTURE = 1025
