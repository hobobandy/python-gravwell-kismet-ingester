import asyncio
import base64
import httpx
import json
import logging
import threading
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlunparse
from .tomlconfig import dict_get_deep
from .utils import suppress_asyncio_cancelled_error

logger = logging.getLogger(__name__)


VALID_KISMET_PHY = (
    "phydot11_accesspoints",
    "phy-IEEE802.11",
    "phy-RFSENSOR",
    "phy-Z-Wave",
    "phy-Bluetooth",
    "phy-UAV",
    "phy-NrfMousejack",
    "phy-BTLE",
    "phy-METER",
    "phy-ADSB",
    "phy-802.15.4",
    "phy-RADIATION",
)


class KismetIngester:
    def __init__(self, config):
        self.config = config
        self.scheduler = AsyncIOScheduler()

        if dict_get_deep(self.config, "logging.debug", False):
            logging_level = logging.DEBUG
        else:
            logging_level = logging.INFO

        logger.setLevel(logging_level)

        self._sleep_loop = threading.Event()
        self._timestamps = {"devices_by_phy": {}}
        for phy in VALID_KISMET_PHY + ("all",):
            self._timestamps["devices_by_phy"][phy] = -1 * dict_get_deep(
                self.config, "general.backtrack", 60
            )

    async def start(self):
        # Prepare singleton-style clients
        logger.info("Preparing clients...")
        # Re-use the same client instances to get the most benefit from connection pooling
        self._client = httpx.AsyncClient()
        self._client_gw = httpx.AsyncClient(headers=self.gw_headers())

        await self.kismet_validate_creds()
        await self.gravwell_validate_creds()

        self.create_tasks()
        await self.start_tasks()

    async def stop(self):
        if not self._sleep_loop.is_set():
            logger.info("Stopping asyncio sleep loop.")
            self._sleep_loop.set()

    async def start_tasks(self):
        logger.info("Starting tasks... Press Ctrl+C to exit.")
        self.scheduler.start()

        # Keep alive for scheduler to run, otherwise script exits
        self._sleep_loop.clear()
        while not self._sleep_loop.is_set():
            await asyncio.sleep(0)

    def create_tasks(self):
        logger.info("Creating tasks...")
        if self.config["kismet"]["ingest"]["system_status"]:
            interval = dict_get_deep(self.config, "kismet.intervals.system_status", 10)
            self.scheduler.add_job(
                self.kismet_system_status, trigger="interval", seconds=interval
            )
            logger.info(f"Scheduled `System Status` every {interval} seconds...")
        if self.config["kismet"]["ingest"]["channels_summary"]:
            interval = dict_get_deep(
                self.config, "kismet.intervals.channels_summary", 10
            )
            self.scheduler.add_job(
                self.kismet_channels_summary, trigger="interval", seconds=interval
            )
            logger.info(f"Scheduled `Channels Summary` every {interval} seconds...")
        if self.config["kismet"]["ingest"]["devices"]:
            interval = dict_get_deep(self.config, "kismet.intervals.devices", 10)
            if self.config["kismet"]["ingest"]["devices_all"]:
                self.scheduler.add_job(
                    self.kismet_devices_by_phy,
                    args=("all",),
                    trigger="interval",
                    seconds=interval,
                )
                logger.info(
                    f"Scheduled `Devices by PHY (all)` every {interval} seconds..."
                )
            else:
                phy_to_ingest = dict_get_deep(
                    self.config, "kismet.ingest.devices_phy", {}
                )
                if not phy_to_ingest:
                    logger.info("No PHY configured to ingest.")
                else:
                    for phy in phy_to_ingest:
                        if phy in VALID_KISMET_PHY:
                            self.scheduler.add_job(
                                self.kismet_devices_by_phy,
                                args=(phy,),
                                trigger="interval",
                                seconds=interval,
                            )
                            logger.info(
                                f"Scheduled `Devices by PHY ({phy})` every {interval} seconds..."
                            )

    @lru_cache
    def gw_headers(self):
        return {"Gravwell-Token": self.config["gravwell"]["token"]}

    def kismet_build_endpoint_uri(self, endpoint, scheme="http"):
        netloc = f"{self.config['kismet']['host']}:{self.config['kismet']['port']}"
        query = f"KISMET={self.config['kismet']['apikey']}"
        return urlunparse((scheme, netloc, endpoint, None, query, None))

    def gw_build_endpoint_uri(self, endpoint):
        netloc = f"{self.config['gravwell']['host']}:{self.config['gravwell']['port']}"
        return urlunparse(("http", netloc, endpoint, None, None, None))

    async def kismet_validate_creds(self):
        uri = self.kismet_build_endpoint_uri("/session/check_login")
        try:
            r = await self._client.get(uri)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.critical("HTTP error during validation of Kismet credentials:")
            logger.critical(e)
            raise SystemExit()
        except httpx.ReadTimeout:
            logger.critical("HTTP request timed out - Kismet server alive? This may be caused by a big/slow API response.")
            raise SystemExit()

    async def gravwell_validate_creds(self):
        uri = self.gw_build_endpoint_uri("/api/tags")
        try:
            r = await self._client_gw.get(uri)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.critical("HTTP error during validation of Gravwell credentials:")
            logger.critical(e)
            raise SystemExit()
        except httpx.ReadTimeout:
            logger.critical("HTTP request timed out - Kismet server alive? This may be caused by a big/slow API response.")
            raise SystemExit()

    async def gravwell_put_ingest_entity(self, tag: str, data: str) -> bool:
        uri = self.gw_build_endpoint_uri("/api/ingest/json")
        ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        b64_data = base64.b64encode(
            bytes(data, "utf-8")
        )  # gravwell requires base64 encoded data
        entity_json = (
            {"TS": ts, "Tag": tag, "Data": b64_data.decode("utf-8")},
        )  # convert b64 bytes to a string representation
        r = await self._client_gw.put(uri, json=entity_json)
        r.raise_for_status()

    async def kismet_get_endpoint(self, uri: str, tag: str) -> None:
        r = await self._client.get(uri)
        r.raise_for_status()
        return r

    async def kismet_post_endpoint(self, uri: str, tag: str, data: dict) -> None:
        r = await self._client.post(uri, json=data)
        r.raise_for_status()
        return r

    @suppress_asyncio_cancelled_error
    async def kismet_system_status(self) -> None:
        uri = self.kismet_build_endpoint_uri("/system/status.json")
        tag = dict_get_deep(self.config, "gravwell.tags.kismet_status", "kismet-status")
        logger.info("System Status - Running...")
        try:
            r = await self.kismet_get_endpoint(uri, tag)
            await self.gravwell_put_ingest_entity(tag, r.text)
        except httpx.HTTPStatusError as e:
            logger.critical("System Status - HTTP error during task:")
            logger.critical(e)
            logger.critical("Exiting due to unrecoverable error...")
            await self.stop()
        except httpx.ReadTimeout:
            logger.critical("HTTP request timed out - Kismet server alive? This may be caused by a big/slow API response.")
            logger.critical("Exiting due to unrecoverable error...")
            await self.stop()

    @suppress_asyncio_cancelled_error
    async def kismet_channels_summary(self) -> None:
        uri = self.kismet_build_endpoint_uri("/channels/channels.json")
        tag = dict_get_deep(
            self.config,
            "gravwell.tags.kismet_channels_summary",
            "kismet-channels_summary",
        )
        logger.info("Channels Summary - Running...")
        try:
            r = await self.kismet_get_endpoint(uri, tag)
            await self.gravwell_put_ingest_entity(tag, r.text)
        except httpx.HTTPStatusError as e:
            logger.critical("Channels Summary - HTTP error during task:")
            logger.critical(e)
            logger.critical("Exiting due to unrecoverable error...")
            await self.stop()
        except httpx.ReadTimeout:
            logger.critical("HTTP request timed out - Kismet server alive? This may be caused by a big/slow API response.")
            logger.critical("Exiting due to unrecoverable error...")
            await self.stop()

    @suppress_asyncio_cancelled_error
    async def kismet_devices_by_phy(self, phy: str) -> None:
        timestamp = self._timestamps["devices_by_phy"][phy]
        endpoint_str = f"/devices/views/{phy}/last-time/{timestamp}/devices.json"
        uri = self.kismet_build_endpoint_uri(endpoint_str)
        tag = dict_get_deep(
            self.config, f"gravwell.tags.kismet.phy.{phy}", "kismet-device"
        )
        logger.info(f"Devices by PHY ({phy}): Running...")
        try:
            # @todo there may be duplicate data? any way to find timestamp from response?
            self._timestamps["devices_by_phy"][phy] = int(datetime.now().timestamp())
            # Field simplification, highly recommended to prevent Kismet hangups
            # From most restrictive (PHY), to recommended (common), to failsafe (all fields)
            data = {}
            fields = dict_get_deep(self.config, f"kismet.fields.devices.{phy}")
            if not fields:
                fields = dict_get_deep(self.config, f"kismet.fields.devices.common")
            if fields:
                data["fields"] = fields
            r = await self.kismet_post_endpoint(uri, tag, data)
            resp = r.json()
            logger.info(f"{len(resp)} devices updated since last run.")
            for d in resp:
                data = json.dumps(d)
                await self.gravwell_put_ingest_entity(tag, data)
        except httpx.HTTPStatusError as e:
            logger.critical(f"HTTP error during Kismet `Devices by PHY {phy}` task:")
            logger.critical(e)
            logger.critical("Exiting due to unrecoverable error...")
            await self.stop()
        except httpx.ReadTimeout:
            logger.critical("HTTP request timed out - Kismet server alive? This may be caused by a big/slow API response.")
            logger.critical("Exiting due to unrecoverable error...")
            await self.stop()


def start_kismet_ingester(config: dict) -> None:
    k = KismetIngester(config)
    with asyncio.Runner() as runner:
        try:
            runner.run(k.start())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Received exit request, exiting...")
        finally:
            runner.run(k.stop())
