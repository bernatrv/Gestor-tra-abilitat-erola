from __future__ import annotations

from datetime import date, datetime
from typing import Iterable


def parse_date(value):
    """Converteix un valor de data (introduït per l'usuari o emmagatzemat interior) a un objecte date.

    Formats acceptats:
    - AAAA-MM-DD i AAAA-MM-DD HH:MM:SS (format intern/ISO, usat per llegir dades ja guardades)
    - DD-MM-AAAA i DD-MM-AA (si no hi ha any de 4 xifres, s'assumeix 20AA)
    - MM-AAAA i MM-AA (es considera dia 1 del mes indicat)

    S'accepten tant '-' com '/' com a separadors.

    Llança ValueError amb un missatge entenedor si el format no és vàlid.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return None

    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        pass

    normalized = text.replace("/", "-")
    parts = normalized.split("-")

    def expand_year(year_text: str) -> int:
        year = int(year_text)
        return year + 2000 if len(year_text) == 2 else year

    try:
        if len(parts) == 3:
            first, second, third = parts
            if len(first) == 4:
                # Format intern AAAA-MM-DD
                return date(int(first), int(second), int(third))
            # DD-MM-AAAA o DD-MM-AA
            day = int(first)
            month = int(second)
            year = expand_year(third)
            return date(year, month, day)
        if len(parts) == 2:
            # MM-AAAA o MM-AA: sense dia especificat, s'assumeix dia 1
            month_text, year_text = parts
            month = int(month_text)
            year = expand_year(year_text)
            return date(year, month, 1)
    except ValueError:
        pass

    raise ValueError(
        f"La data '{text}' no té un format vàlid. Fes servir DD-MM-AAAA, DD-MM-AA, MM-AAAA o MM-AA."
    )



def generate_packed_lot(prefix: str = "LOT") -> str:
    """Genera un número de lot en format PREFIX-YYYYMMDD-HHMMSS."""
    now = datetime.now()
    return f"{prefix}-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"


def generate_daily_sequential_lot(sequence: int, reference_date: date | None = None) -> str:
    """Genera un lot de sortida (envasat) amb format AAMMDD + NN.

    `sequence` és el número de producció del dia (01, 02, ... fins a un màxim de 99),
    independent per cada SKU envasat.
    """
    if not 1 <= sequence <= 99:
        raise ValueError("El número de seqüència diari ha d'estar entre 01 i 99.")
    reference = reference_date or date.today()
    return f"{reference.strftime('%y%m%d')}{sequence:02d}"


def generate_barcode_from_sku(sku: str, reference_date: date | None = None, sequence: int = 1) -> str:
    """Genera un codi de barres amb format AAAAMMDD + SKU + NN.

    `sequence` indica el número d'entrada del mateix producte durant el mateix dia
    (01, 02, ... fins a un màxim de 99).
    """
    if not 1 <= sequence <= 99:
        raise ValueError("El número de seqüència diari ha d'estar entre 01 i 99.")
    reference = reference_date or date.today()
    return f"{reference.strftime('%Y%m%d')}{sku}{sequence:02d}"


def compute_expiry_from_ingredients(ingredients: Iterable[dict]):
    """Calcula la caducitat mínima entre ingredients."""
    valid_dates = []
    for item in ingredients:
        expiry = parse_date(item.get("expiry"))
        if expiry is not None:
            valid_dates.append(expiry)
    if not valid_dates:
        raise ValueError("No hi ha dates de caducitat vàlides als ingredients seleccionats.")
    return min(valid_dates)


def parse_csv_mapping(
    rows: list[dict],
    sku_field: str,
    name_field: str,
    weight_field: str,
    category_field: str | None = None,
) -> list[dict]:
    """Normalitza les files llegides d'un CSV segons les columnes escollides."""
    mapped: list[dict] = []
    for row in rows:
        sku = str(row.get(sku_field, "")).strip()
        name = str(row.get(name_field, "")).strip()
        weight_value = str(row.get(weight_field, "")).strip().replace(",", ".")
        category = str(row.get(category_field, "")).strip() if category_field else ""
        try:
            weight = float(weight_value)
        except ValueError:
            weight = 0.0
        if sku or name:
            mapped.append({"sku": sku, "name": name, "weight": weight, "category": category})
    return mapped



