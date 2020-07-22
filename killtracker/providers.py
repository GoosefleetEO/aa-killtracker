from esi.clients import EsiClientProvider

from allianceauth.services.hooks import get_extension_logger

from . import __title__
from .utils import LoggerAddTag, get_swagger_spec_path


logger = LoggerAddTag(get_extension_logger(__name__), __title__)

esi = EsiClientProvider(spec_file=get_swagger_spec_path())
