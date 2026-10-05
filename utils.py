"""Utilidades compartidas entre los scripts del pipeline."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import config

EMAIL_VALIDO = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}$")

ESTADOS = ("pendiente", "aprobado", "descartado", "borrador_creado")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def cargar_json(ruta: Path, por_defecto: Any = None) -> Any:
    if not ruta.exists():
        return [] if por_defecto is None else por_defecto
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[ERROR] {ruta.name} no es un JSON válido: {exc}") from exc


def guardar_json(ruta: Path, data: Any) -> None:
    """Escritura atómica: evita archivos corruptos si el proceso se interrumpe."""
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ruta)


def ahora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Texto / correo
# ---------------------------------------------------------------------------
def contar_palabras(texto: str) -> int:
    return len(texto.split())


def componer_correo(item: dict) -> str:
    """Cuerpo final del correo = cuerpo generado + despedida + firma + opt-out.

    Se compone al vuelo para que, si editas `cuerpo` a mano en
    revision_prospectos.json, el borrador refleje tus cambios.
    """
    partes = [item.get("cuerpo", "").strip(), "Saludos cordiales,\n" + config.firma()]
    if config.LINEA_OPT_OUT:
        partes.append(config.LINEA_OPT_OUT)
    return "\n\n".join(p for p in partes if p)


# ---------------------------------------------------------------------------
# Resumen Markdown para revisión humana
# ---------------------------------------------------------------------------
def _md(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    return texto.replace("|", "\\|").replace("\n", "<br>").strip()


def escribir_resumen_md(items: list[dict], ruta: Path = config.RESUMEN_MD) -> None:
    total = len(items)
    con_email = sum(1 for i in items if i.get("email"))
    por_estado = {e: sum(1 for i in items if i.get("estado") == e) for e in ESTADOS}
    con_alertas = sum(1 for i in items if i.get("advertencias"))

    lineas: list[str] = [
        "# Revisión de prospectos – Veterinarias Tijuana",
        "",
        f"_Actualizado: {ahora_iso()}_",
        "",
        f"- **Total:** {total}  |  **Con email:** {con_email}  |  **Con advertencias:** {con_alertas}",
        "- **Estados:** " + "  |  ".join(f"{k}: {v}" for k, v in por_estado.items()),
        "",
        "> Para editar o descartar un prospecto, modifica `revision_prospectos.json`:",
        "> cambia `asunto` / `cuerpo`, o pon `\"estado\": \"descartado\"` (o `\"aprobado\"`).",
        "> Luego ejecuta `python 02_generador_pitches.py --solo-resumen` para refrescar este archivo.",
        "",
        "## Tabla resumen",
        "",
        "| # | Estado | Clínica | Colonia | ⭐ | Reseñas | Email | Teléfono | Asunto | Palabras | Alertas |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n, it in enumerate(items, 1):
        lineas.append(
            "| "
            + " | ".join(
                _md(x)
                for x in (
                    n,
                    it.get("estado", ""),
                    it.get("nombre", ""),
                    it.get("colonia", ""),
                    it.get("calificacion", ""),
                    it.get("num_resenas", ""),
                    it.get("email", "") or "—",
                    it.get("telefono", "") or "—",
                    it.get("asunto", ""),
                    it.get("palabras", ""),
                    "; ".join(it.get("advertencias", [])) or "",
                )
            )
            + " |"
        )

    lineas += ["", "## Borradores completos", ""]
    for n, it in enumerate(items, 1):
        lineas += [
            f"### {n}. {it.get('nombre', '')}  `[{it.get('estado', '')}]`",
            "",
            f"- **Para:** {it.get('email') or '— sin email (contactar por teléfono/WhatsApp)'}",
            f"- **Teléfono:** {it.get('telefono') or '—'}"
            + (f"  ·  [WhatsApp]({it['whatsapp_link']})" if it.get("whatsapp_link") else ""),
            f"- **Dirección:** {it.get('direccion') or '—'}",
            f"- **Web/FB:** {it.get('sitio_web') or it.get('facebook') or '—'}",
            f"- **Maps:** {it.get('url_maps') or '—'}",
        ]
        if it.get("advertencias"):
            lineas.append(f"- ⚠️ **Advertencias:** {'; '.join(it['advertencias'])}")
        lineas += [
            "",
            f"**Asunto:** {it.get('asunto', '')}",
            "",
            "```text",
            componer_correo(it),
            "```",
            "",
        ]

    ruta.write_text("\n".join(lineas), encoding="utf-8")
