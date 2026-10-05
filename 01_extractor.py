#!/usr/bin/env python3
"""
01 - Extractor de veterinarias en Tijuana (Google Maps + sitios web).

Flujo:
  1. Busca "veterinarias en <zona>, Tijuana" en Google Maps (Playwright headless).
  2. Hace scroll en la lista de resultados y recolecta las URLs de cada ficha.
  3. Abre cada ficha y extrae nombre, dirección, colonia, teléfono, web,
     calificación y número de reseñas.
  4. Si hay sitio web (no red social), lo visita para encontrar correos
     (mailto:, texto, Cloudflare email-protection) y enlaces de WhatsApp/Facebook.
  5. Guarda/actualiza incrementalmente `veterinarias_raw.json` (re-ejecutable:
     las clínicas ya extraídas se omiten salvo que uses --reset).

Uso:
  python 01_extractor.py
  python 01_extractor.py --zonas "Zona Río" "Otay" --max-por-zona 10
  python 01_extractor.py --headful          # ver el navegador
  python 01_extractor.py --sin-emails       # no visitar sitios web
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import config
from utils import ahora_iso, cargar_json, guardar_json

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
TIJUANA_GEO = {"latitude": 32.5149, "longitude": -117.0382}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
CFEMAIL_RE = re.compile(r'(?:data-cfemail="|/cdn-cgi/l/email-protection#)([0-9a-fA-F]{8,})')
WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp\.com/send\?phone=)\+?(\d{10,13})")
FB_RE = re.compile(r"https?://(?:www\.|m\.)?facebook\.com/[A-Za-z0-9_.\-/?=]+")
IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.\-]+")
PLACE_ID_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)")
CP_RE = re.compile(r"\b\d{5}\b")

EXT_INVALIDAS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico")
DOMINIOS_BASURA = (
    "example.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "domain.com",
    "email.com", "tudominio.com", "yourdomain.com", "dominio.com", "godaddy.com",
    "w3.org", "schema.org", "googlemail.com", "sentry.wixpress.com", "mysite.com",
)
DOMINIOS_SOCIALES = (
    "facebook.com", "fb.com", "instagram.com", "wa.me", "whatsapp.com", "tiktok.com",
    "google.com", "goo.gl", "g.page", "business.site", "twitter.com", "x.com",
)
# Perfiles genéricos que aparecen en plantillas de sitios (no son de la clínica)
PERFILES_GENERICOS = {
    "wix", "wixcom", "wordpress", "wordpressdotcom", "squarespace", "godaddy", "shopify",
    "facebook", "instagram", "meta", "tr", "sharer", "sharer.php", "plugins", "dialog", "share",
    "elementor", "hostinger", "weebly", "jimdo", "pages", "profile.php",
}
PALABRAS_CONTACTO = ("contact", "contacto", "contactanos", "contáctanos", "nosotros", "about", "ubicacion")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def pausa(a: float = 1.0, b: float = 2.5) -> None:
    time.sleep(random.uniform(a, b))


def log(msg: str) -> None:
    print(msg, flush=True)


def _attr(page: Page, selector: str, attr: str) -> str:
    loc = page.locator(selector).first
    try:
        if loc.count() == 0:
            return ""
        return (loc.get_attribute(attr, timeout=2000) or "").strip()
    except (PWTimeout, PWError):
        return ""


def _texto(page: Page, selector: str) -> str:
    loc = page.locator(selector).first
    try:
        if loc.count() == 0:
            return ""
        return (loc.inner_text(timeout=2000) or "").strip()
    except (PWTimeout, PWError):
        return ""


def limpiar_url(href: str) -> str:
    """Desenvuelve redirecciones tipo https://www.google.com/url?q=..."""
    if not href:
        return ""
    if "google.com/url" in href:
        q = parse_qs(urlparse(href).query)
        href = (q.get("q") or q.get("url") or [href])[0]
    return href.strip()


