import asyncio
import logging
import mimetypes
import time
import uuid
from urllib.parse import quote

import boto3

from ..config import R2Settings, get_settings

logger = logging.getLogger("Mon3tr").getChild("utils.r2")


def is_configured() -> bool:
    return get_settings().r2 is not None


def _upload_sync(r2: R2Settings, path: str, filename: str) -> str:
    client = boto3.client(
        "s3",
        endpoint_url=r2.endpoint,
        aws_access_key_id=r2.access_key_id,
        aws_secret_access_key=r2.secret_access_key,
        region_name="auto",
    )
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    prefix = f"downloads/{uuid.uuid4().hex}"
    client.upload_file(
        path, r2.bucket, f"{prefix}/{filename}", ExtraArgs={"ContentType": content_type}
    )
    return f"{r2.public_base_url.rstrip('/')}/{prefix}/{quote(filename, safe='')}"


async def upload(path: str, filename: str) -> str:
    """Upload a file to R2 and return its public URL."""
    r2 = get_settings().r2
    if r2 is None:
        raise RuntimeError("R2 is not configured")
    t0 = time.perf_counter()
    url = await asyncio.to_thread(_upload_sync, r2, path, filename)
    logger.info("upload: done in %.2fs", time.perf_counter() - t0)
    return url
