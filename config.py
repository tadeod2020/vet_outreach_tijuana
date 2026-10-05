"""
Configuración compartida del pipeline de prospección de veterinarias en Tijuana.

Los valores se leen de variables de entorno y, si existe, de un archivo `.env`
en esta misma carpeta (formato KEY=VALUE, sin dependencias externas).
Las variables ya exportadas en la terminal tienen prioridad sobre `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _cargar_env(ruta: Path) -> None:
    """Carga un .env simple (KEY=VALUE) sin sobrescribir variables existentes."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        os.environ.setdefault(clave, valor)


_cargar_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Archivos del pipeline
# ---------------------------------------------------------------------------
RAW_JSON = BASE_DIR / "veterinarias_raw.json"
REVISION_JSON = BASE_DIR / "revision_prospectos.json"
RESUMEN_MD = BASE_DIR / "resumen_revision.md"
CREDENTIALS_JSON = BASE_DIR / "credentials.json"
TOKEN_JSON = BASE_DIR / "token.json"

# ---------------------------------------------------------------------------
# 01 - Extracción
# ---------------------------------------------------------------------------
ZONAS = [
    "Zona Río",
    "Playas de Tijuana",
    "Otay",
    "El Soler",
    "Macroplaza",
]
TERMINO_BUSQUEDA = "veterinarias"
CIUDAD = "Tijuana, Baja California"
MAX_RESULTADOS_POR_ZONA = int(os.getenv("MAX_RESULTADOS_POR_ZONA", "20"))

# ---------------------------------------------------------------------------
# 02 - Generación de pitches con Gemini
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
# Si el modelo principal no está disponible, agota su cuota o está saturado, se usa este:
GEMINI_MODEL_FALLBACK = os.getenv("GEMINI_MODEL_FALLBACK", "gemini-3.8-flash")
MAX_PALABRAS = 90

# Datos del remitente (se usan en el prompt y en la firma del correo)
REMITENTE_NOMBRE = os.getenv("REMITENTE_NOMBRE", "Tu Nombre")
REMITENTE_CARGO = os.getenv("REMITENTE_CARGO", "Desarrollador")
EMPRESA = os.getenv("EMPRESA", "Tu Empresa")
NOMBRE_SOFTWARE = os.getenv("NOMBRE_SOFTWARE", "nuestro sistema")
REMITENTE_TELEFONO = os.getenv("REMITENTE_TELEFONO", "")
REMITENTE_SITIO = os.getenv("REMITENTE_SITIO", "")
# Opcional: nombre de la veterinaria cliente (solo si tienes su permiso para mencionarla)
CLIENTE_REFERENCIA = os.getenv("CLIENTE_REFERENCIA", "")
# Línea de salida amable al final del correo (buena práctica anti-spam)
LINEA_OPT_OUT = os.getenv(
    "LINEA_OPT_OUT",
    "Si no es de su interés, con gusto no vuelvo a escribirle.",
)

FRASES_PROHIBIDAS = [
    "oferta única",
    "oferta unica",
    "el mejor software",
    "revolucionario",
    "revolucionar",
    "no te lo puedes perder",
    "no se lo puede perder",
    "por tiempo limitado",
    "última oportunidad",
    "ultima oportunidad",
    "garantizado",
    "100%",
    "increíble",
    "solución integral",
    "de vanguardia",
    "estimado cliente",
]

# ---------------------------------------------------------------------------
# 03 - Borradores en Gmail
# ---------------------------------------------------------------------------
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
LIMITE_BORRADORES = int(os.getenv("LIMITE_BORRADORES", "10"))
LIMITE_BORRADORES_MAX = 15  # tope duro por tanda, no se puede superar


def firma() -> str:
    """Firma que se agrega al final de cada correo (no cuenta para las 90 palabras)."""
    lineas = [REMITENTE_NOMBRE]
    if REMITENTE_CARGO or EMPRESA:
        lineas.append(" | ".join(x for x in (REMITENTE_CARGO, EMPRESA) if x))
    if REMITENTE_TELEFONO:
        lineas.append(f"Tel/WhatsApp: {REMITENTE_TELEFONO}")
    if REMITENTE_SITIO:
        lineas.append(REMITENTE_SITIO)
    return "\n".join(lineas)
