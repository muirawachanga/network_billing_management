"""Banner validation and publication without a running Frappe site."""

import importlib.util
import shutil
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

try:
    from PIL import Image, features
except ImportError:
    Image = features = None


class BannerFixture(unittest.TestCase):
    at = datetime(2026, 11, 22, 10, 30)

    def setUp(self):
        self.frappe = MagicMock()
        self.frappe.whitelist = lambda **kwargs: lambda method: method

        def throw(message):
            raise ValueError(message)

        self.frappe.throw.side_effect = throw
        self.frappe.db.exists.return_value = True
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: self.at
        path = Path(__file__).parents[1] / "network_billing_system/mikrotik_banners.py"
        spec = importlib.util.spec_from_file_location("_tested_mikrotik_banners", path)
        self.banners = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils}):
            spec.loader.exec_module(self.banners)
        self.banner = {
            "name": "banner-1", "zone": "wifi-1", "title": "Today’s offer",
            "description": "Buy a day pass.", "alt": "Day pass", "enabled": 0,
        }


class TestBannerRules(BannerFixture):
    def test_schedule_and_draft_validation(self):
        self.banners.validate_banner(self.banner)
        for invalid in (
            {"starts_at": self.at, "ends_at": self.at},
            {"starts_at": "2026-11-22T10:30:00+03:00"},
            {"starts_at": "yesterday"},
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "time"):
                self.banners.validate_banner(self.banner | invalid)
        with self.assertRaisesRegex(ValueError, "public banner image"):
            self.banners.validate_banner(self.banner | {"enabled": 1})
        self.frappe.db.exists.return_value = False
        with self.assertRaisesRegex(ValueError, "Choose a MikroTik"):
            self.banners.validate_banner(self.banner)

    def test_target_must_be_https_and_unambiguous(self):
        for url in ("https://example.com/offer?q=1#pass", "https://[2001:db8::1]:443/offer", ""):
            with self.subTest(url=url):
                self.banners.validate_banner(self.banner | {"target_url": url})
        for url in (
            "http://example.com", "//example.com", "javascript:alert(1)", "https:///x",
            "https://a:b@example.com/", "https://@example.com", "https://example.com:99999",
            "https://example.com..", "https://999.0.0.0", "https://example.com\\a",
            "https://ex%61mple.com/", "https://example.com/\n", "https://example.com/%0a",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "absolute HTTPS"):
                self.banners.validate_banner(self.banner | {"target_url": url})

    def test_display_price_is_optional_bounded_text(self):
        for price in (123, "x" * 41):
            with self.subTest(price=price), self.assertRaisesRegex(ValueError, "Display price"):
                self.banners.validate_banner(self.banner | {"display_price": price})

    def test_published_checks_enabled_mikrotik_zone(self):
        for bad_zone in ("", {"name": "wifi-1"}, None):
            self.assertEqual(self.banners.published(bad_zone), [])
        self.frappe.db.exists.return_value = False
        self.assertEqual(self.banners.published("wifi-1"), [])
        self.frappe.db.exists.assert_called_once_with(
            "Captive Portal Zone", {"name": "wifi-1", "backend": "MikroTik", "enabled": 1},
        )
        self.frappe.db.sql.assert_not_called()

    def test_published_filters_and_orders_bounded_projection(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE `tabMikroTik Banner` (
                name, zone, enabled, starts_at, ends_at, sort_order,
                title, description, image, alt, target_url, display_price
            )
        """)
        yesterday = self.at - timedelta(days=1)
        tomorrow = self.at + timedelta(days=1)
        rows = [
            ("a-foreign", "wifi-2", 1, None, None, -4),
            ("a-disabled", "wifi-1", 0, None, None, -1),
            ("a-scheduled", "wifi-1", 1, tomorrow, None, -1),
            ("a-expired", "wifi-1", 1, yesterday, self.at, -1),
            ("b-invalid", "wifi-1", 1, None, None, 0),
            ("a-tie-break", "wifi-1", 1, None, None, 1),
            ("z-starts-now", "wifi-1", 1, self.at, tomorrow, 1),
            *[(f"more-{i:02}", "wifi-1", 1, None, None, 2) for i in range(20)],
        ]
        def sqlite_values(row):
            return [str(value) if isinstance(value, datetime) else value for value in row]

        db.executemany(
            "INSERT INTO `tabMikroTik Banner` VALUES (?, ?, ?, ?, ?, ?, ?, '', '/files/day.png', '', '', 'KSh 200')",
            [sqlite_values(row + (row[0],)) for row in rows],
        )

        def select(sql, params, as_dict):
            matches = list(db.execute(sql.replace("%s", "?"), sqlite_values(params)))
            self.assertLess(len(matches), 20)
            return [dict(row) for row in matches]

        def image(record):
            if record["name"] == "b-invalid":
                raise ValueError("Deleted")
            return record["image"]

        self.frappe.db.sql.side_effect = select
        with patch.object(self.banners, "_public_image", side_effect=image):
            result = self.banners.published("wifi-1")
        self.assertEqual([row["title"] for row in result], ["a-tie-break", "z-starts-now", "more-00", "more-01"])
        self.assertEqual(result[0], {
            "title": "a-tie-break", "description": "", "image": "/files/day.png", "alt": "", "url": "",
            "display_price": "KSh 200",
        })

    def test_preview_uses_saved_record_and_checks_read_permission(self):
        doc = MagicMock()
        doc.get.side_effect = (self.banner | {"display_price": "  KSh 200  "}).get
        self.frappe.get_doc.return_value = doc
        preview = self.banners.preview("banner-1")
        self.assertEqual(preview["title"], "Today’s offer")
        self.assertEqual(preview["display_price"], "KSh 200")
        doc.check_permission.assert_called_once_with("read")
        doc.get.reset_mock()
        doc.check_permission.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.banners.preview("banner-1")
        doc.get.assert_not_called()

    def test_rejects_external_unsafe_and_non_raster_paths_before_io(self):
        for path in (
            "https://example.com/files/day.png", "//example.com/files/day.png", "/private/files/day.png",
            "/files/day.svg", "/files/day.gif", "/files/../day.png", "/files/%2e%2e%2fday.png",
            "/files/dir\\day.png", "/files/day.png?x", "/files/day.png#frag", "/files/day.png\n",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.banners.validate_banner(self.banner | {"image": path})
        self.frappe.db.get_value.assert_not_called()
        self.frappe.get_site_path.assert_not_called()


@unittest.skipUnless(Image, "Pillow is installed by Frappe; needed here for real raster fixtures")
class TestBannerImageContent(BannerFixture):
    def setUp(self):
        super().setUp()
        site = Path(".") / f".banner-test-site-{uuid4().hex}"
        directory = site / "public" / "files"
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, site)
        self.frappe.get_site_path.side_effect = lambda *parts: str(site.joinpath(*parts))
        self.directory = directory

    def _attach(self, name, content):
        raw_url = f"/files/{name}"
        (self.directory / name).write_bytes(content)
        self.frappe.db.get_value.return_value = {
            "file_url": raw_url, "attached_to_doctype": "MikroTik Banner",
            "attached_to_name": "banner-1", "attached_to_field": "image",
        }
        return self.banner | {"image": raw_url, "enabled": 1}

    @staticmethod
    def _raster(image_format="PNG", size=(32, 16), **options):
        out = BytesIO()
        Image.new("RGB", size).save(out, format=image_format, **options)
        return out.getvalue()

    def test_decodes_real_public_attachments_and_escapes_path(self):
        formats = [("PNG", "png"), ("JPEG", "jpg")]
        if features.check("webp"):
            formats.append(("WEBP", "webp"))
        for image_format, ext in formats:
            with self.subTest(image_format=image_format):
                banner = self._attach(f"Day pass.{ext}", self._raster(image_format))
                self.banners.validate_banner(banner)
                self.frappe.db.sql.return_value = [banner]
                self.assertEqual(self.banners.published("wifi-1")[0]["image"], f"/files/Day%20pass.{ext}")
        filters = self.frappe.db.get_value.call_args.args[1]
        self.assertEqual(filters["is_private"], 0)
        self.assertEqual(filters["attached_to_name"], "banner-1")
        self.assertEqual(filters["attached_to_field"], "image")

    def test_rejects_wrong_type_corruption_animation_and_image_limits(self):
        second_frame = Image.new("RGB", (32, 16), "red")
        for content in (
            b"<svg xmlns='http://www.w3.org/2000/svg'/>", self._raster("JPEG"),
            self._raster()[:40], self._raster(size=(1921, 1)), self._raster(size=(1900, 1900)),
            self._raster(save_all=True, append_images=[second_frame], duration=100),
            self._raster() + bytes(self.banners.MAX_IMAGE_BYTES),
        ):
            with self.subTest(size=len(content)), self.assertRaises(ValueError):
                self.banners.validate_banner(self._attach("day.png", content))

    def test_publication_skips_removed_private_unrelated_and_symlinked_files(self):
        banner = self._attach("day.png", self._raster())
        attachment = self.frappe.db.get_value.return_value
        self.frappe.db.sql.return_value = [banner]
        self.frappe.db.get_value.return_value = None
        self.assertEqual(self.banners.published("wifi-1"), [])
        self.frappe.db.get_value.return_value = attachment | {"attached_to_name": "other"}
        self.assertEqual(self.banners.published("wifi-1"), [])
        self.frappe.db.get_value.return_value = attachment
        path = self.directory / "day.png"
        path.rename(self.directory / "other.png")
        self.assertEqual(self.banners.published("wifi-1"), [])
        path.symlink_to("other.png")
        self.assertEqual(self.banners.published("wifi-1"), [])
