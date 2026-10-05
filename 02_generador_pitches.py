#!/usr/bin/env python3
"""
02 - Generador de pitches personalizados con Gemini.

Lee `veterinarias_raw.json`, genera un correo en frío corto por clínica y guarda:
  - revision_prospectos.json  (fuente de verdad, editable a mano)
  - revision_prospectos.csv   (para abrir en Excel / LibreOffice)
  - resumen_revision.md       (tabla + borradores completos para revisión humana)

Es re-ejecutable: las clínicas ya generadas se conservan (con tus ediciones y
estados). Usa --regenerar para rehacer las que sigan en estado "pendiente".

Uso:
  python 02_generador_pitches.py
  python 02_generador_pitches.py --limite 5            # prueba rápida
  python 02_generador_pitches.py --solo-con-email
  python 02_generador_pitches.py --regenerar
  python 02_generador_pitches.py --solo-resumen        # solo refresca el Markdown/CSV
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import pandas as pd
from pydantic import BaseModel, Field

import config
from utils import (
    ahora_iso,
    cargar_json,
    contar_palabras,
    escribir_resumen_md,
    guardar_json,
)

TECNICISMOS = (
    "saas", "software as a service", "cloud", "nube", "api", "dashboard", "crm",
    "base de datos", "plataforma digital", "erp", "interfaz", "backend", "algoritmo",
    "inteligencia artificial", "transformación digital",
)


class Pitch(BaseModel):
    asunto: str = Field(description="Asunto del correo, máximo 7 palabras, sin emojis ni signos de exclamación.")
    cuerpo: str = Field(description="Cuerpo del correo SIN despedida ni firma, menos de 90 palabras.")


def instrucciones_sistema() -> str:
    prueba_social = (
        f"La {config.CLIENTE_REFERENCIA} aquí en Tijuana ya utiliza nuestro sistema"
        if config.CLIENTE_REFERENCIA
        else "Una veterinaria activa en Tijuana ya utiliza nuestro sistema"
    )
    return f"""Eres {config.REMITENTE_NOMBRE}, desarrollador en {config.EMPRESA}, un equipo de desarrollo de software local en Tijuana.
Ofreces un servicio de desarrollo de software médico veterinario rápido y adaptado a las necesidades exactas de cada clínica (expediente clínico de mascotas, control de consultas, vacunación y emisión de recetas).
{prueba_social} para agilizar la atención diaria y llevar el historial médico de sus pacientes sin complicaciones.

Escribe un correo en frío para el dueño o encargado de UNA veterinaria. Reglas OBLIGATORIAS:
1. Español mexicano profesional y cálido, trato de "usted". Natural, como lo escribiría una persona, no un vendedor.
2. El cuerpo debe tener MENOS de {config.MAX_PALABRAS} palabras (ideal 55-80). Párrafos cortos (2-4).
3. Menciona que somos desarrolladores locales en Tijuana.
4. Prueba social: menciona explícitamente que la {config.CLIENTE_REFERENCIA} en Tijuana ya lo usa para agilizar sus expedientes y consultas.
5. Cierra con un llamado a la acción de baja fricción: una videollamada de 10 minutos o una visita presencial
   a su clínica para mostrarles una cuenta demo adaptada a su flujo de trabajo. Formúlalo como una pregunta sencilla.
6. Personaliza con UN detalle real de los datos de la clínica (nombre, colonia o sus reseñas), sin exagerar ni adular.
7. Cero tecnicismos (nada de "plataforma", "nube", "SaaS", "dashboard", "CRM", "API", "inteligencia artificial").
8. Cero clichés de ventas: nada de "oferta única", "el mejor software", "revolucionario", "por tiempo limitado",
   "no se lo puede perder", "garantizado", ni signos de exclamación.
9. No inventes cifras, precios, porcentajes ni funciones que no se mencionan aquí.
10. NO incluyas despedida, firma, nombre del remitente, ni marcadores como [Nombre] o {{clinica}}: la firma se agrega aparte.
11. Empieza con un saludo breve dirigido al equipo de la clínica (p. ej. "Hola, equipo de <nombre>:").

