"""Tests for the CI test-file sharder (``scripts/ci_shard.py``).

The workflow relies on the shards being a partition: a file that no shard runs
would silently stop being tested, and a file run twice only wastes runner time.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_shard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_shard", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_shard = _load_module()


class TestDiscoverTests:
    def test_finds_test_files_in_both_roots(self):
        paths = ci_shard.discover_tests()
        assert paths, "expected at least one test file"
        assert all(path.startswith(("tests/", "legal_paragraph_detection_engine/tests/")) for path in paths)

    def test_is_sorted_and_unique(self):
        paths = ci_shard.discover_tests()
        assert paths == sorted(paths)
        assert len(paths) == len(set(paths))

    def test_every_path_exists(self):
        for path in ci_shard.discover_tests():
            assert Path(path).is_file(), path


class TestShardOf:
    def test_shards_are_a_partition(self):
        paths = ci_shard.discover_tests()
        shards = [ci_shard.shard_of(paths, index, 3) for index in (1, 2, 3)]
        flattened = [path for shard in shards for path in shard]
        assert sorted(flattened) == paths
        assert all(shard for shard in shards)

    def test_single_shard_returns_everything(self):
        paths = ci_shard.discover_tests()
        assert ci_shard.shard_of(paths, 1, 1) == paths

    def test_shard_sizes_are_balanced(self):
        paths = list(range(300))
        sizes = [len(ci_shard.shard_of([str(p) for p in paths], index, 3)) for index in (1, 2, 3)]
        assert max(sizes) - min(sizes) <= 1


class TestCli:
    def test_prints_only_this_shards_paths(self, capsys):
        paths = ci_shard.discover_tests()
        assert ci_shard.main(["--shard", "2/3"]) == 0
        printed = capsys.readouterr().out.split()
        assert printed == ci_shard.shard_of(paths, 2, 3)

    def test_rejects_out_of_range_shard(self):
        with pytest.raises(SystemExit):
            ci_shard.main(["--shard", "4/3"])

    def test_rejects_malformed_shard(self):
        with pytest.raises(SystemExit):
            ci_shard.main(["--shard", "nonsense"])
