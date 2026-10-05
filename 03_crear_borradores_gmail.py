#!/usr/bin/env python3
"""
03 - Crea BORRADORES en Gmail (nunca envía nada).

Lee `revision_prospectos.json` y, para cada prospecto con email que no esté
"descartado" ni ya procesado, inserta un mensaje en la carpeta Borradores de tu
cuenta usando la API oficial de Gmail (OAuth local, scope gmail.compose).

- Límite por tanda configurable (por defecto 10, tope duro 15).
- Marca cada prospecto como "borrador_creado" (con draft_id) para no duplicar.
- Re-ejecutable: cada corrida toma la siguiente tanda.

Uso:
  python 03_crear_borradores_gmail.py --dry-run          # vista previa, sin tocar Gmail
  python 03_crear_borradores_gmail.py                    # crea hasta 10 borradores
  python 03_crear_borradores_gmail.py --limite 15
  python 03_crear_borradores_gmail.py --solo-aprobados   # solo estado "aprobado"
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr

import config
from utils import EMAIL_VALIDO, ahora_iso, cargar_json, componer_correo, escribir_resumen_md, guardar_json


def obtener_servicio():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if config.TOKEN_JSON.exists():
        creds = Credentials.from_authorized_user_file(str(config.TOKEN_JSON), config.GMAIL_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            print("[i] El token expiró o fue revocado; se pedirá autorización de nuevo.")
            creds = None

    if not creds or not creds.valid:
        if not config.CREDENTIALS_JSON.exists():
            sys.exit(
                f"[ERROR] No se encontró {config.CREDENTIALS_JSON}.\n"
                "        Descárgalo de Google Cloud Console (OAuth client ID tipo 'Desktop app'). Ver README."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(config.CREDENTIALS_JSON), config.GMAIL_SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        config.TOKEN_JSON.write_text(creds.to_json(), encoding="utf-8")
        config.TOKEN_JSON.chmod(0o600)

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def construir_mensaje(item: dict, remitente: str) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = item["email"]
    if remitente:
        msg["From"] = formataddr((config.REMITENTE_NOMBRE, remitente))
    msg["Subject"] = item["asunto"]
    msg.set_content(componer_correo(item))
    return msg


def seleccionar(items: list[dict], solo_aprobados: bool) -> tuple[list[dict], dict[str, int]]:
    permitidos = {"aprobado"} if solo_aprobados else {"pendiente", "aprobado"}
    motivos = {"sin_email": 0, "descartado": 0, "ya_creado": 0, "sin_texto": 0, "otro_estado": 0}
    elegidos: list[dict] = []
    for it in items:
        estado = it.get("estado", "pendiente")
        if estado == "borrador_creado":
            motivos["ya_creado"] += 1
        elif estado == "descartado":
            motivos["descartado"] += 1
        elif estado not in permitidos:
            motivos["otro_estado"] += 1
        elif not EMAIL_VALIDO.match(it.get("email", "") or ""):
            motivos["sin_email"] += 1
        elif not it.get("asunto") or not it.get("cuerpo"):
            motivos["sin_texto"] += 1
        else:
            elegidos.append(it)
    return elegidos, motivos


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crea borradores en Gmail (no envía).")
    p.add_argument("--limite", type=int, default=config.LIMITE_BORRADORES,
                   help=f"Borradores por tanda (máx. {config.LIMITE_BORRADORES_MAX}).")
    p.add_argument("--solo-aprobados", action="store_true", help="Procesar solo estado 'aprobado'.")
    p.add_argument("--dry-run", action="store_true", help="Mostrar lo que se crearía, sin llamar a Gmail.")
    p.add_argument("--from", dest="remitente", default="",
                   help="Dirección From opcional (debe ser tu cuenta o un alias verificado).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    limite = max(1, min(args.limite, config.LIMITE_BORRADORES_MAX))
    if args.limite > config.LIMITE_BORRADORES_MAX:
        print(f"[!] Límite ajustado a {config.LIMITE_BORRADORES_MAX} (tope de seguridad por tanda).")

    items: list[dict] = cargar_json(config.REVISION_JSON, [])
    if not items:
        sys.exit(f"[ERROR] {config.REVISION_JSON.name} no existe o está vacío. Ejecuta 02_generador_pitches.py")

    elegibles, motivos = seleccionar(items, args.solo_aprobados)
    tanda = elegibles[:limite]
    print(
        f"[i] Elegibles: {len(elegibles)} | esta tanda: {len(tanda)} (límite {limite})\n"
        f"    Omitidos -> ya creados: {motivos['ya_creado']}, descartados: {motivos['descartado']}, "
        f"sin email: {motivos['sin_email']}, sin texto: {motivos['sin_texto']}, "
        f"otro estado: {motivos['otro_estado']}"
    )
    if not tanda:
        print("[OK] No hay borradores por crear.")
        return

    if args.dry_run:
        for n, it in enumerate(tanda, 1):
            print(f"\n{'=' * 70}\n({n}) Para: {it['email']}\nAsunto: {it['asunto']}\n{'-' * 70}")
            print(componer_correo(it))
        print(f"\n[dry-run] Se crearían {len(tanda)} borradores. No se tocó Gmail.")
        return

    servicio = obtener_servicio()
    creados = 0
    for n, it in enumerate(tanda, 1):
        try:
            msg = construir_mensaje(it, args.remitente)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            draft = (
                servicio.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": raw}})
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - reportar y seguir con el resto
            print(f"  ({n}/{len(tanda)}) [!] {it.get('nombre')}: {exc}")
            continue

        it["estado"] = "borrador_creado"
        it["draft_id"] = draft.get("id", "")
        it["borrador_creado_en"] = ahora_iso()
        guardar_json(config.REVISION_JSON, items)  # persistir de inmediato: evita duplicados
        creados += 1
        print(f"  ({n}/{len(tanda)}) ✅ Borrador creado -> {it['email']}  ({it['nombre'][:40]})")
        time.sleep(1.0)

    escribir_resumen_md(items)
    restantes = len(elegibles) - creados
    print(
        f"\n[OK] {creados} borradores creados en Gmail (carpeta Borradores). Nada fue enviado.\n"
        f"     Pendientes para próximas tandas: {restantes}\n"
        "     Abre https://mail.google.com/mail/u/0/#drafts para revisarlos y enviarlos manualmente."
    )


if __name__ == "__main__":
    main()
