import argparse
import logging
from . import tomlconfig
from .kismet_ingester import KismetIngester
from .utils import dict_get_deep


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="python_gravwell_kismet_ingester",
        description="transfer kismet data to gravwell for ingest",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="logging will not output to stdout"
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

    if not args.quiet:
        logging.basicConfig(
            format="[%(asctime)s] %(module)s - %(message)s",
            datefmt="%H:%M:%S",
        )

    k = KismetIngester(config)
    k.start()
