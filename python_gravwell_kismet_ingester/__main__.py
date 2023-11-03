import argparse
from . import tomlconfig
from .kismet_ingester import KismetIngester

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="python_gravwell_kismet_ingester",
        description="transfer kismet data to gravwell for ingest",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="FILE",
        help="configuration file to override default settings",
        required=True,
    )

    args = parser.parse_args()

    config = tomlconfig.load("config/default.toml")

    if args.config:
        config = tomlconfig.load(args.config, config)

    k = KismetIngester(config)
    k.start()
