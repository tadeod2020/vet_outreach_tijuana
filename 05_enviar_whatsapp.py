#!/usr/bin/env python3
"""
05 - Verificador y Enviador automático de mensajes por WhatsApp Web (Playwright).

Flujo:
  1. Abre Chromium con persistencia de sesión (`.wweb_session/`).
  2. Si es la primera vez, muestra el navegador para que escanees el código QR de tu WhatsApp.
  3. Lee `revision_prospectos.json` y toma los prospectos sin email (o no enviados).
  4. Abre `https://web.whatsapp.com/send?phone=52...&text=...` para cada número.
  5. Si el número NO tiene WhatsApp:
     - Detecta la alerta de WhatsApp Web, marca `tiene_whatsapp: False` y continúa.
  6. Si SÍ tiene WhatsApp:
     - Presiona Enviar (o Enter), marca `wa_enviado: True` y espera un intervalo configurable
       (por defecto 20-35 segundos) para proteger la reputación de tu cuenta.

Uso:
  python 05_enviar_whatsapp.py --limite 5             # envía a 5 veterinarias de prueba
  python 05_enviar_whatsapp.py --solo-verificar       # solo detecta cuáles tienen WA sin enviar
  python 05_enviar_whatsapp.py --pausa 25             # segundos entre mensajes
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from urllib.parse import quote

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import config
from utils import ahora_iso, cargar_json, componer_correo, escribir_resumen_md, guardar_json

SESSION_DIR = config.BASE_DIR / ".wweb_session"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def componer_mensaje_wa(item: dict) -> str:
    cuerpo = item.get("cuerpo", "").strip()
    return (
        f"{cuerpo}\n\n"
        f"Saludos cordiales,\n"
        f"*{config.REMITENTE_NOMBRE}*\n"
        f"{config.REMITENTE_CARGO} | {config.EMPRESA}\n"
        f"Tel/WhatsApp: {config.REMITENTE_TELEFONO}"
    )


def normalizar_telefono_wa(item: dict) -> str:
    tel = item.get("telefono_normalizado") or re.sub(r"\D", "", item.get("telefono", ""))
    if not tel:
        return ""
    if len(tel) == 10:
        return f"52{tel}"
    if len(tel) == 12 and tel.startswith("52"):
        return tel
    return tel if len(tel) >= 10 else ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Envía o verifica WhatsApp Web automáticamente con Playwright.")
    p.add_argument("--limite", type=int, default=10, help="Máximo de mensajes a enviar por tanda.")
    p.add_argument("--pausa", type=float, default=25.0, help="Segundos promedio de pausa anti-spam entre envíos.")
    p.add_argument("--solo-verificar", action="store_true", help="Solo verificar si el número tiene WhatsApp, sin enviar.")
    p.add_argument("--headless", action="store_true", help="Ejecutar en segundo plano (requiere QR ya escaneado previamente).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    items: list[dict] = cargar_json(config.REVISION_JSON, [])
    if not items:
        sys.exit(f"[ERROR] {config.REVISION_JSON.name} no existe. Corre 02_generador_pitches.py primero.")

    # Filtrar candidatas sin email (o con teléfono) y que no hayan sido enviadas ni descartadas
    candidatos = [
        it for it in items
        if not it.get("email")
        and normalizar_telefono_wa(it)
        and it.get("estado") not in ("descartado", "wa_enviado")
        and it.get("tiene_whatsapp") is not False
    ]

    tanda = candidatos[: args.limite]
    print(f"[i] Total sin email elegibles: {len(candidatos)} | Procesando en esta tanda: {len(tanda)}")
    if not tanda:
        print("[OK] No hay prospectos pendientes para enviar por WhatsApp.")
        return

    print("\n[i] Abriendo navegador con sesión de WhatsApp Web...")
    print("    Si es la primera vez, escanea el código QR en la pantalla de tu navegador.\n")

    SESSION_DIR.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=args.headless,
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 850},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()

        # Abrir WhatsApp Web para asegurar login
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)
        
        print("[i] Verificando sesión de WhatsApp Web...")
        try:
            page.wait_for_selector('#pane-side, div[contenteditable="true"], canvas', timeout=30000)
            print("✅ WhatsApp Web listo.")
        except PWTimeout:
            print("[!] Cargando WhatsApp Web... Continuando con la navegación a chats.")

        enviados = 0
        sin_wa = 0

        for n, it in enumerate(tanda, 1):
            phone = normalizar_telefono_wa(it)
            mensaje = componer_mensaje_wa(it)
            nombre = it.get("nombre", "Clínica")

            url = f"https://web.whatsapp.com/send?phone={phone}&text={quote(mensaje)}"
            print(f"\n({n}/{len(tanda)}) Verificando {nombre[:40]} ({phone})...")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)

                # Verificar si sale la ventana de alerta "El número no existe en WhatsApp"
                popup = page.locator('div[role="dialog"]')
                es_invalido = False
                try:
                    if popup.count() > 0 and popup.first.is_visible(timeout=3000):
                        texto_popup = popup.first.inner_text().lower()
                        if any(k in texto_popup for k in ("inválido", "invalid", "no está en whatsapp", "isn't on whatsapp")):
                            es_invalido = True
                except Exception:
                    pass

                if es_invalido:
                    print(f"  ❌ No tiene WhatsApp registrado.")
                    it["tiene_whatsapp"] = False
                    it["wa_verificado_en"] = ahora_iso()
                    sin_wa += 1
                    # Cerrar popup
                    try:
                        page.locator('button:has-text("OK"), button:has-text("Aceptar")').first.click(timeout=2000)
                    except Exception:
                        pass
                    guardar_json(config.REVISION_JSON, items)
                    continue

                # Si tiene WA, buscar el botón de enviar o caja de texto
                it["tiene_whatsapp"] = True
                it["wa_verificado_en"] = ahora_iso()

                if args.solo_verificar:
                    print("  ✅ TIENE WhatsApp (modo --solo-verificar: no se envió).")
                    guardar_json(config.REVISION_JSON, items)
                    continue

                # Esperar a que cargue el botón de enviar o el input de chat
                send_btn = page.locator('span[data-icon="send"], button[aria-label*="Enviar"], button[aria-label*="Send"]')
                try:
                    send_btn.first.wait_for(state="visible", timeout=15000)
                    send_btn.first.click()
                    page.wait_for_timeout(2000)
                    print(f"  ✅ MENSAJE ENVIADO a {nombre}")
                    it["estado"] = "wa_enviado"
                    it["wa_enviado_en"] = ahora_iso()
                    enviados += 1
                except PWTimeout:
                    # Intento alternativo: pulsar Enter en la caja editable
                    chat_input = page.locator('div[contenteditable="true"][data-tab="10"], div[contenteditable="true"][aria-label*="Escribe"]')
                    if chat_input.count() > 0:
                        chat_input.first.press("Enter")
                        page.wait_for_timeout(2000)
                        print(f"  ✅ MENSAJE ENVIADO (Enter) a {nombre}")
                        it["estado"] = "wa_enviado"
                        it["wa_enviado_en"] = ahora_iso()
                        enviados += 1
                    else:
                        print(f"  [!] No se pudo hacer clic en Enviar para {nombre}")

                guardar_json(config.REVISION_JSON, items)

                # Pausa de seguridad aleatoria entre envíos
                if n < len(tanda) and not args.solo_verificar:
                    espera = random.uniform(args.pausa * 0.8, args.pausa * 1.2)
                    print(f"  ⏱ Pausa de seguridad: {espera:.1f} segundos...")
                    time.sleep(espera)

            except Exception as exc:
                print(f"  [!] Error procesando {nombre}: {exc}")
                continue

        context.close()

    escribir_resumen_md(items)
    print(
        f"\n[OK] Tanda finalizada.\n"
        f"     - Enviados: {enviados}\n"
        f"     - Números sin WhatsApp: {sin_wa}\n"
        f"     - Estado guardado en revision_prospectos.json"
    )


if __name__ == "__main__":
    main()
