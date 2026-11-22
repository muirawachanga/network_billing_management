"""Validation and public projection for MikroTik captive portal banners."""

import ipaddress
import os
import re
import stat
import struct
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit

import frappe
from frappe.utils import now_datetime


BANNER = "MikroTik Banner"
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_SIDE = 1920
MAX_IMAGE_PIXELS = 1920 * 1080
MAX_BANNERS = 4
MAX_CANDIDATES = 12
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I | re.ASCII)
BAD_URL_ESCAPE = re.compile(r"%(?![0-9a-f]{2})|%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)", re.I)


def _text(value, label, limit, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{label} must be {'provided and ' if required else ''}at most {limit} characters.")
    return value.strip()


def _target(value):
    url = _text(value, "Target URL", 2048)
    if not url:
        return ""
    error = "Target URL must be an absolute HTTPS URL without credentials."
    if (
        url != value
        or any(not 33 <= ord(char) <= 126 or char in "\\<>\"'" for char in url)
        or BAD_URL_ESCAPE.search(url)
    ):
        raise ValueError(error)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        if (
            parts.scheme != "https" or not host or parts.username is not None
            or parts.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError
        if parts.netloc.startswith("["):
            if not re.fullmatch(r"\[[0-9a-f:.]+\](?::[0-9]+)?", parts.netloc, re.I):
                raise ValueError
            ipaddress.IPv6Address(host)
        else:
            labels = host.removesuffix(".").split(".")
            if len(host) > 253 or not all(HOST_LABEL.fullmatch(label) for label in labels):
                raise ValueError
            if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", labels[-1], re.I):
                ipaddress.IPv4Address(host.removesuffix("."))
    except ValueError:
        raise ValueError(error) from None
    return url


def _validate_schedule(record):
    def site_datetime(value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is not None:
            raise ValueError
        return value

    try:
        starts_at = site_datetime(record.get("starts_at"))
        ends_at = site_datetime(record.get("ends_at"))
    except ValueError:
        raise ValueError("Use site-local date and time for the banner schedule.") from None
    if starts_at and ends_at and starts_at >= ends_at:
        raise ValueError("End time must be after start time.")


def _image_path(raw_url):
    if not isinstance(raw_url, str) or not raw_url.startswith("/files/"):
        raise ValueError("Upload a public banner image instead of linking to one.")
    name = raw_url[len("/files/"):]
    if (
        not name or name != name.strip() or name.startswith(".")
        or any(char in "/\\%?#:" or not char.isprintable() for char in name)
        or len(name.encode("utf-8")) > 255
    ):
        raise ValueError("Use a direct public file path for the banner image.")
    image_format = IMAGE_FORMATS.get(Path(name).suffix.lower())
    if not image_format:
        raise ValueError("Banner images must be PNG, JPEG, or WebP.")
    return name, image_format


def _image_bytes(name):
    path = frappe.get_site_path("public", "files", name)
    # Do not follow a symlink or block on a special file in the public files directory.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as image:
        info = os.fstat(image.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
            raise ValueError("Banner images must be regular, non-empty files of at most 2 MiB.")
        content = image.read(MAX_IMAGE_BYTES + 1)
    if not 0 < len(content) <= MAX_IMAGE_BYTES:
        raise ValueError("Banner images must be non-empty and at most 2 MiB.")
    return content


def _validate_raster(content, image_format):
    # Frappe ships Pillow. Limit the available decoders as well as the size before decoding.
    from PIL import Image

    try:
        with Image.open(BytesIO(content), formats=[image_format]) as image:
            width, height = image.size
            if (
                not 0 < width <= MAX_IMAGE_SIDE or not 0 < height <= MAX_IMAGE_SIDE
                or width * height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("Banner images must be still and fit within 1920 pixels per side and 2,073,600 pixels total.")
            image.verify()
        # verify() alone does not detect all corrupt raster data.
        with Image.open(BytesIO(content), formats=[image_format]) as image:
            image.load()
    except (
        OSError, SyntaxError, EOFError, RuntimeError, struct.error,
        Image.DecompressionBombError, Image.DecompressionBombWarning,
    ):
        raise ValueError("Banner image content must be a valid raster matching its extension.") from None


def _public_image(record):
    raw_url = record.get("image")
    name, image_format = _image_path(raw_url)
    attachment = frappe.db.get_value(
        "File", {
            "file_url": raw_url, "is_private": 0, "is_folder": 0,
            "attached_to_doctype": BANNER, "attached_to_name": record.get("name"),
            "attached_to_field": "image",
        }, ["file_url", "attached_to_name", "attached_to_field", "attached_to_doctype"], as_dict=True,
    )
    if not attachment or any(attachment.get(field) != exact for field, exact in (
        ("file_url", raw_url), ("attached_to_name", record.get("name")),
        ("attached_to_field", "image"), ("attached_to_doctype", BANNER),
    )):
        raise ValueError("Upload a public file to this banner's Image field.")
    try:
        content = _image_bytes(name)
    except OSError:
        raise ValueError("The banner image is not available in this site's public files.") from None
    _validate_raster(content, image_format)
    return "/files/" + quote(name)


def _payload(record, require_image):
    return {
        "title": _text(record.get("title"), "Title", 120, required=True),
        "description": _text(record.get("description"), "Description", 500),
        "display_price": _text(record.get("display_price"), "Display price", 40),
        "alt": _text(record.get("alt"), "Image alternative text", 160),
        "url": _target(record.get("target_url")),
        "image": _public_image(record) if require_image or record.get("image") else "",
    }


def _zone_exists(zone_id, enabled_only=False):
    if not isinstance(zone_id, str) or not zone_id.strip() or len(zone_id) > 140:
        return False
    filters = {"name": zone_id, "backend": "MikroTik"}
    if enabled_only:
        filters["enabled"] = 1
    return bool(frappe.db.exists("Captive Portal Zone", filters))


def validate_banner(record):
    try:
        if not _zone_exists(record.get("zone")):
            raise ValueError("Choose a MikroTik captive portal zone.")
        _validate_schedule(record)
        _payload(record, require_image=bool(record.get("enabled")))
    except ValueError as error:
        frappe.throw(str(error))


def published(zone_id):
    """Return only the public fields of banners scheduled for an enabled MikroTik zone."""
    if not _zone_exists(zone_id, enabled_only=True):
        return []
    at = now_datetime()
    candidates = frappe.db.sql(
        """
        SELECT name, title, description, display_price, image, alt, target_url
          FROM `tabMikroTik Banner`
         WHERE zone = %s AND enabled = 1
           AND (starts_at IS NULL OR starts_at <= %s)
           AND (ends_at IS NULL OR ends_at > %s)
         ORDER BY sort_order ASC, name ASC
         LIMIT %s
        """,
        (zone_id, at, at, MAX_CANDIDATES),
        as_dict=True,
    )
    result = []
    for record in candidates:
        try:
            result.append(_payload(record, require_image=True))
        except ValueError:
            # A file can disappear, become private, or be replaced after saving the banner.
            continue
        if len(result) == MAX_BANNERS:
            break
    return result


@frappe.whitelist(methods=["POST"])
def preview(name):
    """Preview the saved record regardless of its enabled flag or schedule."""
    doc = frappe.get_doc(BANNER, name)
    doc.check_permission("read")
    try:
        return _payload(doc, require_image=bool(doc.get("enabled")))
    except ValueError as error:
        frappe.throw(str(error))
