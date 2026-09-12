"""The ODB settings schema and its hot/cold classification."""

import pytest

from fakesampic import settings as S


def test_defaults_are_valid():
    assert S.validate(S.defaults("/some/run.bin")) == []


def test_defaults_describe_the_eight_plane_detector():
    d = S.defaults()
    assert d["Synthetic"]["N Planes"] == 8
    assert d["Synthetic"]["Strips Per Plane"] == 10
    assert d["Synthetic"]["Pitch mm"] == 0.5
    assert d["Synthetic"]["Plane Orientation"] == list("XYXYXYXY")
    assert len(d["Synthetic"]["Plane Z mm"]) == 8


def test_measured_defaults_match_run914():
    """These are measured from real data, not invented; keep them that way."""
    syn = S.defaults()["Synthetic"]
    assert syn["Baseline V"] == pytest.approx(0.7459, abs=1e-4)
    assert syn["Sampling MSps"] == 6400
    assert syn["Raw ToT"] == 65535, "the sentinel every run914 hit carries"
    assert syn["ToT ns"] == -1.0


def test_validate_reports_every_problem_not_just_the_first():
    bad = S.merged({"Source": "nope", "Rate Hz": -1, "Rate Model": "wat"})
    problems = S.validate(bad)
    assert len(problems) >= 3
    assert any("Source" in p for p in problems)
    assert any("Rate Hz" in p for p in problems)


def test_binfile_source_needs_files():
    cfg = S.merged({"Source": "binfile", "BinFile": {"Files": ["", "", ""]}})
    assert any("Files is empty" in p for p in S.validate(cfg))


def test_mixer_cannot_contain_itself():
    cfg = S.merged({"Source": "mixer", "Mixer": {"Child Sources": ["mixer"]}})
    assert any("cannot contain" in p for p in S.validate(cfg))


def test_hot_and_cold_classification():
    assert S.is_hot("/Equipment/FakeSampic/Settings/Rate Hz")
    assert S.is_hot("/Equipment/FakeSampic/Settings/BinFile/Timing")
    assert S.is_hot("Synthetic/Beam Sigma mm")
    # Cold: changes what the data MEANS, so it waits for a run boundary.
    assert not S.is_hot("/Equipment/FakeSampic/Settings/Restamp Timestamps")
    assert not S.is_hot("/Equipment/FakeSampic/Settings/BinFile/Files")
    assert not S.is_hot("Source")


def test_every_hot_key_exists_in_the_schema():
    """A hot key with no setting behind it would silently never fire."""
    d = S.defaults()
    for key in S.HOT_KEYS:
        if "/" in key:
            sub, leaf = key.split("/", 1)
            assert sub in d, f"{key}: no such subtree"
            assert leaf in d[sub], f"{key}: no such key"
        else:
            assert key in d, f"{key}: no such key"


def test_path_normalisation():
    assert S.normalise_path("/Equipment/FakeSampic/Settings/Rate Hz") == "Rate Hz"
    assert S.normalise_path("/Equipment/X/Settings/A/B") == "A/B"
    assert S.normalise_path("Rate Hz") == "Rate Hz"


def test_as_list_handles_the_scalar_that_midas_returns():
    """A one-element ODB array comes back bare; treating it as a string of
    characters is the failure this prevents."""
    assert S.as_list("only.bin") == ["only.bin"]
    assert S.as_list(None) == []
    assert S.as_list([1, 2], n=4, fill=0) == [1, 2, 0, 0]
    assert S.as_list([1, 2, 3], n=2) == [1, 2]


def test_non_empty_drops_odb_array_padding():
    assert S.non_empty(["a.bin", "", "  ", "b.bin"]) == ["a.bin", "b.bin"]
    assert S.non_empty("one.bin") == ["one.bin"]


def test_merged_fills_gaps_without_dropping_stored_values():
    out = S.merged({"Rate Hz": 4242.0, "Synthetic": {"N Planes": 3}})
    assert out["Rate Hz"] == 4242.0
    assert out["Synthetic"]["N Planes"] == 3
    assert out["Synthetic"]["Pitch mm"] == 0.5, "untouched keys keep defaults"
    assert "Mixer" in out
