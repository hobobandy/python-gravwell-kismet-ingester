import tomllib
from functools import reduce
from pathlib import Path
from typing import Any, Callable


def dict_merge(d1: dict, d2: dict) -> dict:
    # Recursive dict merge
    for k, v in d2.items():
        if k in d1 and isinstance(d1[k], dict) and isinstance(v, dict):
            dict_merge(d1[k], v)
        else:
            d1[k] = v
    return d1


def dict_get_deep(_dict: dict, keys: str, default: Any = None) -> Any:
    # Safely get a key in depth, return default if it does not exist
    # Example: dict_deep_get({key1: {key2: {key3: "hello"}}}, "key1.key2.key3", "goodbye")
    return reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        keys.split("."),
        _dict,
    )


def dict_type_conv_by_prefix(d: dict, prefix: str, _type: Callable) -> dict:
    for k, v in d.items():
        if k == "tomlconfig":  # values in this section shouldn't be modified
            continue
        elif isinstance(v, dict):
            dict_type_conv_by_prefix(v, prefix, _type)
        elif k.startswith(prefix):
            d[k] = _type(v)
    return d


def load(config_path: str | Path, to_override: str | Path | dict = None) -> dict:
    if isinstance(config_path, str):
        config_path = Path(config_path)
    elif isinstance(config_path, Path):
        pass
    else:
        raise ValueError("Invalid configuration path, must be a str or Path object.")

    if isinstance(to_override, str):
        config = load(Path(to_override))
    elif isinstance(to_override, Path):
        config = load(to_override)
    elif isinstance(to_override, dict):
        config = to_override
    else:
        config = dict()

    with config_path.open("rb") as f:
        f_toml_dict = tomllib.load(f)

    dict_merge(config, f_toml_dict)

    try:
        for prefix in config["tomlconfig"]["path_prefix"]:
            dict_type_conv_by_prefix(config, prefix, Path)
    except KeyError:
        pass

    return config
