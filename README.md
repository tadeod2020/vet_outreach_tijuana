# vet_outreach_tijuana 🐾

Pipeline local y semiautomatizado para prospectar veterinarias en Tijuana y ofrecer una demo del
sistema de gestión de pacientes / expediente clínico.

```
01_extractor.py  ──►  veterinarias_raw.json
                         │
02_generador_pitches.py ─┴─►  revision_prospectos.json (+ .csv)  +  resumen_revision.md
                                     │
                         👀 Revisión manual (editar / descartar)
                                     │
03_crear_borradores_gmail.py ────────┴─►  Gmail ▸ Borradores   (NUNCA envía)
```

## Estructura

| Archivo | Función |
|---|---|
| `config.py` | Zonas, datos del remitente, modelo, límites. Lee `.env` si existe. |
| `utils.py` | JSON atómico, composición del correo final, generación del Markdown. |
| `01_extractor.py` | Google Maps (Playwright headless) + emails desde los sitios web. |
| `02_generador_pitches.py` | Correos personalizados con Gemini + validación de reglas. |
| `03_crear_borradores_gmail.py` | Inserta borradores en Gmail (límite por tanda). |
| `.env.example` | Plantilla de variables de entorno. |

---

## 1. Instalación (Linux)

```bash
cd vet_outreach_tijuana
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Navegador para Playwright
playwright install chromium
# Si faltan librerías del sistema (Ubuntu/Debian):
playwright install-deps chromium      # pide sudo
```

## 2. Configuración de credenciales

### 2.1 Variables de entorno (`.env`)

```bash
cp .env.example .env
nano .env        # completa GEMINI_API_KEY, tu nombre, empresa, teléfono, etc.
```

- `GEMINI_API_KEY`: genera una en <https://aistudio.google.com/apikey>.
- También puedes exportarla en la terminal: `export GEMINI_API_KEY="..."` (tiene prioridad sobre `.env`).
- `CLIENTE_REFERENCIA`: déjalo vacío salvo que tu veterinaria cliente te haya autorizado a mencionarla.

> **Nota sobre el modelo:** `gemini-1.5-flash` ya fue retirado por Google (responde 404). El `.env`
> usa `gemini-3.1-pro-preview` (el mejor disponible para redacción) y, si se agota su cuota, está
> saturado o desaparece, el script cambia solo a `GEMINI_MODEL_FALLBACK` (`gemini-3.8-flash`).

### 2.2 `credentials.json` de Gmail (una sola vez)

1. Entra a <https://console.cloud.google.com/> y crea (o elige) un proyecto.
2. **APIs y servicios ▸ Biblioteca** → busca **Gmail API** → **Habilitar**.
3. **APIs y servicios ▸ Pantalla de consentimiento de OAuth** (o *Google Auth Platform*):
   - Tipo de usuario: **Externo** (o Interno si usas Workspace).
   - En **Usuarios de prueba**, agrega tu propia cuenta de Gmail.
4. **APIs y servicios ▸ Credenciales ▸ Crear credenciales ▸ ID de cliente de OAuth**
   - Tipo de aplicación: **App de escritorio**.
   - Descarga el JSON y guárdalo como `vet_outreach_tijuana/credentials.json`.
5. La primera vez que corras el paso 03 se abrirá el navegador para autorizar; se guarda `token.json`
   (scope `gmail.compose`: solo permite crear borradores/enviar, no leer tu bandeja).

> `credentials.json`, `token.json` y `.env` están en `.gitignore`. No los compartas.

---

## 3. Ejecución en orden

Siempre con el entorno activo: `source .venv/bin/activate`

### Paso 01 – Extraer veterinarias

```bash
python 01_extractor.py                                   # 5 zonas, 20 por zona
python 01_extractor.py --zonas "Zona Río" --max-por-zona 5   # prueba rápida
python 01_extractor.py --headful                         # ver el navegador (si algo falla)
```

- Resultado: `veterinarias_raw.json`. Se guarda tras cada clínica; si lo interrumpes (Ctrl+C) no
  pierdes nada y al re-ejecutarlo omite las ya extraídas (usa `--reset` para empezar de cero).
