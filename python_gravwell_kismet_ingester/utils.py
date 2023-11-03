from functools import reduce
from typing import Any


def dict_get_deep(_dict: dict, keys: str, default: Any = None) -> Any:
    # Utility function to safely deep get a key, return default if it does not exist
    # Example: dict_deep_get({key1: {key2: {key3: "hello"}}}, "key1.key2.key3", "goodbye")
    return reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        keys.split("."),
        _dict,
    )
