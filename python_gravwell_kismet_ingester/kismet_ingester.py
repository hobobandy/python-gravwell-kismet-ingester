import asyncio
import base64
import httpx
import json
import logging
import websockets
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlunparse
from .utils import dict_get_deep


logger = logging.getLogger(__name__)


class KismetIngester:
    def __init__(self, config):
        self.config = config

        if dict_get_deep(self.config, "logging.debug", False):
            logging_level = logging.DEBUG
        else:
            logging_level = logging.INFO

        logger.setLevel(logging_level)

    # unused for now since websocket required query KISMET, but keeping just in case
    # @lru_cache
    # def kismet_cookies(self):
    #     return {'KISMET': self.config['kismet']['apikey']}

    @lru_cache
    def gw_headers(self):
        return {"Gravwell-Token": self.config["gravwell"]["token"]}

    def kismet_endpoint_uri(self, endpoint, scheme="http"):
        netloc = f"{self.config['kismet']['host']}:{self.config['kismet']['port']}"
        query = f"KISMET={self.config['kismet']['apikey']}"
        return urlunparse((scheme, netloc, endpoint, None, query, None))

    def gw_endpoint_uri(self, endpoint):
        netloc = f"{self.config['gravwell']['host']}:{self.config['gravwell']['port']}"
        return urlunparse(("http", netloc, endpoint, None, None, None))

    def validate_kismet_creds(self):
        uri = self.kismet_endpoint_uri("/session/check_login")
        with httpx.Client() as client:
            client.get(uri).raise_for_status()

    def validate_gravwell_creds(self):
        uri = self.gw_endpoint_uri("/api/tags")
        with httpx.Client(headers=self.gw_headers()) as client:
            client.get(uri).raise_for_status()

    async def start_tasks(self):
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

        logging.info("Press Ctrl+C to exit")
        self.scheduler.start()
        while True:
            await asyncio.sleep(1000)

    def start(self):
        # Raises an exception if creds are invalid, or another error happens
        self.validate_kismet_creds()
        self.validate_gravwell_creds()

        # @todo prebuild endpoint URIs and cache them?

        try:
            asyncio.run(self.start_tasks())
        except (KeyboardInterrupt, SystemExit):
            pass

    def stop(self):
        pass

    async def gravwell_put_ingest_entity(self, tag, data):
        uri = self.gw_endpoint_uri("/api/ingest/json")
        async with httpx.AsyncClient(headers=self.gw_headers()) as client:
            ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            b64_data = base64.b64encode(
                bytes(data, "utf-8")
            )  # gravwell requires base64 encoded data
            entity_json = (
                {"TS": ts, "Tag": tag, "Data": b64_data.decode("utf-8")},
            )  # convert b64 bytes to a string representation

            r = await client.put(uri, json=entity_json)

        if r.status_code == 200:
            return True
        else:
            raise Exception(
                f"Failed to send data to Gravwell: {r.text}"
            )  # @todo confirm what this would look like, should we just return False?

    async def kismet_ws_monitor_all_devices(self):
        # @todo helper methods per PHY from "/devices/views/all_views.json" result at init?
        uri = self.kismet_endpoint_uri("/devices/views/all/monitor.ws", scheme="ws")
        tag = dict_get_deep(self.config, "gravwell.tags.kismet_device", "kismet-device")
        async with websockets.connect(uri) as websocket:
            print("Subscribing to all device changes...")
            req = {"monitor": "*"}  # wildcard, get updates for ALL devices
            req["request"] = dict_get_deep(
                self.config, "kismet.websockets.request", 31337
            )
            req["rate"] = dict_get_deep(self.config, "kismet.websockets.rate", 1)
            req["fields"] = dict_get_deep(
                self.config, "kismet.fields.devices_IEEE80211", {}
            )
            await websocket.send(json.dumps(req))
            print("Success! Waiting for updates...")
            while websocket.open:
                r = await websocket.recv()
                await self.gravwell_put_ingest_entity(tag, r)
                print("Device update sent to Gravwell...")

    async def kismet_system_status(self):
        uri = self.kismet_endpoint_uri("/system/status.json")
        tag = dict_get_deep(self.config, "gravwell.tags.kismet_status", "kismet-status")
        logger.info("System Status: Running...")
        async with httpx.AsyncClient() as client:
            r = await client.get(uri)
            await self.gravwell_put_ingest_entity(tag, r.text)

    async def kismet_channels_summary(self):
        uri = self.kismet_endpoint_uri("/channels/channels.json")
        tag = dict_get_deep(
            self.config,
            "gravwell.tags.kismet_channels_summary",
            "kismet-channels_summary",
        )
        logger.info("Channels Summary: Running...")
        async with httpx.AsyncClient() as client:
            r = await client.get(uri)
            await self.gravwell_put_ingest_entity(tag, r.text)