- Los emails solo se obtienen si la clínica tiene sitio web propio; muchas veterinarias pequeñas solo
  tienen Facebook/teléfono — esas quedan con `whatsapp_link` para contacto manual.

### Paso 02 – Generar los correos

```bash
python 02_generador_pitches.py --limite 3        # prueba con 3 clínicas
python 02_generador_pitches.py                   # todas las nuevas
python 02_generador_pitches.py --solo-con-email  # ahorra cuota: solo las que tienen email
```

Cada correo se valida automáticamente (menos de 90 palabras, menciona Tijuana, CTA de 10 minutos,
sin clichés, sin tecnicismos, sin signos de exclamación). Si falla, se reintenta hasta 3 veces;
si aún así no cumple, queda marcado con ⚠️ en `advertencias`.

La firma (nombre, empresa, teléfono) y una línea de salida amable se agregan aparte y **no cuentan**
para las 90 palabras.

### Paso 2.5 – Revisión manual 👀

1. Abre `resumen_revision.md` (tabla + cada correo completo) o `revision_prospectos.csv`.
2. Edita en `revision_prospectos.json`:
   - Cambia `asunto` o `cuerpo` si quieres ajustar el texto.
   - `"estado": "descartado"` → no se creará borrador.
   - `"estado": "aprobado"` → útil si usas `--solo-aprobados` en el paso 03.
3. Refresca el resumen: `python 02_generador_pitches.py --solo-resumen`

### Paso 03 – Crear borradores en Gmail

```bash
python 03_crear_borradores_gmail.py --dry-run          # vista previa, no toca Gmail
python 03_crear_borradores_gmail.py                    # crea hasta 10 borradores
python 03_crear_borradores_gmail.py --limite 15        # tope duro: 15 por tanda
python 03_crear_borradores_gmail.py --solo-aprobados
```

- **Nunca envía correos.** Los borradores aparecen en <https://mail.google.com/mail/u/0/#drafts>;
  tú los revisas y envías manualmente.
- Cada prospecto procesado queda como `borrador_creado` (con `draft_id`), así que la siguiente
  ejecución toma la siguiente tanda sin duplicar.

---

## Ciclo recomendado

```bash
source .venv/bin/activate
python 01_extractor.py
python 02_generador_pitches.py
# ...revisar resumen_revision.md y editar revision_prospectos.json...
python 02_generador_pitches.py --solo-resumen
python 03_crear_borradores_gmail.py --dry-run
python 03_crear_borradores_gmail.py
```

Sugerencia: envía 10–15 al día desde Gmail, a horas hábiles (9:00–13:00), y da seguimiento por
WhatsApp/teléfono a quienes no respondan en 3–4 días.

## Solución de problemas

| Problema | Solución |
|---|---|
| `0 fichas encontradas` / captcha | Corre con `--headful`, baja `--max-por-zona`, espera unos minutos. Google cambia el DOM de Maps con frecuencia: los selectores están en `buscar_zona()` y `extraer_ficha()`. |
| `Host system is missing dependencies` | `playwright install-deps chromium` |
| `404 ... gemini-1.5-flash` | Normal: se usa el fallback automáticamente. Pon `GEMINI_MODEL=gemini-2.5-flash` en `.env`. |
| `429` (rate limit) | El script espera y reintenta. Sube `--pausa` (p. ej. `--pausa 8`). |
| `access_denied` en OAuth | Agrega tu cuenta como **usuario de prueba** en la pantalla de consentimiento. |
| `invalid_grant` | Borra `token.json` y vuelve a correr el paso 03. |

## Nota de uso responsable

- El scraping automatizado de Google Maps puede contravenir sus Términos de Servicio; úsalo con
  volúmenes bajos y para uso propio. La alternativa oficial es la Places API.
- Contacta solo datos de contacto **públicos de negocio**, ofrece una forma fácil de no recibir más
  correos (ya incluida) y respeta a quien pida no ser contactado (márcalo como `descartado`).