Devuelve únicamente JSON con las claves "asunto" y "cuerpo"."""


def datos_clinica(v: dict) -> str:
    lineas = [f"Nombre de la clínica: {v.get('nombre', '')}"]
    if v.get("colonia"):
        lineas.append(f"Colonia: {v['colonia']}")
    if v.get("zona_busqueda"):
        lineas.append(f"Zona de Tijuana: {v['zona_busqueda']}")
    if v.get("categoria"):
        lineas.append(f"Categoría en Google: {v['categoria']}")
    if v.get("calificacion") is not None:
        lineas.append(f"Calificación en Google: {v['calificacion']} ({v.get('num_resenas') or 0} reseñas)")
    sitio = v.get("sitio_web", "")
    sitio_propio = bool(sitio) and not re.search(r"facebook\.com|instagram\.com|wa\.me", sitio)
    lineas.append("Tiene sitio web propio: " + ("sí" if sitio_propio else "no"))
    return "\n".join(lineas)


def validar(p: Pitch) -> list[str]:
    errores: list[str] = []
    cuerpo = p.cuerpo.strip()
    texto = f"{p.asunto} {cuerpo}".lower()
    n = contar_palabras(cuerpo)
    if n >= config.MAX_PALABRAS:
        errores.append(f"el cuerpo tiene {n} palabras; debe tener menos de {config.MAX_PALABRAS}")
    if "tijuana" not in cuerpo.lower():
        errores.append("no menciona que somos desarrolladores locales en Tijuana")
    if not re.search(r"\b(10|diez) minutos\b", cuerpo.lower()):
        errores.append("no propone la videollamada de 10 minutos")
    if re.search(r"\[[^\]]+\]|\{[^}]+\}", texto):
        errores.append("contiene marcadores tipo [Nombre] o {campo}")
    if "!" in texto or "¡" in texto:
        errores.append("contiene signos de exclamación")
    for f in config.FRASES_PROHIBIDAS:
        if f in texto:
            errores.append(f"contiene la frase prohibida '{f}'")
    for t in TECNICISMOS:
        if re.search(rf"\b{re.escape(t)}\b", texto):
            errores.append(f"contiene el tecnicismo '{t}'")
    ultima_linea = cuerpo.lower().splitlines()[-1] if cuerpo else ""
    if re.match(r"^(saludos|atentamente|quedo atento|un saludo)", ultima_linea) or (
        config.REMITENTE_NOMBRE.lower() in ultima_linea
    ):
        errores.append("incluye despedida o firma; la firma se agrega aparte")
    return errores


class GeneradorGemini:
    def __init__(self) -> None:
        if not config.GEMINI_API_KEY or config.GEMINI_API_KEY.startswith("pega_aqui"):
            sys.exit("[ERROR] Falta GEMINI_API_KEY (expórtala o ponla en .env).")
        from google import genai
        from google.genai import errors, types

        self.errors = errors
        self.types = types
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.modelo = config.GEMINI_MODEL
        self.config = types.GenerateContentConfig(
            system_instruction=instrucciones_sistema(),
            temperature=0.8,
            response_mime_type="application/json",
            response_schema=Pitch,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _cambiar_a_respaldo(self, motivo: str) -> bool:
        if self.modelo == config.GEMINI_MODEL_FALLBACK:
            return False
        print(f"  [!] '{self.modelo}' {motivo}. Cambiando a '{config.GEMINI_MODEL_FALLBACK}'.")
        self.modelo = config.GEMINI_MODEL_FALLBACK
        return True

    def _llamar(self, prompt: str):
        intentos = 0
        while True:
            try:
                return self.client.models.generate_content(
                    model=self.modelo, contents=prompt, config=self.config
                )
            except self.errors.ClientError as exc:
                code = getattr(exc, "code", None)
                if code == 404 and self._cambiar_a_respaldo("no está disponible"):
                    intentos = 0
                    continue
                if code == 429:
                    cuota_diaria = "PerDay" in str(exc) or "per day" in str(exc).lower()
                    if (cuota_diaria or intentos >= 2) and self._cambiar_a_respaldo("agotó su cuota"):
                        intentos = 0
                        continue
                    if intentos < 5:
                        espera = 15 * (intentos + 1)
                        print(f"  [rate-limit] esperando {espera}s...")
                        time.sleep(espera)
                        intentos += 1
                        continue
                raise
            except self.errors.ServerError:
                if intentos >= 2 and self._cambiar_a_respaldo("está saturado"):
                    intentos = 0
                    continue
                if intentos < 4:
                    time.sleep(10)
                    intentos += 1
                    continue
                raise

    def generar(self, v: dict, max_intentos: int = 3) -> tuple[Pitch, list[str]]:
        base = "Datos de la clínica:\n" + datos_clinica(v)
        prompt = base
        pitch: Pitch | None = None
        errores: list[str] = []
        for _ in range(max_intentos):
            resp = self._llamar(prompt)
            pitch = resp.parsed if isinstance(resp.parsed, Pitch) else Pitch.model_validate_json(resp.text)
            pitch.asunto = pitch.asunto.strip().strip('"').replace("\\n", " ")
            pitch.cuerpo = pitch.cuerpo.strip().replace("\\n", "\n")
            errores = validar(pitch)
            if not errores:
                return pitch, []
            prompt = (
                f"{base}\n\nTu versión anterior fue:\nAsunto: {pitch.asunto}\n{pitch.cuerpo}\n\n"
                "Corrige estos problemas y reescríbelo cumpliendo TODAS las reglas:\n- "
                + "\n- ".join(errores)
            )
        assert pitch is not None
        return pitch, errores


def construir_item(v: dict, pitch: Pitch | None, advertencias: list[str], modelo: str) -> dict:
    tel = v.get("telefono_normalizado") or re.sub(r"\D", "", v.get("telefono", ""))
    wa = v.get("whatsapp") or (f"52{tel}" if len(tel) == 10 else "")
    return {
        "id": v["id"],
        "estado": "pendiente",
        "nombre": v.get("nombre", ""),
        "email": v.get("email", ""),
        "emails_alternos": v.get("emails_encontrados", [])[1:],
        "telefono": v.get("telefono", ""),
        "whatsapp_link": f"https://wa.me/{wa}" if wa else "",
        "direccion": v.get("direccion", ""),
        "colonia": v.get("colonia", ""),
        "zona": v.get("zona_busqueda", ""),
        "calificacion": v.get("calificacion"),
        "num_resenas": v.get("num_resenas"),
        "sitio_web": v.get("sitio_web", ""),
        "facebook": v.get("facebook", ""),
        "url_maps": v.get("url_maps", ""),
        "asunto": pitch.asunto if pitch else "",
        "cuerpo": pitch.cuerpo if pitch else "",
        "palabras": contar_palabras(pitch.cuerpo) if pitch else 0,
        "advertencias": advertencias,
        "modelo": modelo,
        "generado_en": ahora_iso(),
    }


def exportar(items: list[dict]) -> None:
    guardar_json(config.REVISION_JSON, items)
    escribir_resumen_md(items)
    if items:
        cols = ["estado", "nombre", "colonia", "zona", "calificacion", "num_resenas",
                "email", "telefono", "whatsapp_link", "asunto", "cuerpo", "palabras"]
        df = pd.DataFrame(items)
        df = df[[c for c in cols if c in df.columns]]
        df.to_csv(config.REVISION_JSON.with_suffix(".csv"), index=False, encoding="utf-8-sig")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genera correos en frío personalizados con Gemini.")
    p.add_argument("--limite", type=int, default=0, help="Máximo de clínicas nuevas a procesar (0 = todas).")
    p.add_argument("--solo-con-email", action="store_true", help="Generar solo para clínicas con email.")
    p.add_argument("--regenerar", action="store_true", help="Rehacer los pitches en estado 'pendiente'.")
    p.add_argument("--solo-resumen", action="store_true", help="No llamar a Gemini; solo refrescar MD/CSV.")
    p.add_argument("--pausa", type=float, default=4.0, help="Segundos entre llamadas (respeta el rate limit).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    items: list[dict] = cargar_json(config.REVISION_JSON, [])

    if args.solo_resumen:
        exportar(items)
        print(f"[OK] Resumen actualizado: {config.RESUMEN_MD.name} ({len(items)} prospectos)")
        return

    raw: list[dict] = cargar_json(config.RAW_JSON, [])
    if not raw:
        sys.exit(f"[ERROR] {config.RAW_JSON.name} no existe o está vacío. Ejecuta primero 01_extractor.py")

    por_id = {it["id"]: it for it in items}
    pendientes: list[dict] = []
    for v in raw:
        if args.solo_con_email and not v.get("email"):
            continue
        actual = por_id.get(v["id"])
        if actual is None or (args.regenerar and actual.get("estado") == "pendiente"):
            pendientes.append(v)
    if args.limite > 0:
        pendientes = pendientes[: args.limite]

    print(f"[i] {len(raw)} clínicas en bruto | {len(items)} ya revisables | {len(pendientes)} por generar")
    if not pendientes:
        exportar(items)
        print("[OK] Nada nuevo que generar. Resumen actualizado.")
        return

    gen = GeneradorGemini()
    print(f"[i] Modelo: {gen.modelo}\n")

    try:
        for n, v in enumerate(pendientes, 1):
            try:
                pitch, adv = gen.generar(v)
            except Exception as exc:  # noqa: BLE001 - no detener la tanda por una clínica
                print(f"  ({n}/{len(pendientes)}) [!] {v.get('nombre')}: {exc}")
                continue

            nuevo = construir_item(v, pitch, adv, gen.modelo)
            if v["id"] in por_id:
                idx = next(i for i, it in enumerate(items) if it["id"] == v["id"])
                items[idx] = nuevo
            else:
                items.append(nuevo)
            por_id[v["id"]] = nuevo
            guardar_json(config.REVISION_JSON, items)  # guardado incremental

            marca = "⚠️ " if adv else "✅"
            print(f"  ({n}/{len(pendientes)}) {marca} {v.get('nombre', '')[:45]:45} "
                  f"{nuevo['palabras']} palabras | {nuevo['asunto']}")
            time.sleep(args.pausa)
    except KeyboardInterrupt:
        print("\n[i] Interrumpido. Lo generado ya está guardado.")

    exportar(items)
    print(
        f"\n[OK] {len(items)} prospectos en {config.REVISION_JSON.name}\n"
        f"     Revisa: {config.RESUMEN_MD}\n"
        f"     Edita/descarta en {config.REVISION_JSON.name} y luego corre 03_crear_borradores_gmail.py"
    )


if __name__ == "__main__":
    main()
