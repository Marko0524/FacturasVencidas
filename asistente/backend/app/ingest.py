"""Turning an uploaded file into fragments the store can hold.

Everything a user uploads lands with ``alcance=cliente`` and their own address
as the owner. That is not a default someone could forget to set: the caller
never gets to choose the scope, because a scope chosen by the uploader is a
scope an attacker can choose too.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from app.pdf import MEDIO_MARKDOWN, MEDIO_PDF, PdfError, extraer_texto

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
EXTENSIONS = {".pdf", ".md", ".txt", ".markdown"}

# Long enough to carry an idea, short enough that a citation points somewhere
# specific. Sections win when the document has them; otherwise paragraphs are
# packed up to this size.
TARGET_CHARS = 900
MIN_CHARS = 40

HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
PARAGRAPH = re.compile(r"\n\s*\n")


class IngestError(ValueError):
    """The file cannot be turned into fragments, and the reason is worth saying."""


@dataclass(frozen=True)
class ParsedUpload:
    nombre: str
    titulo: str
    fragmentos: list[str]
    # Los bytes tal cual llegaron, para devolver el mismo archivo que se subió.
    archivo: bytes
    medio: str


def parse_upload(filename: str, raw: bytes, customer_email: str) -> ParsedUpload:
    """Validate, decode and split an uploaded file."""
    if len(raw) > MAX_BYTES:
        raise IngestError(f"el archivo pesa más de {MAX_BYTES // (1024 * 1024)} MB")
    if not raw.strip():
        raise IngestError("el archivo está vacío")

    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in EXTENSIONS:
        permitidas = ", ".join(sorted(EXTENSIONS))
        raise IngestError(f"solo se admiten archivos de texto ({permitidas})")

    if extension == ".pdf":
        # Un PDF no se decodifica: se le extrae el texto, que es otra cosa. Los
        # bytes originales se guardan aparte para devolverlos intactos.
        try:
            texto = extraer_texto(raw)
        except PdfError as exc:
            raise IngestError(str(exc)) from exc
        return _resultado(filename, texto, customer_email, raw, MEDIO_PDF)

    try:
        texto = raw.decode("utf-8")
    except UnicodeDecodeError:
        # latin-1 is the other encoding a Spanish-language office actually
        # produces. It also decodes *any* byte sequence without complaining,
        # so a failure to decode can no longer be the check: a .md that is
        # really a JPEG would sail through and be indexed as noise. What
        # follows looks at the result instead.
        texto = raw.decode("latin-1")

    if not looks_like_text(texto):
        raise IngestError("el archivo no parece texto; parece binario renombrado")

    return _resultado(filename, texto, customer_email, raw, MEDIO_MARKDOWN)


def _resultado(filename: str, texto: str, customer_email: str,
               raw: bytes, medio: str) -> ParsedUpload:
    fragmentos = split_text(texto)
    if not fragmentos:
        raise IngestError("el archivo no tiene texto aprovechable")

    seguro = safe_name(filename)
    return ParsedUpload(
        archivo=raw,
        medio=medio,
        # The owner is part of the stored name, so two customers uploading
        # "condiciones.pdf" do not collide into one row and overwrite each other.
        nombre=f"carga/{slug(customer_email)}/{seguro}",
        titulo=first_heading(texto) or seguro.rsplit(".", 1)[0].replace("-", " ").strip(),
        fragmentos=fragmentos,
    )


def looks_like_text(texto: str) -> bool:
    """Whether a decoded string is prose rather than decoded binary.

    Two signals, both cheap. A NUL byte does not occur in text produced by any
    editor, and real prose is overwhelmingly printable — control characters
    beyond tab, newline and carriage return are the fingerprint of a file that
    was never text to begin with.
    """
    if "\x00" in texto:
        return False

    muestra = texto[:8000]
    if not muestra:
        return False

    raros = sum(
        1 for c in muestra if unicodedata.category(c) in {"Cc", "Co", "Cs"} and c not in "\n\r\t"
    )
    return raros / len(muestra) < 0.02


def split_text(texto: str) -> list[str]:
    """Split on headings when the document has them, on paragraphs otherwise."""
    cuerpo = strip_front_matter(texto)

    secciones = _split_headings(cuerpo)
    if len(secciones) > 1:
        piezas = secciones
    else:
        piezas = _pack_paragraphs(cuerpo)

    return [p.strip() for p in piezas if len(p.strip()) >= MIN_CHARS]


def strip_front_matter(texto: str) -> str:
    """Drop a leading ``---`` block: it is metadata, not content to retrieve."""
    if not texto.startswith("---"):
        return texto
    partes = texto.split("---", 2)
    return partes[2] if len(partes) >= 3 else texto


def first_heading(texto: str) -> str:
    for linea in strip_front_matter(texto).splitlines():
        limpia = linea.strip()
        if limpia.startswith("#"):
            return limpia.lstrip("#").strip()
    return ""


def safe_name(filename: str) -> str:
    """Keep the name recognisable, and keep it a name.

    Path separators and traversal segments are removed rather than escaped: the
    value ends up in an identifier, and a filename is never a path.
    """
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = base.replace("..", "")
    base = re.sub(r"[^A-Za-z0-9._ -]", "", base).strip() or "documento.txt"
    return base[:120]


def slug(valor: str) -> str:
    """A short, stable, filesystem-safe form of an address."""
    plano = unicodedata.normalize("NFD", valor.lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", plano).strip("-")[:60]


def _split_headings(cuerpo: str) -> list[str]:
    posiciones = [m.start() for m in HEADING.finditer(cuerpo)]
    if not posiciones:
        return [cuerpo]

    piezas = []
    if posiciones[0] > 0:
        piezas.append(cuerpo[: posiciones[0]])
    for indice, inicio in enumerate(posiciones):
        fin = posiciones[indice + 1] if indice + 1 < len(posiciones) else len(cuerpo)
        piezas.append(cuerpo[inicio:fin])
    return piezas


def _pack_paragraphs(cuerpo: str) -> list[str]:
    """Group paragraphs up to the target size, never splitting one in half."""
    piezas: list[str] = []
    actual = ""
    for parrafo in PARAGRAPH.split(cuerpo):
        parrafo = parrafo.strip()
        if not parrafo:
            continue
        if actual and len(actual) + len(parrafo) + 2 > TARGET_CHARS:
            piezas.append(actual)
            actual = parrafo
        else:
            actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    if actual:
        piezas.append(actual)
    return piezas
