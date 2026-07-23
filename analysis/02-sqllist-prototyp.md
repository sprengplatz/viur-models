# SQLList-Prototyp

**Status:** Umgesetzt inkl. Query-Sprache/`search` (`viur.models.sqllist` + `viur.models.db`; §1-Befund 2 wurde direkt in viur-actions gefixt — das v2-Gate ist jetzt protokollbasiert) · **Bezug:** viur-core 3.9.0.dev6, viur-actions 0.2 (Envelope v2, `@action`/`ActionModule`), SQLModel ≥ 0.0.39, [01 — ViURField & Structure-Mapping](01-viurfield-und-structure-mapping.md)

`SQLList` ist das SQL-Gegenstück zum viur-core-`List`-Prototyp: ein
Modul-Prototyp, der ein `ViURModel` über dieselben Endpunkte
(`list`/`view`/`add`/`edit`/`delete`/`structure`) und dieselbe
Envelope-v2-API serviert wie ein Skeleton-Modul. Die Modell-Schicht
(Dokument 01) liefert dafür bereits `structure()`/`dump()`/`viur_from_client()`
in Bone-Parität — dieses Dokument spezifiziert das Modul drumherum.

---

## 1. Verifizierte Render-Fakten (bestimmen das Design)

Gegen den echten Code geprüft:

1. **v1-`DefaultRender.renderEntry` ist isinstance-gegated:**
   `isinstance(skel, SkeletonInstance)` — sonst Deprecation-Fallback ohne
   `structure`/`errors`. Ein ViURModel wird dort **nicht** korrekt
   gerendert.
2. **Die v2-Envelope-Builder sind protokollbasiert:**
   `render_entity`/`render_list` (viur-actions) greifen per `hasattr` auf
   `dump()` / `structure()` / `getCursor()` / `get_orders()` zu. Der
   darüberliegende `EnvelopeRenderMixin` hatte allerdings **ebenfalls** ein
   isinstance-Gate — das wurde im Zuge dieser Umsetzung direkt in
   viur-actions auf einen Protokoll-Check umgestellt (raw dicts/Strings
   fallen weiter auf v1 zurück). Damit rendern **Modelle über die
   v2-Envelope ohne Adapter.**

Konsequenz: **SQLList-Module sprechen Envelope v2.** Auf den von
`viur.actions.install()` gemounteten `/json/v2/`- · `/vi/v2/`-Pfaden läuft
alles über `self.render.*` wie bei jedem viur-Modul. Die v1-JSON-API für
SQL-Module bleibt **out of scope** (der isinstance-Check in viur-cores
`DefaultRender.renderEntry` müsste auf Protokoll-Prüfung umgestellt werden —
als Core-Change-Kandidat notiert).

## 2. Abhängigkeiten & Ort

- `SQLList` lebt in `viur.models.sqllist` und wird **lazy** importiert —
  die reine Modell-Schicht (`fields`/`types`/`structure`/`base`) bleibt frei
  von viur-core-/viur-actions-Importen zur Importzeit.
- **viur-actions wird Dependency** von viur-models (`@action`,
  `ActionModule`, Envelope). Die Hook-Maschinerie (`can<X>`/`on<X>`/
  `then<X>`/`<X>Skel`-Auflösung zur Klassendefinitionszeit) wird unverändert
  wiederverwendet — SQLList erfindet kein zweites Hook-System.

## 3. Modul-Klasse & Action-Set

```python
# viur.models.sqllist
class SQLList(ActionModule, Module):
    """SQL-Gegenstück zum List-Prototyp. Konkrete Module setzen ``model``."""

    model: type[ViURModel]            # das servierte Model (Pflicht)

    kindName: str                     # default: model._viur_kind()
```

Endpunkte spiegeln den `List`-Prototyp (Signaturen + Decorator-Stack):

