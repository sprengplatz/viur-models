"""Dump & fromClient parity — ViURModel vs. the REAL viur-core skeleton.

Complements ``test_structure_parity``: same twin pair, but comparing the
**value dumps** and the **error shapes** of client input validation.

Error comparison is by ``(fieldPath, severity)`` — messages differ by
design (viur-i18n vs. pydantic).
"""
from datetime import datetime, timezone

from skeletons import ParityRefSkel
from test_structure_parity import ParityModel

DUE = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)
FIELDS = ["name", "mail", "rating", "active", "due", "state"]


def test_dump_values_match_the_real_skeleton():
    skel = ParityRefSkel()
    skel["name"] = "Alice"
    skel["rating"] = 4
    skel["active"] = True
    skel["due"] = DUE
    skel["state"] = "new"

    model = ParityModel(name="Alice", rating=4, active=True, due=DUE, state="new")

    skel_dump = skel.dump()
    model_dump = model.viur_dump()
    for field in FIELDS:
        assert model_dump[field] == skel_dump[field], (
            f"dump drift on {field!r}: model={model_dump[field]!r} "
            f"skel={skel_dump[field]!r}"
        )


def _relevant_errors(errors) -> dict:
    return {tuple(error.fieldPath): error.severity.name for error in errors}


def test_from_client_errors_match_the_real_skeleton():
    payload = {"rating": "99"}  # missing required name + out-of-range rating

    skel = ParityRefSkel()
    assert skel.fromClient(payload) is False
    _, model_errors = ParityModel.viur_from_client(payload)

    assert _relevant_errors(model_errors) == _relevant_errors(skel.errors)


def test_from_client_accepts_the_same_valid_payload():
    payload = {"name": "Alice", "rating": "4", "state": "new"}

    skel = ParityRefSkel()
    assert skel.fromClient(payload) is True

    instance, errors = ParityModel.viur_from_client(payload)
    assert errors == []
    assert (instance.name, instance.rating, instance.state) == ("Alice", 4, "new")
