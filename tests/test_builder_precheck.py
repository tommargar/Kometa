import logging
import os
import sys
from pathlib import Path
from types import MethodType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.builder import CollectionBuilder
import modules.builder as builder_module
from modules.util import Failed
import modules.util as util


class DummyLibrary:
    def __init__(self):
        self.name = "Dummy"
        self.type = "Movie"
        self.collections = []
        self.minimum_items = 0
        self.Radarr = None
        self.Sonarr = None
        self.ignore_ids = []
        self.ignore_imdb_ids = []
        self.is_movie = True
        self.is_show = False
        self.is_music = False

    def split(self, text):
        attribute, modifier = os.path.splitext(str(text).lower())
        final = f"{attribute}{modifier}"
        return attribute, modifier, final

    def smart_label_check(self, name):
        return False


class DummyBuilder(CollectionBuilder):
    def __init__(self):
        # Intentionally avoid running the parent initializer
        pass


@pytest.fixture
def builder():
    instance = DummyBuilder()
    util.logger = logging.getLogger("kometa-test")
    builder_module.logger = util.logger
    instance.Type = "Collection"
    instance.type = "collection"
    instance.builders = []
    instance.builder_level = 0
    instance._precheck_skipped_builders = False
    instance.ignore_blank_results = True
    instance.server_preroll = False
    instance.smart_url = False
    instance.blank_collection = False
    instance.library = DummyLibrary()

    def _mock_validate_attribute(self, *args, **kwargs):
        return None

    def _mock_build_filter(self, *args, **kwargs):
        raise AssertionError("build_filter should not be called when prevalidation fails")

    instance.validate_attribute = MethodType(_mock_validate_attribute, instance)
    instance.build_filter = MethodType(_mock_build_filter, instance)
    return instance


def test_ignore_blank_results_skips_invalid_plex_search(builder, caplog):
    with caplog.at_level(logging.WARNING):
        builder._plex("plex_search", [{"title": None}])
        if not builder.server_preroll and not builder.smart_url and not builder.blank_collection and len(builder.builders) == 0:
            if builder._precheck_skipped_builders and builder.ignore_blank_results:
                util.logger.warning(f"{builder.Type} Warning: All builders were skipped after prevalidation")
            else:
                raise Failed(f"{builder.Type} Error: No builders were found")

    assert builder._precheck_skipped_builders is True
    assert builder.builders == []
    assert any("produced no valid values" in message for message in caplog.messages)
    assert "All builders were skipped after prevalidation" in caplog.text
