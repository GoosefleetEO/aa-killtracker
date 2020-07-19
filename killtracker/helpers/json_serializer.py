# Decoding/encoding datetime values in JSON data in Python.
# Code adapted from: https://gist.github.com/abhinav-upadhyay/5300137

from datetime import datetime
import json
from json import JSONDecoder
from json import JSONEncoder
from pytz import timezone
from typing import Any


class JsonDateTimeDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        JSONDecoder.__init__(self, object_hook=self.dict_to_object, *args, **kwargs)

    def dict_to_object(self, dct: dict) -> object:
        if "__type__" not in dct:
            return dct

        type_str = dct.pop("__type__")
        zone, _ = dct.pop("tz")
        dct["tzinfo"] = timezone(zone)
        try:
            dateobj = datetime(**dct)
            return dateobj
        except Exception:
            dct["__type__"] = type_str
            return dct


class JsonDateTimeEncoder(JSONEncoder):
    """ Instead of letting the default encoder convert datetime to string,
        convert datetime objects into a dict, which can be decoded by the
        JsonDateTimeDecoder
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return {
                "__type__": "datetime",
                "year": o.year,
                "month": o.month,
                "day": o.day,
                "hour": o.hour,
                "minute": o.minute,
                "second": o.second,
                "microsecond": o.microsecond,
                "tz": (o.tzinfo.tzname(o), o.utcoffset().total_seconds()),
            }
        else:
            return JSONEncoder.default(self, o)
