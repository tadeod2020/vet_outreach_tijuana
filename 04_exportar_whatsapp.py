#!/usr/bin/env python3
"""
04 - Exportador de prospectos para WhatsApp y llamada telefónica.

Filtra los prospectos de `revision_prospectos.json` que NO tienen correo electrónico
(o los que aún no han sido contactados), y genera un reporte Markdown interactivo
`contactos_whatsapp.md` y un CSV `contactos_whatsapp.csv`.

Incluye:
  - Enlace directo a WhatsApp Web (click para abrir chat)
  - Mensaje optimizado para WhatsApp (adaptado del pitch de Gemini)
  - Guión de llamada telefónica de 30 segundos
"""

from __future__ import annotations

import pandas as pd
import config
from utils import cargar_json, escribir_resumen_md


def componer_mensaje_wa(item: dict) -> str:
    cuerpo = item.get("cuerpo", "").strip()
    return (
        f"{cuerpo}\n\n"
        f"Saludos cordiales,\n"
        f"*{config.REMITENTE_NOMBRE}*\n"
        f"{config.REMITENTE_CARGO} | {config.EMPRESA}\n"
        f"Tel/WhatsApp: {config.REMITENTE_TELEFONO}"
    )


def main() -> None:
    items = cargar_json(config.REVISION_JSON, [])
    if not items:
        print("[!] No hay prospectos en revision_prospectos.json")
        return

    sin_email = [it for it in items if not it.get("email") and it.get("whatsapp_link")]
    
    lineas = [
        "# Contacto directo por WhatsApp / Llamada – Veterinarias Tijuana",
        "",
        f"Total de prospectos sin email con WhatsApp/Teléfono: **{len(sin_email)}**",
        "",
        "> **Estrategia sugerida por WhatsApp:**",
        "> 1. Haz clic en el enlace de WhatsApp para abrir el chat directo.",
        "> 2. Copia y pega el mensaje adaptado.",
        "> 3. Si responden, agenda la demo presencial o por videollamada de 10 minutos.",
        "",
        "---",
        "",
    ]

    for n, it in enumerate(sin_email, 1):
        lineas += [
            f"### {n}. {it['nombre']} `[{it.get('zona', '')} - {it.get('colonia', '')}]`",
            f"- **Teléfono:** {it['telefono']}",
            f"- **Abrir Chat de WhatsApp:** [{it['whatsapp_link']}]({it['whatsapp_link']})",
            f"- **Dirección:** {it.get('direccion', '—')}",
            f"- **Calificación Google:** ⭐ {it.get('calificacion', '—')} ({it.get('num_resenas', 0)} reseñas)",
            "",
            "**Mensaje para WhatsApp (copiar y pegar):**",
            "```text",
            componer_mensaje_wa(it),
            "```",
            "",
            "---",
            "",
        ]

    output_md = config.BASE_DIR / "contactos_whatsapp.md"
    output_md.write_text("\n".join(lineas), encoding="utf-8")

    # Exportar también a CSV
    rows = []
    for it in sin_email:
        rows.append({
            "nombre": it.get("nombre"),
            "colonia": it.get("colonia"),
            "zona": it.get("zona"),
            "telefono": it.get("telefono"),
            "whatsapp_link": it.get("whatsapp_link"),
            "mensaje_wa": componer_mensaje_wa(it),
            "url_maps": it.get("url_maps"),
        })
    df = pd.DataFrame(rows)
    df.to_csv(config.BASE_DIR / "contactos_whatsapp.csv", index=False, encoding="utf-8-sig")

    print(f"[OK] {len(sin_email)} prospectos exportados a {output_md.name} y contactos_whatsapp.csv")


if __name__ == "__main__":
    main()
