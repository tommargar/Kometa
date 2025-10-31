import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from modules.emby_server import EmbyServer


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DummySession:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.call_count = 0

    def get(self, url, headers=None, params=None, timeout=None):
        if self.call_count >= len(self._payloads):
            raise AssertionError("No more payloads configured for DummySession")
        payload = self._payloads[self.call_count]
        self.call_count += 1
        return _DummyResponse(payload)


class EmbyServerCacheTest(unittest.TestCase):
    def _build_server(self, payloads):
        server = EmbyServer.__new__(EmbyServer)
        server._items_cache = {}
        server._items_cache_fields = {}
        server._items_cache_ts = {}
        server.items_cache_ttl = 300
        server.item_cache = {}
        server.dirty_items = set()
        server._ensure_http_session = lambda: None
        server.session = _DummySession(payloads)
        server.emby_server_url = "http://emby.test"
        server.api_key = "api"
        server.headers = {}
        return server

    def test_bulk_fetch_after_invalidate_returns_fresh_data(self):
        server = self._build_server([
            {"Items": [{"Id": "1", "Name": "Old"}]},
            {"Items": [{"Id": "1", "Name": "New"}]},
        ])

        initial = server.get_items_bulk(["1"], fields=["Name"])
        self.assertEqual("Old", initial["1"]["Name"])
        self.assertIn("1", server._items_cache)
        self.assertEqual(1, server.session.call_count)

        server.invalidate_item(1)
        self.assertNotIn("1", server._items_cache)
        self.assertIn(1, server.dirty_items)

        refreshed = server.get_items_bulk(["1"], fields=["Name"])
        self.assertEqual("New", refreshed["1"]["Name"])
        self.assertEqual(2, server.session.call_count)
        self.assertNotIn(1, server.dirty_items)


if __name__ == "__main__":
    unittest.main()
