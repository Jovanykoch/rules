import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from main import (
    QUANX_POLICY_BY_TAG,
    extract_ip_cidrs,
    release_clash_file,
    release_clash_ipcidr_file,
    release_quanx_file,
    release_singbox_file,
    release_surge_file,
    validate_clash_yaml,
    validate_quanx_file,
    validate_singbox_json,
    validate_surge_file,
)


class OutputFormatTests(unittest.TestCase):
    def test_surge_output_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path.cwd()
            try:
                Path(directory, "dist").mkdir()
                Path(directory).joinpath("tmp").mkdir()
                import os

                os.chdir(directory)
                release_surge_file("tag", ["a.example"], ["example.com"])
                validate_surge_file("dist/tag.list")
                content = Path("dist/tag.list").read_text()
                self.assertEqual(content, "a.example\n.example.com\n")
            finally:
                os.chdir(current)

    def test_clash_output_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path.cwd()
            try:
                Path(directory, "dist").mkdir()
                import os

                os.chdir(directory)
                release_clash_file("tag", ["a.example"], ["example.com"])
                validate_clash_yaml("dist/tag.yaml")
                payload = yaml.safe_load(Path("dist/tag.yaml").read_text())["payload"]
                self.assertEqual(payload, ["a.example", ".example.com"])
            finally:
                os.chdir(current)

    def test_quanx_output_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path.cwd()
            try:
                Path(directory, "dist").mkdir()
                import os

                os.chdir(directory)
                release_quanx_file("tag", ["a.example"], ["example.com"], ["foo"], "direct")
                validate_quanx_file("dist/tag.quanx")
                content = Path("dist/tag.quanx").read_text().splitlines()
                self.assertEqual(content[0], "host, a.example, direct")
            finally:
                os.chdir(current)

    def test_quanx_accepts_reject_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tag.quanx")
            path.write_text("host-suffix, ads.example, reject\n")
            validate_quanx_file(str(path))

    def test_block_tags_ship_reject_quanx_policy(self):
        # Block lists must bake `reject` into their *.quanx files so that
        # subscribers without an explicit force-policy still block.
        for tag in ("reject", "ads", "ads-mini"):
            self.assertEqual(QUANX_POLICY_BY_TAG[tag], "reject")
        self.assertEqual(QUANX_POLICY_BY_TAG["gfw"], "proxy")
        self.assertEqual(QUANX_POLICY_BY_TAG["gfw-skip"], "direct")
        self.assertEqual(QUANX_POLICY_BY_TAG.get("loc-cn", "direct"), "direct")

    def test_singbox_json_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tag.json")
            path.write_text(json.dumps({"version": 2, "rules": [{"domain": ["a.example"]}]}))
            validate_singbox_json(str(path))


class IpCidrTests(unittest.TestCase):
    def test_extract_ip_cidrs_splits_literals(self):
        domains, cidrs = extract_ip_cidrs(
            ["example.com", "1.2.3.4", "2001:db8::1", "not-an-ip"]
        )
        self.assertEqual(domains, ["example.com", "not-an-ip"])
        self.assertEqual(cidrs, ["1.2.3.4/32", "2001:db8::1/128"])

    def test_quanx_writes_ip_cidr_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path.cwd()
            try:
                Path(directory, "dist").mkdir()
                import os

                os.chdir(directory)
                release_quanx_file(
                    "tag",
                    ["a.example"],
                    [],
                    [],
                    "proxy",
                    ["1.2.3.4/32", "2001:db8::/32"],
                )
                validate_quanx_file("dist/tag.quanx")
                content = Path("dist/tag.quanx").read_text().splitlines()
                self.assertIn("ip-cidr, 1.2.3.4/32, proxy", content)
                self.assertIn("ip-cidr, 2001:db8::/32, proxy", content)
            finally:
                os.chdir(current)

    def test_clash_ipcidr_file_uses_bare_cidrs(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path.cwd()
            try:
                Path(directory, "dist").mkdir()
                import os

                os.chdir(directory)
                release_clash_ipcidr_file("tag", ["1.2.3.4/32"])
                validate_clash_yaml("dist/tag-ipcidr.yaml")
                payload = yaml.safe_load(Path("dist/tag-ipcidr.yaml").read_text())[
                    "payload"
                ]
                self.assertEqual(payload, ["1.2.3.4/32"])
            finally:
                os.chdir(current)

    def test_singbox_json_includes_ip_cidr(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path.cwd()
            try:
                Path(directory, "dist").mkdir()
                Path(directory, "tmp").mkdir()
                import os

                os.chdir(directory)
                with patch("main.subprocess.run") as run:
                    release_singbox_file(
                        "tag", ["a.example"], [], [], [], ["1.2.3.4/32"]
                    )
                run.assert_called_once()
                data = json.loads(Path("tmp/tag.json").read_text())
                self.assertEqual(data["rules"][0]["ip_cidr"], ["1.2.3.4/32"])
                validate_singbox_json("tmp/tag.json")
            finally:
                os.chdir(current)

    def test_surge_validation_rejects_ip_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tag.list")
            path.write_text("1.2.3.4\n")
            with self.assertRaisesRegex(ValueError, "IP literal"):
                validate_surge_file(str(path))


if __name__ == "__main__":
    unittest.main()
