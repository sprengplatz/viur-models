"""Golden-file pin of the emitted structure (analysis/01 §6).

Any change to the structure mapping shows up as a diff against
``tests/golden/sample_structure.json`` — intentional changes regenerate the
file (see the module docstring of the golden), unintentional ones fail here
before they reach a client.

The integration suite additionally compares the emission against the real
viur-core bones; this pin catches regressions without the real core.
"""
import json
import pathlib

from tests.test_structure import Sample

GOLDEN = pathlib.Path(__file__).parent / "golden" / "sample_structure.json"


def test_structure_matches_golden_file():
    golden = json.loads(GOLDEN.read_text())
    # JSON knows no non-string keys: normalize ours the same way.
    current = json.loads(json.dumps(Sample.viur_structure()))
    assert current == golden, (
        "structure emission changed — if intentional, regenerate "
        "tests/golden/sample_structure.json"
    )
