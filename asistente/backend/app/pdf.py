"""PDF: leerlo para indexarlo, y escribirlo para entregarlo.

Los documentos que la gente sube y descarga son PDF, pero un PDF no se puede
buscar por similitud: hay que sacarle el texto. Y el corpus del repositorio se
escribe en Markdown —porque el front matter es donde vive el alcance de cada
documento, y un PDF no tiene dónde llevarlo— así que se renderiza a PDF para
que se pueda descargar como los demás.

Dos formatos con papeles distintos, y ninguno intenta hacer el del otro:
Markdown es el formato de autoría, PDF el de entrega.
"""

from __future__ import annotations

import io
import logging
import re

logger = logging.getLogger(__name__)

MEDIO_PDF = "application/pdf"
MEDIO_MARKDOWN = "text/markdown; charset=utf-8"

# Una página carta con márgenes de 2 cm.
ANCHO, ALTO = 612, 792
MARGEN = 57


class PdfError(ValueError):
    """El PDF no se pudo leer, y la razón se puede decir."""


def extraer_texto(datos: bytes) -> str:
    """Saca el texto de un PDF para poder indexarlo.

    Un PDF escaneado no tiene texto, solo una imagen de texto. Devolver cadena
    vacía haría que se indexara un documento sin contenido y que las preguntas
    sobre él escalaran sin explicación; decirlo permite responder que hace falta
    OCR, que es la verdad.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depende de la instalación
        raise PdfError("falta pypdf: pip install -r requirements.txt") from exc

    try:
        lector = PdfReader(io.BytesIO(datos))
    except Exception as exc:  # noqa: BLE001 - pypdf lanza de todo
        raise PdfError("el archivo no es un PDF válido o está dañado") from exc

    if getattr(lector, "is_encrypted", False):
        # Se intenta la contraseña vacía, que abre los PDF con permisos pero sin
        # clave de apertura. Si no basta, no hay nada que hacer sin la clave.
        try:
            if lector.decrypt("") == 0:
                raise PdfError("el PDF está protegido con contraseña")
        except PdfError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PdfError("el PDF está protegido con contraseña") from exc

    partes = []
    for numero, pagina in enumerate(lector.pages, start=1):
        try:
            partes.append(pagina.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - una página rota no tira el resto
            logger.warning("No se pudo extraer la página %d: %s", numero, exc)

    texto = _limpiar("\n\n".join(p for p in partes if p.strip()))
    if not texto.strip():
        raise PdfError(
            "el PDF no contiene texto extraíble; si es un escaneo hace falta OCR"
        )
    return texto


def _limpiar(texto: str) -> str:
    """Deshace los artefactos típicos de la extracción.

    Los extractores parten líneas donde el PDF las partió visualmente, no donde
    termina la frase, y dejan espacios dobles por el ajuste tipográfico. Sin
    esto, los fragmentos quedan cortados a media oración y la cita que se le
    enseña a la persona se lee mal.
    """
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"[ \t]+", " ", texto)
    # Un guion al final de línea es una palabra partida por el ajuste.
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)
    # Un salto simple entre minúscula y minúscula es ajuste, no párrafo nuevo.
    texto = re.sub(r"(?<=[a-záéíóúñ,;])\n(?=[a-záéíóúñ])", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


# --- generación ---------------------------------------------------------------


def markdown_a_pdf(markdown: str, titulo: str) -> bytes:
    """Renderiza un documento del corpus como PDF descargable.

    Deliberadamente simple: encabezados, párrafos, viñetas y tablas. No es un
    motor de Markdown, es lo justo para que una carátula de póliza se lea como
    un documento y no como texto plano volcado en una página.
    """
    try:
        import reportlab  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depende de la instalación
        # Solo se culpa a la dependencia cuando de verdad falta. Envolver todos
        # los ImportError convierte cualquier error de nombre dentro de esta
        # función en un "falta reportlab" que manda a buscar donde no es.
        raise PdfError("falta reportlab: pip install -r requirements.txt") from exc

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    salida = io.BytesIO()
    documento = SimpleDocTemplate(
        salida,
        pagesize=letter,
        leftMargin=MARGEN, rightMargin=MARGEN,
        topMargin=MARGEN, bottomMargin=MARGEN,
        title=titulo,
        author="Aseguradora",
    )

    base = getSampleStyleSheet()
    estilos = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=17, leading=21,
                             spaceAfter=12, textColor=colors.HexColor("#0f172a")),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=12.5, leading=16,
                             spaceBefore=14, spaceAfter=6,
                             textColor=colors.HexColor("#0369a1")),
        "p": ParagraphStyle("p", parent=base["BodyText"], fontSize=10, leading=15,
                            alignment=TA_LEFT, spaceAfter=7),
        "li": ParagraphStyle("li", parent=base["BodyText"], fontSize=10, leading=15,
                             leftIndent=14, bulletIndent=4, spaceAfter=4),
    }

    flujo = []
    for bloque in _bloques(markdown):
        tipo, contenido = bloque
        if tipo == "h1":
            flujo.append(Paragraph(_inline(contenido), estilos["h1"]))
        elif tipo == "h2":
            flujo.append(Paragraph(_inline(contenido), estilos["h2"]))
        elif tipo == "li":
            flujo.append(Paragraph(_inline(contenido), estilos["li"], bulletText="•"))
        elif tipo == "tabla":
            flujo.append(_tabla(contenido, colors))
            flujo.append(Spacer(1, 8))
        else:
            flujo.append(Paragraph(_inline(contenido), estilos["p"]))

    documento.build(flujo)
    return salida.getvalue()


def _bloques(markdown: str) -> list[tuple[str, object]]:
    """Trocea el Markdown en bloques etiquetados por tipo."""
    cuerpo = markdown
    if cuerpo.startswith("---"):
        partes = cuerpo.split("---", 2)
        cuerpo = partes[2] if len(partes) >= 3 else cuerpo

    bloques: list[tuple[str, object]] = []
    filas: list[list[str]] = []

    def cerrar_tabla():
        if filas:
            bloques.append(("tabla", list(filas)))
            filas.clear()

    parrafo: list[str] = []

    def cerrar_parrafo():
        if parrafo:
            bloques.append(("p", " ".join(parrafo)))
            parrafo.clear()

    for linea in cuerpo.splitlines():
        limpia = linea.strip()

        if limpia.startswith("|"):
            cerrar_parrafo()
            celdas = [c.strip() for c in limpia.strip("|").split("|")]
            # La fila de guiones solo separa el encabezado; no es contenido.
            if not all(set(c) <= set("-: ") for c in celdas):
                filas.append(celdas)
            continue

        cerrar_tabla()

        if not limpia:
            cerrar_parrafo()
        elif limpia.startswith("## "):
            cerrar_parrafo()
            bloques.append(("h2", limpia[3:]))
        elif limpia.startswith("# "):
            cerrar_parrafo()
            bloques.append(("h1", limpia[2:]))
        elif limpia.startswith("- "):
            cerrar_parrafo()
            bloques.append(("li", limpia[2:]))
        else:
            parrafo.append(limpia)

    cerrar_parrafo()
    cerrar_tabla()
    return bloques


def _tabla(filas: list[list[str]], colors):
    from reportlab.platypus import Table, TableStyle

    ancho_util = ANCHO - 2 * MARGEN
    columnas = max(len(f) for f in filas)
    normalizadas = [f + [""] * (columnas - len(f)) for f in filas]

    tabla = Table(normalizadas, colWidths=[ancho_util / columnas] * columnas)
    tabla.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("LEADING", (0, 0), (-1, -1), 12),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf1")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    return tabla


def _inline(texto: str) -> str:
    """Negritas y código de Markdown a las etiquetas que entiende reportlab.

    El escapado va primero: si fuera después, convertiría en literales las
    etiquetas que acabamos de generar.
    """
    seguro = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    seguro = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", seguro)
    seguro = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", seguro)
    return seguro
