from pathlib import Path

from esi.clients import EsiClientProvider

from allianceauth.services.hooks import get_extension_logger
from app_utils.logging import LoggerAddTag

from . import USER_AGENT_TEXT, __title__

logger = LoggerAddTag(get_extension_logger(__name__), __title__)

spec_file_path = Path(__file__).parent / "swagger.json"
esi = EsiClientProvider(spec_file=spec_file_path, app_info_text=USER_AGENT_TEXT)
