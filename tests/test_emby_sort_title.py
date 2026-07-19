from unittest.mock import MagicMock

from modules.emby_server import EmbyServer


def make_server(item):
    server = EmbyServer.__new__(EmbyServer)
    server.get_item = MagicMock(return_value=item)
    server._ensure_http_session = MagicMock()
    server.session = MagicMock()
    server.emby_server_url = "http://emby.test"
    server.api_key = "secret"
    server.invalidate_item = MagicMock()
    return server


def test_forced_sort_name_is_posted_before_local_state_is_changed():
    item = {
        "Id": "2568905",
        "Name": "🎥 Filme 🎵🎂 Tyler Bates",
        "SortName": "🎥 Filme 🎵🎂 Tyler Bates",
        "LockedFields": [],
    }
    server = make_server(item)

    server.update_item("2568905", {"ForcedSortName": "🎥 Filme_!161_🎵🎂 Tyler Bates", "LockedFields": ["SortName"]})

    server.session.post.assert_called_once()
    payload = server.session.post.call_args.kwargs["json"]
    assert payload["SortName"] == "🎥 Filme_!161_🎵🎂 Tyler Bates"
    assert payload["ForcedSortName"] == "🎥 Filme_!161_🎵🎂 Tyler Bates"
    assert "SortName" in payload["LockedFields"]


def test_matching_sort_name_does_not_trigger_redundant_post():
    item = {
        "Id": "2568905",
        "Name": "🎥 Filme 🎵🎂 Tyler Bates",
        "SortName": "🎥 Filme_!161_🎵🎂 Tyler Bates",
        "LockedFields": ["SortName"],
    }
    server = make_server(item)

    result = server.update_item("2568905", {"ForcedSortName": "🎥 Filme_!161_🎵🎂 Tyler Bates", "LockedFields": ["SortName"]})

    assert result is None
    server.session.post.assert_not_called()