| Action | Signatur | Decorators | Rückgabe |
|---|---|---|---|
| `list` | `list(self, **kwargs)` | `@exposed` | `self.render.list(ModelList(...))` |
| `view` | `view(self, key, **kwargs)` | `@exposed` | `self.render.view(instance)` |
| `add` | `add(self, **kwargs)` | `@force_ssl @exposed @skey(allow_empty=True)` | Formular-Envelope (GET/leer) oder `addSuccess` |
| `edit` | `edit(self, key, **kwargs)` | `@force_ssl @exposed @skey(allow_empty=True)` | Formular-Envelope oder `editSuccess` |
| `delete` | `delete(self, key, **kwargs)` | `@force_ssl @force_post @exposed @skey` | `deleteSuccess` (Entity im Response, v2-Verhalten) |
| `structure` | `structure(self, action="view")` | `@exposed` | Structure-Envelope |

Nicht in v1: `preview`, `clone`, `index`, `getDefaultListParams` (§9).

### 3.1 Hooks

Alle Actions tragen `@action`; die viur-actions-Auflösung liefert pro
Action `can`/`on`/`then`/`skel`. Semantik in SQLList:

- **`can<X>(instance_or_None)`** — Permission-Check, fail-closed
  (`can`-Default wirft `Forbidden`). `view`/`edit`/`delete` übergeben die
  geladene Instanz, `add`/`list` `None`.
- **`on<X>(instance)`** — vor dem Commit (Werte sind gesetzt/validiert).
- **`then<X>(instance)`** — nach dem Commit.
- **`<x>Skel()`** — Factory-Slot; Default liefert `cls.model`. Overrides
  pro Action (`editSkel` → eingeschränktes Model) sind möglich, müssen
  aber ein ViURModel (Klasse) liefern. Der Slot-Name bleibt `Skel` —
  bewusst, damit die viur-actions-Maschinerie unverändert bleibt.

### 3.2 Ablauf pro Action (Soll)

```
edit(key, **kwargs):
    model_cls = editSkel()                        # Hook-Slot
    pk = model_cls.viur_parse_key(key)  or → NotFound
    with get_session() as session:
        instance = session.get(model_cls, pk)     or → NotFound
        canEdit(instance)                          or → Forbidden
        if not kwargs (GET / leeres POST):
            return self.render.edit(instance)      # Formular, action="edit"
        merged = instance.viur_dump() ohne readonly | kwargs
        new, errors = model_cls.viur_from_client(merged)
        if errors:
            return self.render.edit(instance, errors=errors)   # rejected
        instance <- Felder aus new übernehmen; changedate = now()
        onEdit(instance)
        session.commit()
    thenEdit(instance)
    return self.render.editSuccess(instance)
```

`add` analog ohne Laden; `delete` lädt, prüft `canDelete`, löscht,
rendert `deleteSuccess(instance)` (Entity im Response — v2-Verhalten,
nicht v1s `"OKAY"`). `view` lädt + `canView` + `render.view`.

**Edit ist Merge, nicht Replace:** Basis ist der Dump der Instanz (ohne
readonly-Felder), überschrieben von den Client-Werten — unübermittelte
Felder behalten ihren Wert, exakt wie `skel.fromClient` auf einer
geladenen Skeleton-Instanz.

## 4. Session-Lifecycle (`viur.models.db`)

```python
configure(url_or_engine, **create_engine_kwargs)   # einmal beim App-Boot
get_session() -> contextmanager[Session]           # pro Request-Methode
```

- **Eine Session pro Action-Aufruf**, geöffnet im Modul, Commit bei
  Erfolg, Rollback bei Exception (Contextmanager). Keine globale Session,
  kein Request-Middleware-Magic.
- **App Engine Standard:** `configure()` setzt Default `poolclass=NullPool`
  (Instanzen skalieren auf 0, persistente Pools lecken Verbindungen).
  Cloud-SQL-Anbindung über den `cloud-sql-python-connector` als
  `creator=`-Kwarg — dokumentiert, nicht hart verdrahtet.
- `configure()` vor erstem `get_session()` vergessen → sofortiger
  `RuntimeError` mit Handlungsanweisung (fail fast, kein impliziter
  SQLite-Fallback).

## 5. Query-Parität (`list`)

Parameter wie beim Skeleton-`list`, übersetzt auf `select()`:

