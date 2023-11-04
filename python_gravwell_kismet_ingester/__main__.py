import argparse
import logging
import os
from pathlib import Path
from . import tomlconfig
from .kismet_ingester import start_kismet_ingester


parser = argparse.ArgumentParser(
    prog="python_gravwell_kismet_ingester",
    description="transfer kismet data to gravwell for ingest",
)
parser.add_argument(
    "--quiet", action="store_true", help="logging will not output to stdout"
)
parser.add_argument(
    "--debug",
    action="store_true",
    help="increase root-level verbosity, for development purposes",
)
parser.add_argument(
    "-c",
    "--config",
    metavar="FILE",
    help="configuration file to override default settings",
    required=True,
)

args = parser.parse_args()

logger = logging.getLogger()

if not args.quiet:
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

if args.debug:
    logger.setLevel(logging.DEBUG)

try:
    config = tomlconfig.load(Path(__file__).parent / "default.toml")
except FileNotFoundError:
    logging.warning("Default config file missing, unexpected errors may happen.")
    config = {}
finally:
    config = tomlconfig.load(args.config, config)

logger.warning(f"Process ID: {os.getpid()}")

start_kismet_ingester(config)
