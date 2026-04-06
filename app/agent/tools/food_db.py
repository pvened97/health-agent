"""FatSecret API integration — поиск продуктов и их БЖУ."""

import hashlib
import hmac
import logging
import time
import urllib.parse
import uuid as _uuid
from typing import Optional

import httpx
from agents import function_tool

from app.agent.tools._context import get_user_id
from app.config import settings

logger = logging.getLogger(__name__)

FATSECRET_API_URL = "https://platform.api.fatsecret.com/rest/server.api"


def _oauth_sign(method: str, url: str, params: dict, consumer_secret: str) -> str:
    """OAuth 1.0 HMAC-SHA1 signature (consumer-only, no token)."""
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(params.items())
    )
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(sorted_params, safe=""),
    ])
    signing_key = f"{urllib.parse.quote(consumer_secret, safe='')}&"
    sig = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1)
    import base64
    return base64.b64encode(sig.digest()).decode()


async def _fatsecret_search(query: str, max_results: int = 3) -> list[dict]:
    """Ищет продукты в FatSecret API. Возвращает список с БЖУ на 100г."""
    if not settings.fatsecret_consumer_key or not settings.fatsecret_consumer_secret:
        return []

    params = {
        "method": "foods.search",
        "search_expression": query,
        "format": "json",
        "max_results": str(max_results),
        "oauth_consumer_key": settings.fatsecret_consumer_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_nonce": _uuid.uuid4().hex,
        "oauth_version": "1.0",
    }

    params["oauth_signature"] = _oauth_sign(
        "GET", FATSECRET_API_URL, params, settings.fatsecret_consumer_secret,
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(FATSECRET_API_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("FatSecret API error")
        return []

    foods = data.get("foods", {}).get("food", [])
    if isinstance(foods, dict):
        foods = [foods]

    results = []
    for f in foods:
        desc = f.get("food_description", "")
        parsed = _parse_fatsecret_description(desc)
        results.append({
            "name": f.get("food_name", ""),
            "brand": f.get("brand_name", ""),
            **parsed,
        })

    return results


def _parse_fatsecret_description(desc: str) -> dict:
    """Парсит строку FatSecret вида 'Per 100g - Calories: 250kcal | Fat: 10g | Carbs: 30g | Protein: 15g'."""
    result = {
        "serving": "",
        "calories_per_serving": None,
        "fat_per_serving": None,
        "carbs_per_serving": None,
        "protein_per_serving": None,
    }
    if not desc:
        return result

    parts = desc.split(" - ", 1)
    if len(parts) == 2:
        result["serving"] = parts[0].strip()
        nutrients = parts[1]
    else:
        nutrients = desc

    for part in nutrients.split("|"):
        part = part.strip().lower()
        try:
            if part.startswith("calories:"):
                result["calories_per_serving"] = float(part.split(":")[1].replace("kcal", "").strip())
            elif part.startswith("fat:"):
                result["fat_per_serving"] = float(part.split(":")[1].replace("g", "").strip())
            elif part.startswith("carbs:"):
                result["carbs_per_serving"] = float(part.split(":")[1].replace("g", "").strip())
            elif part.startswith("protein:"):
                result["protein_per_serving"] = float(part.split(":")[1].replace("g", "").strip())
        except (ValueError, IndexError):
            continue

    return result


@function_tool
async def lookup_food_nutrition(query: str) -> str:
    """Ищет продукт в базе данных FatSecret и возвращает калории и БЖУ.
    Вызывай ПЕРЕД оценкой нутриентов для каждого продукта — чтобы взять точные данные из справочника.

    Args:
        query: Название продукта на русском или английском (например «куриная грудка», «овсянка», «banana»)
    """
    results = await _fatsecret_search(query, max_results=3)

    if not results:
        return f"Продукт «{query}» не найден в справочнике. Оцени БЖУ самостоятельно."

    lines = [f"Найдено в справочнике для «{query}»:"]
    for i, r in enumerate(results, 1):
        name = r["name"]
        if r.get("brand"):
            name += f" ({r['brand']})"
        serving = r.get("serving", "")
        cal = r.get("calories_per_serving")
        prot = r.get("protein_per_serving")
        fat = r.get("fat_per_serving")
        carbs = r.get("carbs_per_serving")

        parts = []
        if cal is not None:
            parts.append(f"{cal:.0f} ккал")
        if prot is not None:
            parts.append(f"Б {prot:.1f}г")
        if fat is not None:
            parts.append(f"Ж {fat:.1f}г")
        if carbs is not None:
            parts.append(f"У {carbs:.1f}г")

        line = f"  {i}. {name}"
        if serving:
            line += f" [{serving}]"
        if parts:
            line += f" — {', '.join(parts)}"
        lines.append(line)

    lines.append("")
    lines.append("Используй эти данные для расчёта. Умножь на вес порции если нужно.")

    return "\n".join(lines)


@function_tool
async def lookup_barcode(barcode: str) -> str:
    """Ищет продукт по штрихкоду (EAN-13/EAN-8) в базе Open Food Facts.
    Возвращает название, бренд, калории и БЖУ на 100г и на порцию.
    Вызывай когда на фото виден штрихкод или пользователь прислал числовой код продукта.

    Args:
        barcode: Числовой штрихкод продукта (8 или 13 цифр, например «4610169567113»)
    """
    barcode = barcode.strip()
    if not barcode.isdigit() or len(barcode) not in (8, 13):
        return f"Некорректный штрихкод: «{barcode}». Ожидается 8 или 13 цифр."

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://world.openfoodfacts.org/api/v2/product/{barcode}"
                "?fields=product_name,brands,quantity,nutriments",
                headers={"User-Agent": "HealthAgent/1.0"},
                timeout=10,
            )
            data = resp.json()
    except Exception:
        logger.exception("Open Food Facts API error for barcode %s", barcode)
        return f"Ошибка при запросе по штрихкоду {barcode}. Попробуй оценить вручную."

    if data.get("status") != 1:
        return f"Продукт со штрихкодом {barcode} не найден в базе. Оцени БЖУ самостоятельно."

    product = data.get("product", {})
    name = product.get("product_name", "Без названия")
    brand = product.get("brands", "")
    quantity = product.get("quantity", "")
    n = product.get("nutriments", {})

    cal_100 = n.get("energy-kcal_100g")
    prot_100 = n.get("proteins_100g")
    fat_100 = n.get("fat_100g")
    carbs_100 = n.get("carbohydrates_100g")
    fiber_100 = n.get("fiber_100g")

    cal_srv = n.get("energy-kcal_serving")
    prot_srv = n.get("proteins_serving")
    fat_srv = n.get("fat_serving")
    carbs_srv = n.get("carbohydrates_serving")

    lines = [f"Найден по штрихкоду {barcode}:"]
    title = name
    if brand:
        title += f" ({brand})"
    if quantity:
        title += f", {quantity}"
    lines.append(f"  {title}")

    if cal_100 is not None:
        parts = [f"{cal_100:.0f} ккал"]
        if prot_100 is not None:
            parts.append(f"Б {prot_100:.1f}г")
        if fat_100 is not None:
            parts.append(f"Ж {fat_100:.1f}г")
        if carbs_100 is not None:
            parts.append(f"У {carbs_100:.1f}г")
        if fiber_100 is not None:
            parts.append(f"клетчатка {fiber_100:.1f}г")
        lines.append(f"  На 100г: {', '.join(parts)}")

    if cal_srv is not None:
        parts_srv = [f"{cal_srv:.0f} ккал"]
        if prot_srv is not None:
            parts_srv.append(f"Б {prot_srv:.1f}г")
        if fat_srv is not None:
            parts_srv.append(f"Ж {fat_srv:.1f}г")
        if carbs_srv is not None:
            parts_srv.append(f"У {carbs_srv:.1f}г")
        lines.append(f"  На порцию: {', '.join(parts_srv)}")

    lines.append("")
    lines.append("Используй данные из базы. Умножь на вес порции если пользователь указал.")

    return "\n".join(lines)
