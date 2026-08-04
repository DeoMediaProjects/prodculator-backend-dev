"""Account logo upload.

The validation tests matter more than the routing ones: this is the only place
in the product that accepts a user-supplied image and serves it back, so the
cases below are the attacks it is meant to stop, not just shape checks.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.modules.auth.logo import LogoRejected, logo_path, normalise_logo


def _image_bytes(fmt: str = "PNG", size: tuple[int, int] = (300, 120)) -> bytes:
    buf = io.BytesIO()
    mode = "RGB" if fmt == "JPEG" else "RGBA"
    Image.new(mode, size, (200, 160, 60)).save(buf, fmt)
    return buf.getvalue()


class TestAccepted:
    def test_png_is_accepted_and_returned_as_png(self):
        out = normalise_logo(_image_bytes("PNG"))
        assert out[:8] == b"\x89PNG\r\n\x1a\n"

    def test_jpeg_is_converted_to_png(self):
        out = normalise_logo(_image_bytes("JPEG"))
        assert out[:8] == b"\x89PNG\r\n\x1a\n"

    def test_oversized_image_is_downscaled(self):
        out = normalise_logo(_image_bytes("PNG", (2000, 2000)))
        assert max(Image.open(io.BytesIO(out)).size) <= 512

    def test_small_image_is_not_upscaled(self):
        out = normalise_logo(_image_bytes("PNG", (64, 64)))
        assert Image.open(io.BytesIO(out)).size == (64, 64)


class TestRejected:
    def test_svg_is_rejected(self):
        """SVG can carry script and external references. It is refused rather
        than sanitised: there is no 'clean SVG' worth maintaining for a logo."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with pytest.raises(LogoRejected):
            normalise_logo(svg)

    def test_non_image_is_rejected(self):
        with pytest.raises(LogoRejected):
            normalise_logo(b"MZ\x90\x00" + b"\x00" * 500)

    def test_empty_file_is_rejected(self):
        with pytest.raises(LogoRejected):
            normalise_logo(b"")

    def test_oversized_upload_is_rejected_before_decoding(self):
        with pytest.raises(LogoRejected, match="2MB"):
            normalise_logo(b"x" * (3 * 1024 * 1024))

    def test_truncated_image_is_rejected(self):
        with pytest.raises(LogoRejected):
            normalise_logo(_image_bytes("PNG")[:40])


class TestPayloadStripping:
    def test_appended_payload_does_not_survive_re_encoding(self):
        """A valid image with a payload appended is the polyglot case: it passes
        any signature check, so the defence is that we never serve the original
        bytes back."""
        payload = b"<script>alert(1)</script>"
        out = normalise_logo(_image_bytes("PNG") + payload)
        assert payload not in out

    def test_exif_does_not_survive_re_encoding(self):
        buf = io.BytesIO()
        img = Image.new("RGB", (120, 120), (10, 10, 10))
        exif = img.getexif()
        exif[0x010E] = "secret location data"
        img.save(buf, "JPEG", exif=exif)

        out = normalise_logo(buf.getvalue())
        assert b"secret location data" not in out
        assert not Image.open(io.BytesIO(out)).getexif()


def test_logo_path_is_per_account_so_reuploads_overwrite():
    """One object per account. Without this an account could accumulate orphaned
    objects simply by uploading repeatedly."""
    assert logo_path("user-1") == logo_path("user-1")
    assert logo_path("user-1") != logo_path("user-2")
