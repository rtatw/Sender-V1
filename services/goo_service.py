import logging
from services.http_client import get_httpx

logger = logging.getLogger(__name__)
GOO_API_BASE = "https://api.goo.network"

GOO_SERVICES_PARSE = {
    "ebay_de": "eBay DE", "vinted_de": "Vinted DE", "depop_de": "Depop DE",
    "shpock_de": "Shpock DE", "etsy_de": "Etsy DE", "quoka_de": "Quoka DE",
    "willhaben_at": "Willhaben AT", "kleinanzeigen_at": "Kleinanzeigen AT",
    "vinted_at": "Vinted AT", "olx_pl": "OLX PL", "vinted_pl": "Vinted PL",
    "allegrolokalnie_pl": "Allegro Lokalnie PL", "tutti_ch": "Tutti CH",
    "anibis_ch": "Anibis CH", "ricardo_ch": "Ricardo CH",
    "blocket_se": "Blocket SE", "vinted_se": "Vinted SE",
    "finn_no": "Finn NO", "tise_no": "Tise NO",
    "dba_dk": "DBA DK", "vinted_dk": "Vinted DK",
    "vinted_be": "Vinted BE", "2dehands_be": "2dehands BE",
    "etsy_world": "Etsy (World)", "etsy_verify_world": "Etsy Verify (World)",
}

GOO_SERVICES_NO_PARSE_ONLY = {
    "dhl_de": "DHL DE", "gls_de": "GLS DE", "dpd_de": "DPD DE",
    "fedex_de": "FedEx DE", "hermes_de": "Hermes DE", "ups_de": "UPS DE",
    "post_at": "Post AT", "dhl_at": "DHL AT", "dpd_at": "DPD AT",
    "inpost_pl": "InPost PL", "poczta_pl": "Poczta PL", "dpd_pl": "DPD PL",
    "post_ch": "Post CH", "dhl_ch": "DHL CH",
    "postnord_se": "PostNord SE", "dpd_se": "DPD SE",
    "posten_no": "Posten NO",
    "postnord_dk": "PostNord DK", "dpd_dk": "DPD DK",
    "bpost_be": "BPost BE", "dpd_be": "DPD BE",
}

GOO_SERVICES = {**GOO_SERVICES_PARSE, **GOO_SERVICES_NO_PARSE_ONLY}


def _make_headers(team_key: str, user_key: str = "") -> dict:
    auth_key = user_key if user_key else team_key
    return {
        "Authorization": f"Apikey {auth_key}",
        "Host": "api.goo.network",
        "X-Team-Key": team_key,
        "Content-Type": "application/json",
    }


async def generate_link_with_parser(team_key, service, item_url, profile_id, user_key="", balance_checker=False):
    if "kleinanzeigen.de" in (item_url or ""):
        service = "ebay_de"
    if service not in GOO_SERVICES_PARSE:
        return False, f"Сервис '{service}' не поддерживает парсер."
    payload = {"service": service, "url": item_url, "isNeedBalanceChecker": balance_checker, "profileID": profile_id}
    try:
        client = await get_httpx()
        resp = await client.post(f"{GOO_API_BASE}/api/generate/single/parse", headers=_make_headers(team_key, user_key), json=payload)
        text = resp.text
        try:
            data = resp.json()
            if data.get("status") is True: return True, data["message"]
            return False, data.get("message", f"HTTP {resp.status_code}: {text[:300]}")
        except Exception:
            return False, f"HTTP {resp.status_code} (not JSON): {text[:300]}"
    except Exception as e: logger.error("GOO parse error: %s", e); return False, str(e)[:200]


async def generate_link_no_parser(team_key, service, name, price, image, profile_id, user_key="", balance_checker=False):
    payload = {"service": service, "name": name, "price": int(price), "image": image, "profileID": profile_id, "isNeedBalanceChecker": balance_checker}
    try:
        client = await get_httpx()
        resp = await client.post(f"{GOO_API_BASE}/api/generate/single/no-parse", headers=_make_headers(team_key, user_key), json=payload)
        data = resp.json()
        if data.get("status") is True: return True, data["message"]
        return False, data.get("message", f"HTTP {resp.status_code}")
    except Exception as e: logger.error("GOO no-parse error: %s", e); return False, str(e)[:200]


async def generate_link_auto(team_key, service, profile_id, item_url=None, name=None, price=None, image=None, balance_checker=False):
    if item_url and service in GOO_SERVICES_PARSE:
        return await generate_link_with_parser(team_key, service, item_url, profile_id, balance_checker)
    elif name and price is not None and image:
        return await generate_link_no_parser(team_key, service, name, price, image, profile_id, balance_checker)
    elif item_url and service not in GOO_SERVICES_PARSE:
        return False, f"Сервис не поддерживает парсер. Укажи название, цену и фото вручную."
    else:
        return False, "Нет данных: нужен url или (название + цена + фото)"


async def generate_link(team_key, user_key="", offer_key="", name="", price=""):
    if not team_key:
        return False, "Ключ команды не указан."
    payload = {"offer_key": offer_key}
    if name: payload["name"] = name
    if price: payload["price"] = str(price)
    headers = _make_headers(team_key, user_key)
    try:
        client = await get_httpx()
        resp = await client.post(f"{GOO_API_BASE}/api/generate/single/offer", headers=headers, json=payload)
        data = resp.json()
        if data.get("success") is True: return True, data["redirect_url"]
        return False, data.get("message", f"HTTP {resp.status_code}")
    except Exception as e: logger.error("GOO offer error: %s", e); return False, str(e)[:200]
