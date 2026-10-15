from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher

from src.core import generate_barcode_from_sku, parse_date

DB_PATH = os.path.join("data", "trazabilitat.db")


def connect_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = connect_db(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            supplier TEXT,
            supplier_lot TEXT,
            provenance TEXT,
            expiry TEXT,
            barcode TEXT,
            created_at TEXT NOT NULL,
            notes TEXT
        )
        """
    )
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(raw_materials)")}
    if "supplier" not in existing_columns:
        conn.execute("ALTER TABLE raw_materials ADD COLUMN supplier TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS packaged_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            packed_lot TEXT NOT NULL,
            supplier_lot TEXT,
            provenance TEXT,
            expiry TEXT,
            barcode TEXT,
            quantity INTEGER,
            weight REAL,
            created_at TEXT NOT NULL,
            session_id INTEGER,
            ingredients_json TEXT,
            sku TEXT
        )
        """
    )
    existing_packaged_columns = {row["name"] for row in conn.execute("PRAGMA table_info(packaged_products)")}
    if "sku" not in existing_packaged_columns:
        conn.execute("ALTER TABLE packaged_products ADD COLUMN sku TEXT")
    if "prepared_mixture_id" not in existing_packaged_columns:
        conn.execute("ALTER TABLE packaged_products ADD COLUMN prepared_mixture_id INTEGER")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT,
            name TEXT NOT NULL,
            weight REAL,
            source_file TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    existing_catalog_columns = {row["name"] for row in conn.execute("PRAGMA table_info(product_catalog)")}
    for column in ("edited_sku", "edited_name", "edited_weight"):
        if column not in existing_catalog_columns:
            conn.execute(f"ALTER TABLE product_catalog ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
    if "category" not in existing_catalog_columns:
        conn.execute("ALTER TABLE product_catalog ADD COLUMN category TEXT")
    for column, col_type in (("original_sku", "TEXT"), ("original_name", "TEXT"), ("original_weight", "REAL")):
        if column not in existing_catalog_columns:
            conn.execute(f"ALTER TABLE product_catalog ADD COLUMN {column} {col_type}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            sku TEXT,
            name TEXT NOT NULL,
            quantity REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prepared_mixtures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sku TEXT,
            lot TEXT NOT NULL,
            expiry TEXT NOT NULL,
            barcode TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            ingredients_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def create_session(conn: sqlite3.Connection, notes: str = "") -> int:
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO sessions (started_at, notes) VALUES (?, ?)",
        (started_at, notes),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_sessions(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM sessions ORDER BY started_at DESC, id DESC").fetchall()


def list_session_packaged_products(conn: sqlite3.Connection, session_id: int, term: str = ""):
    return list_history_packaged_products(conn, term=term, session_id=session_id)


def list_history_packaged_products(conn: sqlite3.Connection, term: str = "", session_id: int | None = None):
    rows = conn.execute(
        "SELECT p.*, s.started_at AS session_started_at FROM packaged_products p "
        "LEFT JOIN sessions s ON s.id = p.session_id WHERE (? IS NULL OR p.session_id = ?) "
        "ORDER BY s.started_at DESC, p.session_id DESC, p.created_at DESC, p.id DESC",
        (session_id, session_id),
    ).fetchall()
    query = _suggestion_key(term)
    columns = ("name", "packed_lot", "supplier_lot", "provenance", "expiry", "created_at")
    return [row for row in rows if not query or any(query in _suggestion_key(row[column]) for column in columns)]


def search_history_suggestions(conn: sqlite3.Connection, term: str, session_id: int | None = None, limit: int = 8):
    if not _suggestion_key(term):
        return []
    candidates = []
    seen = set()
    for row in list_history_packaged_products(conn, session_id=session_id):
        for column in ("name", "packed_lot", "supplier_lot", "provenance", "expiry", "created_at"):
            value = row[column]
            key = _suggestion_key(value)
            if key and key not in seen:
                seen.add(key)
                candidates.append({"value": value})
    matches = [row["value"] for row in _rank_suggestions(candidates, term, ("value",), limit)]
    query = _suggestion_key(term)
    if len(matches) >= limit or len(query) < 3 or query.isdecimal():
        return matches
    approximate = []
    compact_query = query.replace(" ", "")
    word_count = len(query.split())
    for index, candidate in enumerate(candidates):
        value = candidate["value"]
        if value in matches:
            continue
        normalized = _suggestion_key(value)
        words = normalized.split()
        variants = [normalized.replace(" ", "")]
        variants.extend(
            "".join(words[start:start + count])
            for count in range(max(1, word_count - 1), word_count + 2)
            for start in range(len(words))
        )
        similarity = max(SequenceMatcher(None, compact_query, variant).ratio() for variant in variants)
        if similarity >= 0.66:
            approximate.append((-similarity, index, value))
    approximate.sort()
    return matches + [value for score, index, value in approximate[:limit - len(matches)]]


def add_raw_material(
    conn: sqlite3.Connection,
    name: str,
    supplier: str,
    supplier_lot: str,
    provenance: str,
    expiry: str,
    barcode: str,
    notes: str = "",
) -> int:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """
        INSERT INTO raw_materials (name, supplier, supplier_lot, provenance, expiry, barcode, created_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, supplier, supplier_lot, provenance, expiry, barcode, created_at, notes),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_raw_material(
    conn: sqlite3.Connection,
    material_id: int,
    name: str,
    supplier: str,
    supplier_lot: str,
    provenance: str,
    expiry: str,
    *,
    barcode: str | None = None,
    created_at: str | None = None,
) -> None:
    with conn:
        if barcode is not None:
            if not barcode.strip():
                raise ValueError("Cal indicar el codi de barres.")
            if conn.execute(
                "SELECT id FROM raw_materials WHERE barcode = ? AND id <> ? "
                "UNION ALL SELECT id FROM prepared_mixtures WHERE barcode = ?",
                (barcode, material_id, barcode),
            ).fetchone():
                raise ValueError("Ja existeix una recepció o barreja amb aquest codi de barres.")
        conn.execute(
            "UPDATE raw_materials SET name = ?, supplier = ?, supplier_lot = ?, provenance = ?, expiry = ?, "
            "barcode = COALESCE(?, barcode), created_at = COALESCE(?, created_at) WHERE id = ?",
            (name, supplier, supplier_lot, provenance, expiry, barcode, created_at, material_id),
        )


def add_packaged_product(
    conn: sqlite3.Connection,
    name: str,
    packed_lot: str,
    supplier_lot: str,
    provenance: str,
    expiry: str,
    barcode: str,
    quantity: int,
    weight: float,
    session_id: int,
    ingredients: list[dict] | None = None,
    sku: str | None = None,
    prepared_mixture_id: int | None = None,
) -> int:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cleaned_ingredients = []
    for item in ingredients or []:
        safe_item = dict(item)
        expiry_value = safe_item.get("expiry")
        if expiry_value is not None and not isinstance(expiry_value, str):
            safe_item["expiry"] = expiry_value.isoformat()
        cleaned_ingredients.append(safe_item)

    ingredients_data = json.dumps(cleaned_ingredients)
    cursor = conn.execute(
        """
        INSERT INTO packaged_products (
            name, packed_lot, supplier_lot, provenance, expiry, barcode, quantity, weight, created_at, session_id, ingredients_json, sku, prepared_mixture_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, packed_lot, supplier_lot, provenance, expiry, barcode, quantity, weight, created_at, session_id, ingredients_data, sku, prepared_mixture_id),
    )
    conn.commit()
    return int(cursor.lastrowid)


def count_packed_lots_for_sku_on_date(conn: sqlite3.Connection, sku: str, date_str: str) -> int:
    """Compta lots diferents d'envasats i preparacions per reservar la seqüència diària d'un SKU."""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT lot) AS total FROM (
            SELECT packed_lot AS lot FROM packaged_products WHERE sku = ? AND substr(created_at, 1, 10) = ?
            UNION ALL
            SELECT lot FROM prepared_mixtures WHERE sku = ? AND substr(created_at, 1, 10) = ?
        )
        """,
        (sku, date_str, sku, date_str),
    ).fetchone()
    return int(row["total"]) if row else 0


def next_inventory_barcode(conn, sku, reference_date: date | None = None):
    reference_date = reference_date or date.today()
    sku = sku or ""
    prefix = f"{reference_date.strftime('%Y%m%d')}{sku}"
    rows = conn.execute(
        "SELECT barcode FROM raw_materials WHERE substr(barcode, 1, ?) = ? AND length(barcode) = ? "
        "UNION ALL SELECT barcode FROM prepared_mixtures WHERE substr(barcode, 1, ?) = ? AND length(barcode) = ?",
        (len(prefix), prefix, len(prefix) + 2, len(prefix), prefix, len(prefix) + 2),
    ).fetchall()
    sequence = max((int(row["barcode"][-2:]) for row in rows if row["barcode"][-2:].isdigit()), default=0) + 1
    if sequence > 99:
        raise ValueError("Ja s'han utilitzat els 99 codis de barres disponibles avui per a aquest SKU.")
    return generate_barcode_from_sku(sku, reference_date=reference_date, sequence=sequence)


def add_prepared_mixture(conn, name, sku, lot, expiry, ingredients):
    created_at = datetime.now()
    barcode = next_inventory_barcode(conn, sku, created_at.date())
    cursor = conn.execute(
        """
        INSERT INTO prepared_mixtures (name, sku, lot, expiry, barcode, created_at, ingredients_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, sku or "", lot, expiry, barcode,
         created_at.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(ingredients)),
    )
    conn.commit()
    return conn.execute("SELECT * FROM prepared_mixtures WHERE id = ?", (cursor.lastrowid,)).fetchone()


