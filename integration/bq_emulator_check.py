"""SQLList end-to-end against the BigQuery emulator — manual check script.

Not part of either test suite (needs Docker). Run it after changes to the
BigQuery backend::

    docker run -d --name viurbq -p 9050:9050 \\
        ghcr.io/goccy/bigquery-emulator:latest --project=test --dataset=viur
    .venv/bin/python integration/bq_emulator_check.py
    docker rm -f viurbq

Every line must print OK. Known emulator limitations (NOT backend bugs):
reflection hangs (hence ``checkfirst=False``), ALTER TABLE hangs, and the
google client retries emulator-internal errors for minutes — the per-step
alarm turns those into visible FAILs.
"""
from viur.light_mock import install_viur_core_mocks
install_viur_core_mocks()

import signal
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
from sqlalchemy import create_engine

from viur.models import Field, db
from viur.models.bigquery import BigQueryModel
from viur.models.sqllist import SQLList


class BQTicket(BigQueryModel, table=True):
    __tablename__ = "bq_check"
    __table_args__ = {"schema": "viur"}
    name: str = Field(descr="Name", max_length=50)
    rating: int | None = Field(default=None, ge=1, le=5)


class Rec:
    version = 2  # envelope-v2 marker — SQLList refuses non-v2 renders

    def __getattr__(self, verb):
        return lambda skel, **kw: (verb, skel)
    def render(self, action, skel, **kw): return (action, skel)


class Mod(SQLList):
    model = BQTicket
    def __init__(self):
        super().__init__("bqtickets", "/bqtickets")
        self.render = Rec()
    def can(self, instance): return True


client = bigquery.Client(project="test",
    client_options=ClientOptions(api_endpoint="http://localhost:9050"),
    credentials=AnonymousCredentials())
engine = create_engine("bigquery://test/viur", connect_args={"client": client})
BQTicket.__table__.create(engine, checkfirst=False)
db.configure(engine)   # triggert auch die BigQuery-Workarounds
module = Mod()

def _alarm(sig, frame): raise TimeoutError("step timeout")
signal.signal(signal.SIGALRM, _alarm)

def step(label, fn):
    signal.alarm(15)
    try:
        print(f"  OK   {label}: {fn()}", flush=True)
    except BaseException as e:
        print(f"  FAIL {label}: {type(e).__name__}: {str(e)[:150]}", flush=True)
    finally:
        signal.alarm(0)

r = module.add(name="Alpha", rating="3", skey="x"); key = r[1].viur_key
module.add(name="Beta", rating="5", skey="x")
module.add(name="Gamma", skey="x")
print("=== IDs zeitlich sortiert? ===")
ids = [t.id for t in module.list()[1]]
print("   ohne orderby (= ORDER BY id):", [t.name for t in module.list()[1]],
      "| sortiert:", ids == sorted(ids))
step("view", lambda: module.view(key)[1].name)
step("search (vorher FAIL)", lambda: [t.name for t in module.list(search="alp")[1]])
step("$lk prefix (vorher FAIL)", lambda: [t.name for t in module.list(**{"name$lk": "be"})[1]])
step("edit (vorher StaleDataError)", lambda: module.edit(key, name="Alpha2", skey="x")[1].name)
step("view nach edit", lambda: module.view(key)[1].name)
step("delete", lambda: module.delete(key, skey="x")[0])
step("list nach delete", lambda: [t.name for t in module.list(orderby="name")[1]])
def paging():
    p1 = module.list(limit="1", orderby="name")[1]
    p2 = module.list(limit="1", orderby="name", cursor=p1.getCursor())[1]
    return [t.name for t in p1] + [t.name for t in p2]
step("cursor", paging)
step("dump/key roundtrip", lambda: BQTicket.viur_parse_key(module.list()[1][0].viur_key) == module.list()[1][0].id)