def dominio(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def es_red_social(url: str) -> bool:
    d = dominio(url)
    return any(d == s or d.endswith("." + s) for s in DOMINIOS_SOCIALES)


def place_id(url: str) -> str:
    m = PLACE_ID_RE.search(url)
    return m.group(1) if m else ""


def extraer_colonia(direccion: str) -> str:
    """Heurística para direcciones MX: 'Calle 123, Colonia, 22000 Tijuana, B.C.'"""
    if not direccion:
        return ""
    partes = [p.strip() for p in direccion.split(",") if p.strip()]
    for i, p in enumerate(partes):
        if CP_RE.search(p) and i > 0:
            return partes[i - 1]
    return partes[1] if len(partes) >= 3 else ""


def normalizar_telefono(tel: str) -> str:
    digitos = re.sub(r"\D", "", tel or "")
    if digitos.startswith("52") and len(digitos) == 12:
        digitos = digitos[2:]
    return digitos


def decodificar_cfemail(hexstr: str) -> str:
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def filtrar_emails(candidatos: set[str], sitio: str) -> list[str]:
    limpios: set[str] = set()
    for e in candidatos:
        e = unquote(e).strip().strip(".").lower()
        e = e.split("?")[0]
        if not EMAIL_RE.fullmatch(e):
            continue
        if e.endswith(EXT_INVALIDAS):
            continue
        dom = e.split("@", 1)[1]
        if any(dom == b or dom.endswith("." + b) for b in DOMINIOS_BASURA):
            continue
        limpios.add(e)
    dom_sitio = dominio(sitio)

    def prioridad(e: str) -> tuple[int, str]:
        d = e.split("@", 1)[1]
        if dom_sitio and (d == dom_sitio or dom_sitio.endswith(d)):
            return (0, e)
        if any(k in e for k in ("contacto", "info", "citas", "recepcion", "hola")):
            return (1, e)
        return (2, e)

    return sorted(limpios, key=prioridad)


# ---------------------------------------------------------------------------
# Google Maps
# ---------------------------------------------------------------------------
def aceptar_consentimiento(page: Page) -> None:
    for texto in ("Aceptar todo", "Accept all", "Acepto"):
        try:
            btn = page.get_by_role("button", name=texto)
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded")
                return
        except (PWTimeout, PWError):
            continue


def buscar_zona(page: Page, zona: str, max_resultados: int) -> list[str]:
    consulta = f"{config.TERMINO_BUSQUEDA} en {zona}, {config.CIUDAD}"
    url = f"https://www.google.com/maps/search/{quote_plus(consulta)}?hl=es"
    log(f"\n[Maps] Buscando: {consulta}")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    aceptar_consentimiento(page)

    try:
        page.wait_for_selector('div[role="feed"], h1.DUwDvf', timeout=20000)
    except PWTimeout:
        log("  [!] No cargaron resultados (¿captcha o cambio de DOM?). Prueba con --headful.")
        return []

    # Caso: Maps redirige directo a una sola ficha
    if page.locator('div[role="feed"]').count() == 0:
        return [page.url] if "/maps/place/" in page.url else []

    feed_sel = 'div[role="feed"]'
    link_sel = f'{feed_sel} a.hfpxzc, {feed_sel} a[href*="/maps/place/"]'
    urls: list[str] = []
    sin_cambios = 0

    while len(urls) < max_resultados and sin_cambios < 4:
        hrefs = page.eval_on_selector_all(link_sel, "els => els.map(e => e.href)")
        nuevos = [h for h in hrefs if h and h not in urls]
        urls.extend(nuevos)
        sin_cambios = 0 if nuevos else sin_cambios + 1

        fin = page.locator("text=/Llegaste al final de la lista|You've reached the end of the list/")
        if fin.count() > 0:
            break
        page.evaluate(
            "(sel) => { const f = document.querySelector(sel); if (f) f.scrollBy(0, f.scrollHeight); }",
            feed_sel,
        )
        pausa(1.5, 2.8)

    urls = urls[:max_resultados]
    log(f"  -> {len(urls)} fichas encontradas")
    return urls


def extraer_ficha(page: Page, url: str) -> dict | None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector("h1.DUwDvf, h1", timeout=20000)
    except PWTimeout:
        return None
    page.wait_for_timeout(1500)  # deja que carguen los botones de info

    nombre = _texto(page, "h1.DUwDvf") or _texto(page, "h1")
    if not nombre:
        return None

    categoria = _texto(page, "button.DkEaL")

    # Calificación y reseñas (texto tipo "4.7\n(235)" o "4,7(1.234)")
    bloque = _texto(page, "div.F7nice")
    calificacion = None
    num_resenas = None
    m = re.search(r"(\d[.,]\d)", bloque)
    if m:
        calificacion = float(m.group(1).replace(",", "."))
    m = re.search(r"\(([\d.,\s]+)\)", bloque)
    if m:
        num_resenas = int(re.sub(r"\D", "", m.group(1)) or 0)
    if calificacion is None:
        lbl = _attr(page, 'span[role="img"][aria-label*="estrella"]', "aria-label")
        m = re.search(r"(\d[.,]\d)", lbl)
        if m:
            calificacion = float(m.group(1).replace(",", "."))
    if num_resenas is None:
        lbl = _attr(page, 'span[aria-label*="reseña"]', "aria-label")
        digitos = re.sub(r"\D", "", lbl)
        num_resenas = int(digitos) if digitos else None

    direccion = _attr(page, 'button[data-item-id="address"]', "aria-label")
    direccion = re.sub(r"^(Dirección|Address):\s*", "", direccion).strip()

    telefono = _attr(page, 'button[data-item-id^="phone:tel:"]', "aria-label")
    telefono = re.sub(r"^(Teléfono|Phone):\s*", "", telefono).strip()
    if not telefono:
        data_id = _attr(page, 'button[data-item-id^="phone:tel:"]', "data-item-id")
        telefono = data_id.replace("phone:tel:", "")

    sitio = limpiar_url(_attr(page, 'a[data-item-id="authority"]', "href"))

    facebook = sitio if "facebook.com" in dominio(sitio) else ""
    instagram = sitio if "instagram.com" in dominio(sitio) else ""

    tel_norm = normalizar_telefono(telefono)
    return {
        "id": place_id(url) or f"{nombre}|{direccion}".lower(),
        "nombre": nombre,
        "categoria": categoria,
        "direccion": direccion,
        "colonia": extraer_colonia(direccion),
        "telefono": telefono,
        "telefono_normalizado": tel_norm,
        "whatsapp": "",
        "sitio_web": sitio,
        "facebook": facebook,
        "instagram": instagram,
        "email": "",
        "emails_encontrados": [],
        "email_fuente": "",
        "calificacion": calificacion,
        "num_resenas": num_resenas,
        "url_maps": url.split("?")[0] if "/maps/place/" in url else url,
    }


# ---------------------------------------------------------------------------
# Sitios web -> emails / WhatsApp / redes
# ---------------------------------------------------------------------------
def _es_perfil_generico(url: str) -> bool:
    ruta = urlparse(url).path.strip("/").split("/")
    primero = (ruta[0] if ruta else "").lower()
    return not primero or primero in PERFILES_GENERICOS


def _analizar_html(html: str, page: Page) -> tuple[set[str], set[str], set[str], set[str]]:
    emails: set[str] = set(EMAIL_RE.findall(html))
    try:
        mailtos = page.eval_on_selector_all(
            'a[href^="mailto:"]', "els => els.map(e => e.getAttribute('href'))"
        )
        emails.update(m.replace("mailto:", "") for m in mailtos if m)
    except PWError:
        pass
    for hexstr in CFEMAIL_RE.findall(html):
        dec = decodificar_cfemail(hexstr)
        if dec:
            emails.add(dec)
    was = set(WA_RE.findall(html))
    fbs = {u.rstrip("/") for u in FB_RE.findall(html) if not _es_perfil_generico(u)}
    igs = {u.rstrip("/") for u in IG_RE.findall(html) if not _es_perfil_generico(u)}
    return emails, was, fbs, igs


def enriquecer_desde_web(ctx: BrowserContext, ficha: dict) -> None:
    sitio = ficha.get("sitio_web", "")
    if not sitio or es_red_social(sitio):
        return

    page = ctx.new_page()
    emails: set[str] = set()
    was: set[str] = set()
    fbs: set[str] = set()
    igs: set[str] = set()
    try:
        page.goto(sitio, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(1500)
        e, w, f, i = _analizar_html(page.content(), page)
        emails |= e; was |= w; fbs |= f; igs |= i

        if not filtrar_emails(emails, sitio):
            base = dominio(page.url)
            enlaces = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => [e.href, (e.innerText || '').toLowerCase()])"
            )
            candidatos: list[str] = []
            for href, texto in enlaces:
                if not href or dominio(href) != base or href in candidatos:
                    continue
                if any(k in href.lower() or k in texto for k in PALABRAS_CONTACTO):
                    candidatos.append(href)
            for href in candidatos[:3]:
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1000)
                    e, w, f, i = _analizar_html(page.content(), page)
                    emails |= e; was |= w; fbs |= f; igs |= i
                    if filtrar_emails(emails, sitio):
                        break
                except (PWTimeout, PWError):
                    continue
    except (PWTimeout, PWError) as exc:
        log(f"    [web] No se pudo abrir {sitio}: {str(exc).splitlines()[0][:90]}")
    finally:
        page.close()

    lista = filtrar_emails(emails, sitio)
    if lista:
        ficha["email"] = lista[0]
        ficha["emails_encontrados"] = lista
        ficha["email_fuente"] = "sitio_web"
    if was and not ficha.get("whatsapp"):
        ficha["whatsapp"] = sorted(was)[0]
    if fbs and not ficha.get("facebook"):
        ficha["facebook"] = sorted(fbs, key=len)[0]
    if igs and not ficha.get("instagram"):
        ficha["instagram"] = sorted(igs, key=len)[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def crear_contexto(browser: Browser, bloquear_recursos: bool = False) -> BrowserContext:
    ctx = browser.new_context(
        locale="es-MX",
        timezone_id="America/Tijuana",
        geolocation=TIJUANA_GEO,
        permissions=["geolocation"],
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
    )
    if bloquear_recursos:
        ctx.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )
    return ctx


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extrae veterinarias de Tijuana desde Google Maps.")
    p.add_argument("--zonas", nargs="+", default=config.ZONAS, help="Zonas a buscar.")
    p.add_argument("--max-por-zona", type=int, default=config.MAX_RESULTADOS_POR_ZONA)
    p.add_argument("--headful", action="store_true", help="Mostrar el navegador (debug).")
    p.add_argument("--sin-emails", action="store_true", help="No visitar sitios web.")
    p.add_argument("--reset", action="store_true", help="Ignorar el JSON existente y empezar de cero.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    salida = config.RAW_JSON

    datos: list[dict] = [] if args.reset else cargar_json(salida, [])
    ids_existentes = {d["id"] for d in datos}
    log(f"[i] Registros previos: {len(datos)}  ->  {salida.name}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not args.headful,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_maps = crear_contexto(browser)
        ctx_web = crear_contexto(browser, bloquear_recursos=True)
        page = ctx_maps.new_page()

        try:
            for zona in args.zonas:
                try:
                    urls = buscar_zona(page, zona, args.max_por_zona)
                except (PWTimeout, PWError) as exc:
                    log(f"  [!] Error buscando {zona}: {str(exc).splitlines()[0]}")
                    continue

                for n, url in enumerate(urls, 1):
                    pid = place_id(url)
                    if pid and pid in ids_existentes:
                        log(f"  ({n}/{len(urls)}) ya existe, se omite")
                        continue
                    try:
                        ficha = extraer_ficha(page, url)
                    except (PWTimeout, PWError) as exc:
                        log(f"  ({n}/{len(urls)}) [!] error en ficha: {str(exc).splitlines()[0][:90]}")
                        continue
                    if not ficha or ficha["id"] in ids_existentes:
                        continue

                    ficha["zona_busqueda"] = zona
                    if not args.sin_emails:
                        enriquecer_desde_web(ctx_web, ficha)
                    ficha["fecha_extraccion"] = ahora_iso()

                    datos.append(ficha)
                    ids_existentes.add(ficha["id"])
                    guardar_json(salida, datos)  # guardado incremental
                    log(
                        f"  ({n}/{len(urls)}) {ficha['nombre'][:45]:45} "
                        f"⭐{ficha['calificacion'] or '-'} "
                        f"📞{ficha['telefono'] or '-'} "
                        f"✉️ {ficha['email'] or '-'}"
                    )
                    pausa(1.2, 3.0)
                pausa(3, 6)
        except KeyboardInterrupt:
            log("\n[i] Interrumpido por el usuario. Lo extraído ya está guardado.")
        finally:
            browser.close()

    guardar_json(salida, datos)
    con_email = sum(1 for d in datos if d.get("email"))
    con_tel = sum(1 for d in datos if d.get("telefono"))
    log(
        f"\n[OK] {len(datos)} veterinarias en {salida.name} "
        f"| con email: {con_email} | con teléfono: {con_tel}"
    )
    if not datos:
        sys.exit(1)


if __name__ == "__main__":
    main()
