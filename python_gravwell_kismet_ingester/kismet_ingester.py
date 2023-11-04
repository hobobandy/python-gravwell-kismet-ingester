import asyncio
import base64
import contextlib
import functools
import httpx
import json
import logging
import threading
import websockets
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlunparse
from .tomlconfig import dict_get_deep


logger = logging.getLogger(__name__)


class KismetIngester:
    def __init__(self, config):
        self.config = config
        self._sleep_loop = threading.Event()

        if dict_get_deep(self.config, "logging.debug", False):
            logging_level = logging.DEBUG
        else:
            logging_level = logging.INFO

        logger.setLevel(logging_level)
    
    def suppress_asyncio_cancelled_error(func):
        """Decorator to reduce code repetition. Suppresses task cancellation exception.
        """
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Suppressing exception, no concerns since in control of the asyncio loop
            with contextlib.suppress(asyncio.CancelledError):
                await func(*args, **kwargs)
        return wrapper

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

    async def start_tasks(self):
        logger.info("Creating tasks...")
        self.scheduler = AsyncIOScheduler()
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

        logger.info("Starting tasks... Press Ctrl+C to exit.")
        self.scheduler.start()
        self._sleep_loop.clear()
        while not self._sleep_loop.is_set():
            await asyncio.sleep(0) # Keep alive for scheduler to run, otherwise script exits.

    async def start(self):
        # Prepare singleton-style clients
        logger.info("Preparing clients...")
        # Re-use the same client instances to get the most benefit from connection pooling
        self._client = httpx.AsyncClient()
        self._client_gw = httpx.AsyncClient(headers=self.gw_headers())
        
        # await self.kismet_validate_creds()
        # await self.gravwell_validate_creds()
        
        await self.start_tasks()

    async def stop(self):
        if not self._sleep_loop.is_set():
            logger.info("Stopping asyncio sleep loop.")
            self._sleep_loop.set()
    
    async def kismet_validate_creds(self):
        uri = self.kismet_build_endpoint_uri("/session/check_login")
        try:
            r = await self._client.get(uri)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.critical("HTTP error during validation of Kismet credentials:")
            logger.critical(e)
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

    async def gravwell_put_ingest_entity(self, tag: str, data: json) -> bool:
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
        await self.gravwell_put_ingest_entity(tag, r.text)

    @suppress_asyncio_cancelled_error
    async def kismet_system_status(self) -> None:
        uri = self.kismet_build_endpoint_uri("/system/status.json")
        tag = dict_get_deep(self.config, "gravwell.tags.kismet_status", "kismet-status")
        logger.info("System Status: Running...")
        try:
            await self.kismet_get_endpoint(uri, tag)
        except httpx.HTTPStatusError as e:
            logger.critical("HTTP error during Kismet system status task:")
            logger.critical(e)
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
        logger.info("Channels Summary: Running...")
        try:
            await self.kismet_get_endpoint(uri, tag)
        except httpx.HTTPStatusError as e:
            logger.critical("HTTP error during Kismet channels summary task:")
            logger.critical(e)
            logger.critical("Exiting due to unrecoverable error...")
            await self.stop()

    # @todo decorator to handle exceptions?
    async def kismet_ws_monitor_all_devices(self) -> None:
        # @todo helper methods per PHY from "/devices/views/all_views.json" result at init?
        uri = self.kismet_build_endpoint_uri("/devices/views/all/monitor.ws", scheme="ws")
        tag = dict_get_deep(self.config, "gravwell.tags.kismet_device", "kismet-device")
        async with websockets.connect(uri) as websocket:
            logger.info("Subscribing to all device changes...")
            req = {"monitor": "*"}  # wildcard, get updates for ALL devices
            req["request"] = dict_get_deep(
                self.config, "kismet.websockets.request", 31337
            )
            req["rate"] = dict_get_deep(self.config, "kismet.websockets.rate", 1)
            req["fields"] = dict_get_deep(
                self.config, "kismet.fields.devices_IEEE80211", {}
            )
            await websocket.send(json.dumps(req))
            logger.info("Success! Waiting for updates...")
            while websocket.open:
                r = await websocket.recv()
                await self.gravwell_put_ingest_entity(tag, r)
                logger.info("Device update sent to Gravwell...")


def start_kismet_ingester(config: dict) -> None:
    k = KismetIngester(config)
    with asyncio.Runner() as runner:
        try:
            runner.run(k.start())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Received exit request, exiting...")
        finally:
            runner.run(k.stop())