def ingredient_match_key(sku, name) -> str:
    """Clau normalitzada d'un ingredient per comparar-lo sense distingir ordre ni majúscules."""
    return (sku or name or "").strip().lower()


def recipe_match_score(ingredient_keys: set[str], recipe_keys: set[str]) -> float:
    """Coeficient de Dice entre dos conjunts de claus d'ingredients (0.0 si no hi ha intersecció)."""
    if not ingredient_keys or not recipe_keys:
        return 0.0
    intersection = ingredient_keys & recipe_keys
    if not intersection:
        return 0.0
    return (2 * len(intersection)) / (len(ingredient_keys) + len(recipe_keys))


def _normalized(value) -> str:
    return str(value).strip().lower() if value is not None else ""


def ingredients_match(first: dict, second: dict) -> bool:
    """Dos ingredients coincideixen si tenen el mateix SKU o, si no, el mateix nom (sense majúscules)."""
    first_sku, second_sku = _normalized(first.get("sku")), _normalized(second.get("sku"))
    if first_sku and first_sku == second_sku:
        return True
    first_name, second_name = _normalized(first.get("name")), _normalized(second.get("name"))
    return bool(first_name) and first_name == second_name


def ingredient_matches_recipe_ingredients(ingredient: dict, recipe_ingredients: list[dict]) -> bool:
    return any(ingredients_match(ingredient, item) for item in recipe_ingredients)


def ingredients_recipe_score(ingredients: list[dict], recipe_ingredients: list[dict]) -> float:
    """Coeficient de Dice entre els ingredients afegits i els d'una recepta (0.0 si no coincideix cap)."""
    if not ingredients or not recipe_ingredients:
        return 0.0
    remaining = list(recipe_ingredients)
    matches = 0
    for ingredient in ingredients:
        for index, candidate in enumerate(remaining):
            if ingredients_match(ingredient, candidate):
                del remaining[index]
                matches += 1
                break
    return (2 * matches) / (len(ingredients) + len(recipe_ingredients))


def rank_recipes_by_match(ingredients: list[dict], recipes: list[dict]) -> list[tuple[float, dict]]:
    """Puntua totes les receptes contra els `ingredients` (dicts amb `sku`/`name`) de millor a pitjor.

    Cada recepta ha de tenir una clau `ingredients` (llista de dicts amb `sku`/`name`). Retorna tuples
    `(score, recipe)`; les receptes sense coincidència tenen `score = 0.0` i mantenen l'ordre d'entrada.
    """
    scored = [(ingredients_recipe_score(ingredients, recipe.get("ingredients") or []), recipe) for recipe in recipes]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def match_best_recipe(ingredient_keys: set[str], recipes: list[dict]):
    """Troba la recepta amb millor coincidència d'ingredients, sense tenir en compte l'ordre.

    `recipes` és una llista de diccionaris amb almenys una clau `ingredient_keys` (un `set` de claus
    normalitzades). La puntuació és el coeficient de Dice (2 * interseccio / suma de mides), que premia
    coincidències parcials sense exigir una coincidència exacta. Retorna `None` si cap recepta comparteix
    cap ingredient amb `ingredient_keys`.
    """
    best = None
    best_score = 0.0
    for recipe in recipes:
        keys = recipe.get("ingredient_keys") or set()
        if not keys or not ingredient_keys:
            continue
        intersection = ingredient_keys & keys
        if not intersection:
            continue
        score = (2 * len(intersection)) / (len(ingredient_keys) + len(keys))
        if score > best_score:
            best_score = score
            best = recipe
    if best is None:
        return None
    return {**best, "score": best_score}