def update_prepared_mixture(conn, mixture_id, lot, expiry, barcode, created_at, ingredients):
    if not lot.strip() or not barcode.strip():
        raise ValueError("Cal indicar el lot de sortida i el codi de barres.")
    expiry_date = parse_date(expiry)
    if expiry_date is None:
        raise ValueError("Cal indicar el consum preferent.")
    try:
        produced_at = datetime.strptime(created_at.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        produced_date = parse_date(created_at)
        if produced_date is None:
            raise ValueError("Cal indicar la data de producció.")
        produced_at = datetime.combine(produced_date, datetime.min.time())

    def clean_ingredients(items):
        if not isinstance(items, list) or not items:
            raise ValueError("Cal indicar almenys un ingredient.")
        cleaned = []
        for item in items:
            if not isinstance(item, dict) or not (str(item.get("name") or "").strip() or str(item.get("sku") or "").strip()):
                raise ValueError("Cada ingredient ha de tenir nom o SKU.")
            ingredient = dict(item)
            ingredient_expiry = parse_date(ingredient.get("expiry"))
            ingredient["expiry"] = ingredient_expiry.isoformat() if ingredient_expiry else ""
            if str(ingredient.get("created_at") or "").strip():
                try:
                    received_at = datetime.strptime(ingredient["created_at"].strip(), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    received_at = datetime.combine(parse_date(ingredient["created_at"]), datetime.min.time())
                ingredient["created_at"] = received_at.strftime("%Y-%m-%d %H:%M:%S")
            elif "created_at" in ingredient:
                ingredient["created_at"] = ""
            if ingredient.get("quantity") not in (None, ""):
                quantity = float(str(ingredient["quantity"]).replace(",", "."))
                if not math.isfinite(quantity) or quantity < 0:
                    raise ValueError("La quantitat d'un ingredient ha de ser un número finit no negatiu.")
                ingredient["quantity"] = quantity
            elif "quantity" in ingredient:
                ingredient["quantity"] = None
            if ingredient.get("ingredients"):
                ingredient["ingredients"] = clean_ingredients(ingredient["ingredients"])
            cleaned.append(ingredient)
        return cleaned

    ingredients = clean_ingredients(ingredients)
    with conn:
        if conn.execute(
            "SELECT id FROM prepared_mixtures WHERE barcode = ? AND id <> ? "
            "UNION ALL SELECT id FROM raw_materials WHERE barcode = ?",
            (barcode.strip(), mixture_id, barcode.strip()),
        ).fetchone():
            raise ValueError("Ja existeix una recepció o preparació amb aquest codi de barres.")
        conn.execute(
            "UPDATE prepared_mixtures SET lot = ?, expiry = ?, barcode = ?, created_at = ?, ingredients_json = ? WHERE id = ?",
            (lot.strip(), expiry_date.isoformat(), barcode.strip(), produced_at.strftime("%Y-%m-%d %H:%M:%S"),
             json.dumps(ingredients), mixture_id),
        )


def list_prepared_mixtures(conn):
    return conn.execute("SELECT * FROM prepared_mixtures ORDER BY created_at DESC, id DESC").fetchall()


def get_packaging_ingredient_by_barcode(conn, barcode):
    mixture = conn.execute(
        """
        SELECT name, sku, lot AS supplier_lot, '' AS provenance, '' AS supplier, expiry, barcode, created_at,
               id AS mixture_id, ingredients_json
        FROM prepared_mixtures WHERE barcode = ?
        """, (barcode,),
    ).fetchone()
    return mixture if mixture is not None else get_material_by_barcode(conn, barcode)


def _suggestion_key(value):
    return " ".join("".join(
        character for character in unicodedata.normalize("NFD", str(value or "").casefold())
        if not unicodedata.combining(character)
    ).split())


def _rank_suggestions(rows, term, columns, limit):
    query = _suggestion_key(term)
    if not query:
        return []
    ranked = []
    for index, row in enumerate(rows):
        best = (4, 0.0)
        for column in columns:
            value = _suggestion_key(row[column])
            if not value:
                continue
            if value == query:
                score = (0, 0.0)
            elif value.startswith(query):
                score = (1, 0.0)
            elif query in value:
                score = (2, 0.0)
            elif len(query) >= 4 and not query.isdecimal() and column != "sku":
                words = value.split()
                window = len(query.split())
                candidates = [value] + [" ".join(words[start:start + window]) for start in range(len(words))]
                similarity = max(SequenceMatcher(None, query, candidate).ratio() for candidate in candidates)
                score = (3, -similarity) if similarity >= 0.78 else (4, 0.0)
            else:
                score = (4, 0.0)
            best = min(best, score)
        if best[0] < 4:
            ranked.append((best, index, row))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [row for score, index, row in ranked[:limit]]


def search_packaging_ingredients(conn, term, limit=8):
    if not term:
        return []
    rows = conn.execute(
        """
        WITH available AS (
            SELECT rm.name, rm.supplier_lot, rm.provenance, rm.supplier, rm.expiry, rm.barcode, rm.created_at,
                   (SELECT pc.sku FROM product_catalog pc
                    WHERE LOWER(TRIM(pc.name)) = LOWER(TRIM(rm.name)) AND pc.sku != ''
                    ORDER BY pc.created_at DESC, pc.id DESC LIMIT 1) AS sku,
                   NULL AS mixture_id, NULL AS ingredients_json
            FROM raw_materials rm
            UNION ALL
            SELECT name, lot, '', '', expiry, barcode, created_at, sku, id, ingredients_json
            FROM prepared_mixtures
        )
        SELECT * FROM available ORDER BY created_at DESC
        """,
    ).fetchall()
    return _rank_suggestions(rows, term, ("name", "sku"), limit)


def upsert_catalog_rows(conn: sqlite3.Connection, rows: list[dict], source_file: str) -> int:
    """Insereix o actualitza (per SKU) les files normalitzades d'un CSV de productes.

    Si el SKU ja existeix al catàleg (a la fila més recent amb aquest SKU), s'actualitza en lloc
    de duplicar-lo. Les files sense SKU sempre s'insereixen com a noves.
    """
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    count = 0
    for item in rows:
        sku = item.get("sku", "")
        name = item.get("name", "")
        weight = item.get("weight", 0.0)
        category = item.get("category", "")
        if not name:
            continue
        existing = None
        if sku:
            existing = conn.execute(
                "SELECT id FROM product_catalog WHERE sku = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (sku,),
            ).fetchone()
        if existing:
            conn.execute(
                "UPDATE product_catalog SET name = ?, weight = ?, category = ?, source_file = ?, created_at = ? WHERE id = ?",
                (name, weight, category, source_file, created_at, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO product_catalog (sku, name, weight, category, source_file, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sku, name, weight, category, source_file, created_at),
            )
        count += 1
    conn.commit()
    return count


def replace_catalog_rows(conn: sqlite3.Connection, rows: list[dict], source_file: str) -> int:
    """Esborra tot el catàleg anterior i hi carrega només les files del nou CSV."""
    conn.execute("DELETE FROM product_catalog")
    return upsert_catalog_rows(conn, rows, source_file)


def list_catalog_products(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM product_catalog ORDER BY name COLLATE NOCASE").fetchall()


_CATALOG_EDITABLE_COLUMNS = {"sku", "name", "weight"}


def update_catalog_field(conn: sqlite3.Connection, row_id: int, field: str, value) -> None:
    """Actualitza un camp editable del catàleg i el marca com a modificat manualment.

    La primera vegada que s'edita un camp es guarda el seu valor anterior a `original_<camp>`
    perquè es pugui revertir més endavant amb `revert_catalog_field`/`revert_catalog_row`.
    """
    if field not in _CATALOG_EDITABLE_COLUMNS:
        raise ValueError(f"Camp no editable: {field}")
    current = conn.execute(
        f"SELECT {field} AS current_value, edited_{field} AS already_edited FROM product_catalog WHERE id = ?",
        (row_id,),
    ).fetchone()
    if current is not None and not current["already_edited"]:
        conn.execute(
            f"UPDATE product_catalog SET original_{field} = ? WHERE id = ?",
            (current["current_value"], row_id),
        )
    conn.execute(
        f"UPDATE product_catalog SET {field} = ?, edited_{field} = 1 WHERE id = ?",
        (value, row_id),
    )
    conn.commit()


def revert_catalog_field(conn: sqlite3.Connection, row_id: int, field: str) -> None:
    """Desfà la modificació manual d'un sol camp, restaurant el valor que tenia abans d'editar-lo."""
    if field not in _CATALOG_EDITABLE_COLUMNS:
        raise ValueError(f"Camp no editable: {field}")
    conn.execute(
        f"UPDATE product_catalog SET {field} = original_{field}, edited_{field} = 0, original_{field} = NULL "
        f"WHERE id = ? AND edited_{field} = 1",
        (row_id,),
    )
    conn.commit()


def revert_catalog_row(conn: sqlite3.Connection, row_id: int) -> None:
    """Desfà totes les modificacions manuals (SKU, Nom, Pes) d'un producte del catàleg."""
    for field in _CATALOG_EDITABLE_COLUMNS:
        revert_catalog_field(conn, row_id, field)


def revert_all_catalog_edits(conn: sqlite3.Connection) -> int:
    """Desfà totes les modificacions manuals de tot el catàleg. Retorna el nombre de productes afectats."""
    rows = list_edited_catalog_products(conn)
    for row in rows:
        revert_catalog_row(conn, row["id"])
    return len(rows)


def get_catalog_edit_counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COALESCE(SUM(edited_sku), 0) AS sku, COALESCE(SUM(edited_name), 0) AS name, "
        "COALESCE(SUM(edited_weight), 0) AS weight FROM product_catalog"
    ).fetchone()
    return {"sku": int(row["sku"]), "name": int(row["name"]), "weight": int(row["weight"])}


def list_edited_catalog_products(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM product_catalog WHERE edited_sku = 1 OR edited_name = 1 OR edited_weight = 1 "
        "ORDER BY name COLLATE NOCASE"
    ).fetchall()


def get_catalog_weight_by_name(conn: sqlite3.Connection, name: str):
    """Retorna el pes més recent importat pel CSV per a un producte, si n'hi ha."""
    row = conn.execute(
        "SELECT weight FROM product_catalog WHERE name = ? ORDER BY created_at DESC LIMIT 1",
        (name,),
    ).fetchone()
    return row["weight"] if row else None


def get_catalog_sku_by_name(conn: sqlite3.Connection, name: str):
    """Retorna el SKU més recent importat pel CSV per a un producte, si n'hi ha.

    La comparació ignora majúscules/minúscules i espais sobrants als extrems.
    """
    row = conn.execute(
        "SELECT sku FROM product_catalog WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND sku != '' ORDER BY created_at DESC LIMIT 1",
        (name,),
    ).fetchone()
    return row["sku"] if row else None


def get_catalog_row_by_sku(conn: sqlite3.Connection, sku: str):
    """Retorna la fila més recent del catàleg amb aquest SKU exacte, si n'hi ha."""
    if not sku:
        return None
    return conn.execute(
        "SELECT * FROM product_catalog WHERE sku = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (sku,),
    ).fetchone()


def get_catalog_row_by_name(conn: sqlite3.Connection, name: str):
    """Retorna la fila més recent del catàleg amb aquest nom (ignorant majúscules/espais), si n'hi ha."""
    if not name:
        return None
    return conn.execute(
        "SELECT * FROM product_catalog WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) ORDER BY created_at DESC, id DESC LIMIT 1",
        (name,),
    ).fetchone()


def count_barcodes_with_prefix(conn: sqlite3.Connection, prefix: str) -> int:
    """Compta quants codis de barres de raw_materials comencen exactament pel prefix indicat.

    S'utilitza per calcular el n\u00famero de seq\u00fc\u00e8ncia diari (data + SKU) d'un producte rebut.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM raw_materials WHERE barcode LIKE ? || '%'",
        (prefix,),
    ).fetchone()
    return int(row["total"]) if row else 0

_ALLOWED_SUGGESTION_COLUMNS = {"supplier", "provenance"}


def search_distinct_raw_material_values(conn: sqlite3.Connection, column: str, term: str, limit: int = 8):
    """Retorna valors distints similars al text, ignorant accents i acceptant fragments.

    Només s'accepten les columnes de `_ALLOWED_SUGGESTION_COLUMNS` per evitar interpolar noms de columna
    arbitraris a la consulta SQL.
    """
    if column not in _ALLOWED_SUGGESTION_COLUMNS:
        raise ValueError(f"Columna no permesa per a suggeriments: {column}")
    if not term:
        return []
    query = (
        f"SELECT DISTINCT {column} AS value FROM raw_materials "
        f"WHERE {column} IS NOT NULL AND TRIM({column}) != '' "
        f"ORDER BY {column} COLLATE NOCASE"
    )
    rows = conn.execute(query).fetchall()
    return [row["value"] for row in _rank_suggestions(rows, term, ("value",), limit)]

def search_received_materials(conn: sqlite3.Connection, term: str, limit: int = 8):
    """Retorna lots rebuts amb nom o SKU similar al text de cerca.

    A diferència de `search_distinct_raw_material_values`, aquí es retorna cada lot rebut
    individualment (amb el seu propi codi de barres, lot, caducitat i data d'entrada), ja
    que s'utilitza per triar un lot concret a l'hora d'envasar-lo.
    """
    if not term:
        return []
    rows = conn.execute(
        """
        WITH enriched AS (
            SELECT rm.name AS name, rm.supplier_lot AS supplier_lot, rm.provenance AS provenance,
                   rm.expiry AS expiry, rm.barcode AS barcode, rm.created_at AS created_at,
                   (
                       SELECT pc.sku FROM product_catalog pc
                       WHERE LOWER(TRIM(pc.name)) = LOWER(TRIM(rm.name)) AND pc.sku != ''
                       ORDER BY pc.created_at DESC LIMIT 1
                   ) AS sku
            FROM raw_materials rm
        )
        SELECT * FROM enriched
        ORDER BY created_at DESC
        """,
    ).fetchall()
    return _rank_suggestions(rows, term, ("name", "sku"), limit)


def search_catalog_products(conn: sqlite3.Connection, term: str, limit: int = 8):
    """Retorna productes amb nom o SKU similar al text, ignorant accents.

    Nom\u00e9s es retorna l'entrada m\u00e9s recent per a cada nom (per si s'ha importat m\u00e9s d'un cop).
    """
    if not term:
        return []
    rows = conn.execute(
        """
        SELECT p.name AS name, p.sku AS sku
        FROM product_catalog p
                WHERE p.id = (
              SELECT id FROM product_catalog p2
              WHERE LOWER(TRIM(p2.name)) = LOWER(TRIM(p.name))
              ORDER BY created_at DESC, id DESC
              LIMIT 1
          )
        ORDER BY p.name COLLATE NOCASE
        """,
    ).fetchall()
    return _rank_suggestions(rows, term, ("name", "sku"), limit)


def list_raw_materials(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM raw_materials ORDER BY created_at DESC"
    ).fetchall()


def list_packaged_products(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT p.*, m.barcode AS prepared_mixture_barcode FROM packaged_products p "
        "LEFT JOIN prepared_mixtures m ON m.id = p.prepared_mixture_id ORDER BY p.created_at DESC"
    ).fetchall()


def get_material_by_barcode(conn: sqlite3.Connection, barcode: str):
    return conn.execute(
        "SELECT * FROM raw_materials WHERE barcode = ? OR supplier_lot = ? LIMIT 1",
        (barcode, barcode),
    ).fetchone()


def search_packaged(conn: sqlite3.Connection, term: str):
    like_term = f"%{term}%"
    return conn.execute(
        """
        SELECT * FROM packaged_products
        WHERE name LIKE ?
           OR packed_lot LIKE ?
           OR supplier_lot LIKE ?
           OR provenance LIKE ?
           OR expiry LIKE ?
           OR created_at LIKE ?
        ORDER BY created_at DESC
        """,
        (like_term, like_term, like_term, like_term, like_term, like_term),
    ).fetchall()


def export_session_csv(conn: sqlite3.Connection, session_id: int, target_path: str):
    rows = conn.execute(
        "SELECT name, packed_lot AS lot, expiry AS caducitat, provenance AS origen, quantity AS quantitat FROM packaged_products WHERE session_id = ? ORDER BY created_at DESC",
        (session_id,),
    ).fetchall()

    with open(target_path, "w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["nom", "lot", "caducitat", "origen", "quantitat"])
        for row in rows:
            writer.writerow([row["name"], row["lot"], row["caducitat"], row["origen"], row["quantitat"]])

    return len(rows)


def upsert_recipe(
    conn: sqlite3.Connection,
    sku: str,
    name: str,
    ingredients: list[dict],
    recipe_id: int | None = None,
) -> int:
    with conn:
        return _upsert_recipe(conn, sku, name, ingredients, recipe_id)


def _upsert_recipe(
    conn: sqlite3.Connection,
    sku: str,
    name: str,
    ingredients: list[dict],
    recipe_id: int | None = None,
) -> int:
    """Insereix una recepta nova o actualitza'n una existent (per `recipe_id` o, si no, pel SKU).

    `ingredients` és una llista de diccionaris amb `sku`, `name` i `quantity`. En actualitzar, es
    reemplacen tots els ingredients anteriors pels nous (no es fa merge parcial).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_id = recipe_id
    if existing_id is None and sku:
        row = conn.execute("SELECT id FROM recipes WHERE sku = ?", (sku,)).fetchone()
        if row:
            existing_id = row["id"]
    if existing_id is not None:
        conn.execute(
            "UPDATE recipes SET sku = ?, name = ?, updated_at = ? WHERE id = ?",
            (sku, name, now, existing_id),
        )
        conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (existing_id,))
        recipe_id = existing_id
    else:
        cursor = conn.execute(
            "INSERT INTO recipes (sku, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sku, name, now, now),
        )
        recipe_id = int(cursor.lastrowid)
    for item in ingredients:
        conn.execute(
            "INSERT INTO recipe_ingredients (recipe_id, sku, name, quantity) VALUES (?, ?, ?, ?)",
            (recipe_id, item.get("sku") or "", item.get("name", ""), item.get("quantity")),
        )
    return recipe_id


def validate_recipes_data(payload) -> list[dict]:
    if isinstance(payload, dict):
        if payload.get("format", "erola.recipes") != "erola.recipes" or payload.get("version", 1) != 1:
            raise ValueError("Format o versió de receptes no compatible.")
        payload = payload.get("recipes")
    if not isinstance(payload, list) or not payload:
        raise ValueError("El fitxer ha de contenir una llista de receptes no buida.")
    result = []
    seen = set()
    for index, recipe in enumerate(payload, start=1):
        if not isinstance(recipe, dict):
            raise ValueError(f"La recepta {index} no és un objecte JSON.")
        name = recipe.get("name")
        sku = recipe.get("sku", "")
        if sku is None:
            sku = ""
        if not isinstance(name, str) or not name.strip() or not isinstance(sku, str):
            raise ValueError(f"Nom o SKU no vàlid a la recepta {index}. L'SKU ha de ser text.")
        name, sku = name.strip(), sku.strip()
        key = ("sku", sku) if sku else ("name", name.casefold())
        if key in seen:
            raise ValueError(f"Recepta duplicada al fitxer: {name} ({sku}).")
        seen.add(key)
        ingredients = recipe.get("ingredients")
        if not isinstance(ingredients, list) or not ingredients:
            raise ValueError(f"La recepta '{name}' ha de tenir almenys un ingredient.")
        cleaned = []
        for ingredient in ingredients:
            if not isinstance(ingredient, dict):
                raise ValueError(f"Ingredient no vàlid a '{name}'.")
            ing_name = ingredient.get("name", "")
            ing_sku = ingredient.get("sku", "")
            if ing_name is None:
                ing_name = ""
            if ing_sku is None:
                ing_sku = ""
            if not isinstance(ing_name, str) or not isinstance(ing_sku, str) or not (ing_name.strip() or ing_sku.strip()):
                raise ValueError(f"Cada ingredient de '{name}' ha de tenir nom o SKU de text.")
            quantity = ingredient.get("quantity")
            if quantity is not None:
                if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                    raise ValueError(f"Quantitat no vàlida a '{name}': cal un número o null.")
                try:
                    quantity = float(quantity)
                except OverflowError as exc:
                    raise ValueError(f"Quantitat massa gran a '{name}'.") from exc
                if not math.isfinite(quantity):
                    raise ValueError(f"Quantitat no vàlida a '{name}': cal un número finit.")
            cleaned.append({"name": ing_name.strip(), "sku": ing_sku.strip(), "quantity": quantity})
        result.append({"name": name, "sku": sku, "ingredients": cleaned})
    return result


def read_recipes_json(target_path: str) -> list[dict]:
    with open(target_path, encoding="utf-8-sig") as source:
        return validate_recipes_data(json.load(source))


def export_recipes_json(conn: sqlite3.Connection, target_path: str, recipe_ids: list[int] | None = None) -> int:
    ids = recipe_ids if recipe_ids is not None else [row["id"] for row in list_recipes(conn)]
    recipes = []
    for recipe_id in ids:
        data = get_recipe_by_id(conn, recipe_id)
        if data is None:
            raise ValueError(f"La recepta {recipe_id} ja no existeix.")
        recipe = data["recipe"]
        recipes.append({
            "sku": recipe["sku"] or "", "name": recipe["name"],
            "ingredients": [
                {"sku": row["sku"] or "", "name": row["name"], "quantity": row["quantity"]}
                for row in data["ingredients"]
            ],
        })
    if not recipes:
        raise ValueError("No hi ha receptes per exportar.")
    with open(target_path, "w", encoding="utf-8") as target:
        json.dump({"format": "erola.recipes", "version": 1, "recipes": recipes}, target,
                  ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")
    return len(recipes)


def import_recipes(conn: sqlite3.Connection, recipes: list[dict]) -> int:
    recipes = validate_recipes_data(recipes)
    with conn:
        for recipe in recipes:
            recipe_id = None
            if not recipe["sku"]:
                existing = conn.execute("SELECT id, name FROM recipes WHERE COALESCE(sku, '') = ''").fetchall()
                recipe_id = next((row["id"] for row in existing if row["name"].casefold() == recipe["name"].casefold()), None)
            _upsert_recipe(conn, recipe["sku"], recipe["name"], recipe["ingredients"], recipe_id)
    return len(recipes)


def list_recipes(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM recipes ORDER BY name COLLATE NOCASE").fetchall()


def get_recipe_by_id(conn: sqlite3.Connection, recipe_id: int):
    recipe = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if recipe is None:
        return None
    ingredients = conn.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = ? ORDER BY id", (recipe_id,)
    ).fetchall()
    return {"recipe": recipe, "ingredients": ingredients}


def get_recipe_by_sku(conn: sqlite3.Connection, sku: str):
    if not sku:
        return None
    recipe = conn.execute("SELECT * FROM recipes WHERE sku = ?", (sku,)).fetchone()
    if recipe is None:
        return None
    return get_recipe_by_id(conn, recipe["id"])


def list_recipes_with_ingredients(conn: sqlite3.Connection):
    """Retorna totes les receptes amb el conjunt de claus normalitzades dels seus ingredients.

    S'utilitza per fer coincidència aproximada (sense ordre) amb els ingredients escanejats a Envasat.
    """
    result = []
    for recipe in list_recipes(conn):
        ingredient_rows = conn.execute(
            "SELECT sku, name FROM recipe_ingredients WHERE recipe_id = ?", (recipe["id"],)
        ).fetchall()
        keys = {
            (row["sku"] or row["name"]).strip().lower()
            for row in ingredient_rows
            if (row["sku"] or row["name"])
        }
        result.append(
            {
                "id": recipe["id"],
                "sku": recipe["sku"],
                "name": recipe["name"],
                "ingredient_keys": keys,
                "ingredients": [{"sku": row["sku"] or "", "name": row["name"] or ""} for row in ingredient_rows],
            }
        )
    return result
