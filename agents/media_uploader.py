"""Uploads a locally rendered image to Gettr's CDN so it can be attached to a
post.

Ported from China_Scandal_News_EN/agents/media_uploader.py (same VM, confirmed
working against the real Gettr API there), trimmed to the image case -- this
channel only ever uploads a headline card it drew itself, never a video and
never bytes fetched from somewhere else.

The real Gettr upload flow is a 4-step dance against a separate host
(upload.gettr.com):
  1. GET  /media/get_upload_channel  -> a GCS resumable-upload init URL + a
     notify URL to call once the bytes are up.
  2. POST that init URL (x-goog-resumable: start) -> a `Location` header,
     the actual upload session URL.
  3. PUT  the raw bytes to that Location.
  4. GET  the notify URL with the uploaded location -> media metadata
     (ori/screen/...) that agents/gettr_publisher.py builds its payload from.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.config import AppConfig

logger = logging.getLogger(__name__)

# Gettr's upload host wasn't confirmed to accept a bare/no user-agent by the
# reference this was ported from -- if uploads start failing with no other
# explanation, this is the first thing to try changing.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class MediaUploader:
    def __init__(self, config: AppConfig, dry_run: bool = False) -> None:
        self._config = config
        self._dry_run = dry_run

    async def upload_png(self, data: bytes, filename: str) -> dict | None:
        """Uploads PNG bytes and returns Gettr's media metadata dict
        (ori/screen/..., plus our own media_type key), or None on any failure
        -- fail open, same convention as every other best-effort network call
        in this codebase. The caller falls back to a link post."""
        gettr = self._config.gettr
        mime = "image/png"

        if self._dry_run or not gettr.user_id or not gettr.user_token:
            logger.info("[dry-run] would upload %s (%d bytes) to %s",
                        filename, len(data), gettr.media_upload_host)
            return {"ori": f"dry-run://{filename}", "screen": f"dry-run://{filename}",
                    "media_type": "image"}

        timeout = httpx.Timeout(30.0, read=60.0, write=300.0, pool=30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                channel = await client.get(
                    f"{gettr.media_upload_host}/media/get_upload_channel",
                    params={"scene": "getter"},
                    headers={
                        "filename": filename,
                        "authorization": gettr.user_token,
                        "userid": gettr.user_id,
                        "user-agent": _USER_AGENT,
                    },
                )
                channel.raise_for_status()
                # Gettr answers HTTP 200 with an empty body when it rejects the
                # request, so this is checked rather than assumed.
                channel_data = channel.json() if channel.text.strip() else {}
                init_url = (channel_data.get("gcs") or channel_data.get("gcp") or {}).get("url")
                notify_url = channel_data.get("notify_url")
                if not init_url or not notify_url:
                    logger.error(
                        "MediaUploader: get_upload_channel response missing gcs/gcp url or notify_url: %s",
                        channel_data,
                    )
                    return None

                init_resp = await client.post(
                    init_url,
                    headers={"x-goog-resumable": "start", "content-type": mime},
                    json={"unuse": 0},
                )
                init_resp.raise_for_status()
                location = init_resp.headers.get("location")
                if not location:
                    logger.error("MediaUploader: no Location header from GCS init request")
                    return None

                put_resp = await client.put(
                    location, headers={"content-type": mime}, content=data,
                )
                put_resp.raise_for_status()

                location_no_query = urlunsplit(urlsplit(location)._replace(query=""))
                notify_base = (
                    notify_url
                    if notify_url.startswith("http")
                    else f"{gettr.media_upload_host}/{notify_url.lstrip('/')}"
                )
                notify_resp = await client.get(
                    notify_base,
                    params={"uploadedurl": location_no_query, "result": "ok"},
                    headers={
                        "authorization": gettr.user_token,
                        "userid": gettr.user_id,
                        "origin": "https://gettr.com",
                    },
                )
                notify_resp.raise_for_status()
                media_data = notify_resp.json()

                if media_data.get("message") == "ERR_UPLOAD_FAILURE" or not (
                    media_data.get("ori") or media_data.get("screen")
                ):
                    logger.error("MediaUploader: upload failed, notify response: %s", media_data)
                    return None

                media_data["media_type"] = "image"
                return media_data
        except Exception as e:
            logger.error("MediaUploader: upload failed for %s: %s", filename, e)
            return None
