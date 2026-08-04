"""Account logo upload: validation, normalisation and storage.

This is the only endpoint in the product that accepts a user-supplied image and
serves it back, so it is treated as hostile input throughout.

The central decision is that the bytes we store are never the bytes we were
given. Every upload is decoded and re-encoded to PNG, which:

  - strips EXIF, colour profiles and any trailing appended data,
  - defeats polyglot files (a valid GIF that is also valid JavaScript, say),
  - normalises the served content type, so a spoofed extension buys nothing.

SVG is rejected outright rather than sanitised. It is a document format that
can carry script and external references; there is no version of "clean SVG"
worth maintaining for a logo when raster formats do the job.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# Comfortably above any real logo. The cap is enforced on the raw upload before
# decoding, so an oversized file is rejected without being handed to Pillow.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

# Formats we are willing to decode. Deliberately narrow, and deliberately
# excludes SVG (script-bearing) and ICO/TIFF (multi-frame parsers, more surface
# than a logo justifies).
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})

# Logos render at 44px in the masthead and are unlikely to exceed a few hundred
# pixels anywhere. Downscaling caps both the stored object and the decoded
# bitmap a client has to hold.
MAX_DIMENSION = 512

# Guards against decompression bombs: a small file that decodes to an enormous
# bitmap. Pillow warns above this and we refuse outright.
MAX_PIXELS = 8000 * 8000


class LogoRejected(Exception):
    """Upload failed validation. The message is safe to return to the caller."""


def normalise_logo(raw: bytes) -> bytes:
    """Validate *raw* and return clean PNG bytes.

    Raises LogoRejected with a user-facing reason.
    """
    if not raw:
        raise LogoRejected("That file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise LogoRejected("Logos must be 2MB or smaller.")

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a pinned dependency
        logger.exception("Pillow is unavailable, so logo uploads cannot be validated")
        raise LogoRejected("Image processing is unavailable right now.")

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

    # verify() must run on a fresh handle and leaves the image unusable after,
    # so the file is opened twice: once to authenticate it, once to convert it.
    try:
        probe = Image.open(io.BytesIO(raw))
        detected = probe.format
        probe.verify()
    except Exception:
        # Covers truncated files, spoofed extensions and anything Pillow cannot
        # parse. The caller only needs to know it was not a usable image.
        raise LogoRejected("That file is not a readable image.")

    if detected not in ALLOWED_FORMATS:
        raise LogoRejected("Logos must be a PNG, JPG or WEBP file.")

    try:
        image = Image.open(io.BytesIO(raw))
        # RGBA preserves transparency, which most logos rely on. Palette and
        # greyscale sources convert cleanly into it.
        image = image.convert("RGBA")
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

        out = io.BytesIO()
        # No EXIF is carried across: save() writes only what the Image holds,
        # and convert() dropped the original metadata.
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except LogoRejected:
        raise
    except Exception:
        logger.exception("Logo re-encode failed for a %s upload", detected)
        raise LogoRejected("That image could not be processed.")


def logo_path(user_id: str) -> str:
    """Storage path for a user's logo.

    One object per account, overwritten on re-upload, so an account cannot
    accumulate orphaned objects by uploading repeatedly.
    """
    return f"{user_id}.png"