| Parameter | Verhalten |
|---|---|
| `limit` | Default 30, hart gekappt auf 100 (core-Verhalten) |
| `orderby` / `orderdir` | nur strukturbekannte, nicht-readonly Felder; `orderdir` ∈ {`0`/`asc`, `1`/`desc`}; Ergebnis erscheint in `orders` der Envelope |
| `<feldname>=<wert>` | Gleichheitsfilter (Liste → `IN`); **nur** strukturbekannte, schreibbare Skalarfelder (Spalten-Lookup über das Model — kein String-Interpolat, injection-sicher by construction); Relationen und write-only-Felder sind nicht filterbar |
| `<feld>$lt / $le / $gt / $ge` | Vergleichsoperatoren (Core-Query-Sprache aus `buildDBFilter`); Werte für numeric-Spalten werden koerziert, Unbrauchbares wird ignoriert |
| `<feld>$lk=<prefix>` | case-insensitiver Prefix-Match (`ilike 'prefix%'`, LIKE-Wildcards escaped) — StringBone-Semantik |
| `search=<begriff>` | Volltext: OR-`ilike '%begriff%'` über alle str/text-Felder (ohne write-only/languages); ohne durchsuchbare Felder ist die Query unerfüllbar — wie Core ohne Fulltext-Adapter |
| `cursor` | opak, **Keyset**: kodiert die Sortierwerte der letzten Zeile (gebunden an orderby/orderdir — Mismatch startet von vorn); Seek statt OFFSET → stabil bei parallelen Inserts/Deletes, O(1) unabhängig von der Seitentiefe. `id` ist immer Tiebreaker, Sortierspalten ordnen mit `NULLS LAST` (backend-unabhängig; NULL-Tail paginiert korrekt) |

Rückgabe ist eine `ModelList` (list-Subklasse) mit `getCursor()` /
`get_orders()`, damit `render_list` Cursor und Sortierung in die Envelope
schreibt: Cursor = nächstes Offset (oder `None` am Ende), Orders im
Skeleton-Format. `limit+1`-Fetch entscheidet, ob es eine nächste Seite
gibt.

Der `listFilter`-Hook des Skeleton-List (Query-Einschränkung) heißt hier
**`sqlFilter(stmt) -> stmt`** — bekommt das SQLAlchemy-Statement und darf
es einschränken (Mandanten-Filter etc.). Default: unverändert.

## 6. Fehlerfälle

| Fall | Verhalten |
|---|---|
| Key nicht parsebar / nicht gefunden | `viur.core.errors.NotFound` |
| `can<X>` falsy | `viur.core.errors.Forbidden` |
| Validierungsfehler | Formular-Envelope mit `errors` (Status `rejected`), HTTP 200 — Envelope-v2-Semantik |
| unbekannte `orderby`/Filter-Felder | ignoriert (core-Verhalten), **nicht** 400 |

## 7. Registrierung im Projekt

```python
# deploy/models/feedback.py     — das Model (Dokument 01)
# deploy/modules/feedback.py    — das Modul:
from viur.models.sqllist import SQLList
from models.feedback import Feedback

class feedback(SQLList):
    model = Feedback

    def canEdit(self, instance): ...
```

`viur.models.db.configure(...)` gehört in `deploy/main.py` neben
`viur.actions.install()`.

## 8. Tests

- **Unit** (`tests/`, light-mock + SQLite in-memory): Session-Lifecycle
  (configure-Guard, Commit/Rollback), Query-Builder (limit-Kappung,
  orderby-Whitelist, Filter-Whitelist, Cursor-Roundtrip), Merge-Semantik
  von `edit`.
- **Integration** (`integration/`, echter Core + echtes viur-actions +
  SQLite): SQLList-Modul instanziieren, Envelope-JSON von
  `list`/`view`/`add`/`edit`/`delete` gegen das Envelope-Schema und — wo
  sinnvoll — gegen ein Skeleton-Zwillingsmodul vergleichen.

## 9. Explizit außerhalb von v1

| Feature | Pfad |
|---|---|
| v1-JSON-Render (`/json/` DefaultRender) | Core-Change: Protokoll- statt isinstance-Check in `renderEntry` |
| `preview`, `clone`, `index` | nach Bedarf |
| Relationen laden/expandieren | folgt dem Relational-Mapping (01 §5.4) |
| HTML-Render | Jinja-Kontext braucht das Skeleton-Interface vollständig; nach dem Relational-Mapping |
