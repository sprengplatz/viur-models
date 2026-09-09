"""The one-table-name-per-suite guard in ``conftest.py``."""
import pathlib

from tests.conftest import duplicate_tablenames


def test_duplicate_tablenames_reports_every_declaring_line(tmp_path):
    (tmp_path / "test_a.py").write_text(
        'class A(Model, table=True):\n    __tablename__ = "shared"\n'
    )
    (tmp_path / "test_b.py").write_text(
        "class B(Model, table=True):\n    __tablename__ = 'shared'\n\n"
        'class C(Model, table=True):\n    __tablename__ = "unique"\n'
    )
    assert duplicate_tablenames(tmp_path) == {"shared": ["test_a.py:2", "test_b.py:2"]}


def test_the_suites_themselves_are_clean():
    here = pathlib.Path(__file__).resolve().parent
    assert duplicate_tablenames(here) == {}
    assert duplicate_tablenames(here.parent / "integration") == {}
