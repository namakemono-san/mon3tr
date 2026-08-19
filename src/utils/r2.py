import asyncio
import logging
import mimetypes
import os
import boto3
import time
import uuid
from urllib.parse import quote

logger = logging.getLogger("Mon3tr").getChild("utils.r2")


def r2_configured() -> bool:
    return bool(
        os.getenv("R2_ENDPOINT")
        and os.getenv("R2_ACCESS_KEY_ID")
        and os.getenv("R2_SECRET_ACCESS_KEY")
        and os.getenv("R2_BUCKET")
        and os.getenv("R2_PUBLIC_BASE_URL")
    )


def _upload_sync(filepath: str, filename: str) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=os.getenv("R2_ENDPOINT"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    uid = uuid.uuid4().hex
    key = f"downloads/{uid}/{filename}"
    s3.upload_file(
        filepath,
        os.getenv("R2_BUCKET", ""),
        key,
        ExtraArgs={"ContentType": content_type},
    )
    base_url = os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base_url}/downloads/{uid}/{quote(filename, safe='')}"


async def upload(filepath: str, filename: str) -> str:
    logger.info("upload: uploading %s", filename)
    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()
    url = await loop.run_in_executor(None, lambda: _upload_sync(filepath, filename))
    logger.info("upload: done in %.2fs", time.perf_counter() - t0)
    return url
