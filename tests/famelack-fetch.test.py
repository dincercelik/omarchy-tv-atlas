#!/usr/bin/env python3
"""tv-fetch against a local copy of the TV Atlas dataset layout."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

FETCH = Path(__file__).resolve().parent.parent / "tv-fetch"


def row(nanoid, name, country, streams, languages=("eng",)):
    return {"nanoid": nanoid, "name": name, "sources": {"streams": list(streams)},
            "languages": list(languages), "country": country, "isGeoBlocked": False}


CATALOG = [
    row("TrAAAAAAAAAAA1", "A Haber Radyo", "tr", ["https://tr.example/a.m3u8", "https://tr.example/b"], ["tur"]),
    row("TrAAAAAAAAAAA2", "News Istanbul", "tr", ["http://tr.example/news"], ["tur"]),
    row("UsAAAAAAAAAAA1", "KEXP\nSeattle", "us", ["https://us.example/kexp"]),
    row("UsAAAAAAAAAAA2", "<img src=https://example.invalid/t>", "us", ["https://us.example/x"]),
    row("BadAAAAAAAAAA1", "No stream", "us", ["ftp://bad.example/x"]),
    row("bad id!", "Bad id", "us", ["https://bad.example/x"]),
    row("NoCountryAAAA1", "No country", "", ["https://bad.example/y"]),
    row("TrAAAAAAAAAAA1", "Duplicate", "tr", ["https://tr.example/dup"]),
    {"nanoid": "YtAAAAAAAAAAA1", "name": "News YouTube", "country": "ua", "languages": ["ukr"],
     "isGeoBlocked": True, "sources": {"youtube": ["https://www.youtube-nocookie.com/embed/0jkTYCaRBKA"]}},
    {"nanoid": "YtAAAAAAAAAAA2", "name": "Bad YouTube", "country": "ua", "languages": ["ukr"],
     "isGeoBlocked": False, "sources": {"youtube": ["https://evil.example/embed/0jkTYCaRBKA"]}},
    "not an object",
]


class FamelackFetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        data = root / "data"
        (data / "countries").mkdir(parents=True)
        (data / "categories").mkdir()
        (data / "categories/all.json").write_text(json.dumps(CATALOG))
        (data / "categories/news.json").write_text(json.dumps([CATALOG[1]]))
        (data / "countries/tr.json").write_text(json.dumps(CATALOG[:2]))
        (data / "countries_metadata.json").write_text(json.dumps({
            "TR": {"country": "Türkiye"}, "US": {"country": "United States"}, "UA": {"country": "Ukraine"}}))
        self.data = data
        runtime = root / "run"
        runtime.mkdir(mode=0o700)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
                        XDG_CACHE_HOME=str(root / "cache"), XDG_DATA_HOME=str(root / "share"),
                        FAMELACK_DATA_ROOT=f"file://{data}")

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, *args, check=True):
        result = subprocess.run([str(FETCH), *args], env=self.env, capture_output=True, text=True, timeout=60)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return result

    def test_world_normalizes_and_filters(self):
        rows = self.fetch("world")
        self.assertEqual({r["uuid"] for r in rows},
                         {"TrAAAAAAAAAAA1", "TrAAAAAAAAAAA2", "UsAAAAAAAAAAA1", "UsAAAAAAAAAAA2", "YtAAAAAAAAAAA1"})
        youtube = next(r for r in rows if r["uuid"] == "YtAAAAAAAAAAA1")
        self.assertEqual(youtube["url"], "https://www.youtube.com/watch?v=0jkTYCaRBKA")
        self.assertEqual(youtube["codec"], "YouTube")
        self.assertEqual(youtube["tags"], "geo-blocked")
        first = next(r for r in rows if r["uuid"] == "TrAAAAAAAAAAA1")
        self.assertEqual(first["name"], "A Haber Radyo")
        self.assertEqual(first["url"], "https://tr.example/a.m3u8")
        self.assertEqual(first["country"], "Türkiye")
        self.assertEqual(first["countryCode"], "TR")
        self.assertEqual(first["language"], "tur")
        self.assertEqual(first["codec"], "Live")
        self.assertIsNone(first["latitude"])
        kexp = next(r for r in rows if r["uuid"] == "UsAAAAAAAAAAA1")
        self.assertNotIn("\n", kexp["name"])
        # Markup-shaped names pass through as data; the UI renders them as plain text.
        self.assertIn("<img", next(r for r in rows if r["uuid"] == "UsAAAAAAAAAAA2")["name"])

    def test_country_search_random_resolve(self):
        self.assertEqual([r["uuid"] for r in self.fetch("country", "tr")], ["TrAAAAAAAAAAA1", "TrAAAAAAAAAAA2"])
        self.assertEqual({r["uuid"] for r in self.fetch("search", "turkey")}, {"TrAAAAAAAAAAA1", "TrAAAAAAAAAAA2"})
        news = self.fetch("search", "news")
        self.assertEqual([r["uuid"] for r in news], ["TrAAAAAAAAAAA2", "YtAAAAAAAAAAA1"])
        self.assertEqual(self.fetch("search", "kexp")[0]["uuid"], "UsAAAAAAAAAAA1")
        self.assertEqual(self.fetch("search", "nothing matches"), [])
        random = self.fetch("random", "TrAAAAAAAAAAA1,TrAAAAAAAAAAA2,UsAAAAAAAAAAA1,YtAAAAAAAAAAA1")
        self.assertEqual([r["uuid"] for r in random], ["UsAAAAAAAAAAA2"])
        self.assertEqual([r["uuid"] for r in self.fetch("resolve", "UsAAAAAAAAAAA1,Missing0000001")], ["UsAAAAAAAAAAA1"])

    def test_search_ignores_case_in_any_script_and_matches_literally(self):
        (self.data / "categories/all.json").write_text(json.dumps([
            row("TrUnicode0001", "CNN TÜRK", "tr", ["https://tr.example/cnn"], ["tur"]),
            row("RuUnicode0001", "Первый канал", "ru", ["https://ru.example/1"], ["rus"]),
            row("UsLiteral0001", "Dot.TV", "us", ["https://us.example/dot"]),
            row("UsLiteral0002", "DotxTV", "us", ["https://us.example/dotx"]),
            row("TrUnicode0002", "Kırmızı İzmir", "tr", ["https://tr.example/k"], ["tur"]),
        ]))
        self.assertEqual([r["uuid"] for r in self.fetch("search", "cnn türk")], ["TrUnicode0001"])
        self.assertEqual([r["uuid"] for r in self.fetch("search", "ПЕРВЫЙ")], ["RuUnicode0001"])
        self.assertEqual([r["uuid"] for r in self.fetch("search", "первый")], ["RuUnicode0001"])
        # Regex metacharacters in the query are literal: "." must not match "x".
        self.assertEqual([r["uuid"] for r in self.fetch("search", "dot.tv")], ["UsLiteral0001"])
        self.assertEqual(self.fetch("search", "(.*"), [])
        # Country names resolve whatever their case.
        # Turkish dotted/dotless i fold to "i" in both directions.
        self.assertEqual([r["uuid"] for r in self.fetch("search", "kirmizi izmir")], ["TrUnicode0002"])
        self.assertEqual([r["uuid"] for r in self.fetch("search", "KIRMIZI")], ["TrUnicode0002"])
        tr = ["TrUnicode0001", "TrUnicode0002"]
        self.assertEqual([r["uuid"] for r in self.fetch("search", "  TÜRKIYE ")], tr)
        self.assertEqual([r["uuid"] for r in self.fetch("search", "TÜRKİYE")], tr)
        self.assertEqual([r["uuid"] for r in self.fetch("search", "Russia")], ["RuUnicode0001"])
        # Language codes match whole codes, in any case.
        self.assertEqual([r["uuid"] for r in self.fetch("search", "RUS")], ["RuUnicode0001"])

    def test_rejects_invalid_input(self):
        for args in (["country", "t1"], ["search", " "], ["search", "x" * 129],
                     ["random", "bad id!"], ["resolve", "a,b"], ["nope"]):
            self.assertEqual(self.fetch(*args, check=False).returncode, 2, args)

    def test_unavailable_source_fails_cleanly(self):
        self.env["FAMELACK_DATA_ROOT"] = "file:///nonexistent-famelack"
        result = self.fetch("world", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Famelack", result.stderr)


if __name__ == "__main__":
    unittest.main()
