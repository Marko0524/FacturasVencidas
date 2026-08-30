"""Turning an uploaded file into fragments. Pure functions, no database."""

from __future__ import annotations

import pytest

from app.ingest import IngestError, parse_upload, safe_name, slug, split_text
from tests.conftest import LOGISTICA

CON_ENCABEZADOS = """---
titulo: metadatos que no son contenido
---

# Convenio

## Primera

Texto suficientemente largo para superar el mínimo de caracteres exigido.

## Segunda

Otro bloque con longitud más que suficiente para pasar el filtro de tamaño.
"""


def subir(nombre: str, texto: str, cliente: str = LOGISTICA):
    return parse_upload(nombre, texto.encode("utf-8"), cliente)


# --- troceado ----------------------------------------------------------------


def test_headings_become_separate_fragments():
    piezas = split_text(CON_ENCABEZADOS)

    assert len(piezas) == 2
    assert piezas[0].startswith("## Primera")


def test_front_matter_is_not_indexed():
    """Es metadato, no contenido que alguien vaya a preguntar."""
    assert "titulo: metadatos" not in "\n".join(split_text(CON_ENCABEZADOS))


def test_a_document_without_headings_is_packed_by_paragraph():
    parrafos = "\n\n".join(f"Párrafo número {i} con longitud suficiente." for i in range(40))

    piezas = split_text(parrafos)

    assert len(piezas) > 1
    assert all(len(p) <= 1200 for p in piezas)


def test_paragraphs_are_never_cut_in_half():
    largo = "Una sola oración muy larga. " * 60

    for pieza in split_text(largo):
        assert pieza.strip().endswith(".")


def test_scraps_below_the_minimum_are_dropped():
    """Un fragmento de tres palabras no es evidencia de nada."""
    assert split_text("# T\n\n## A\n\nok\n\n## B\n\n" + "x" * 200) == ["## B\n\n" + "x" * 200]


# --- validación --------------------------------------------------------------


def test_the_title_comes_from_the_first_heading():
    assert subir("archivo.md", CON_ENCABEZADOS).titulo == "Convenio"


def test_without_a_heading_the_filename_becomes_the_title():
    assert subir("convenio-flotilla.md", "x" * 200).titulo == "convenio flotilla"


@pytest.mark.parametrize("nombre", ["hoja.xlsx", "imagen.png", "presentacion.pptx", "sinextension"])
def test_unsupported_formats_are_refused(nombre: str):
    with pytest.raises(IngestError, match="admiten"):
        subir(nombre, "x" * 200)


def test_a_pdf_is_accepted_and_its_text_indexed():
    """Un PDF no se decodifica: se le extrae el texto, que es otra cosa."""
    from app.pdf import markdown_a_pdf

    fuente = "# Convenio\n\n## Deducible\n\n" + "El deducible es del tres por ciento. " * 6
    pdf = markdown_a_pdf(fuente, "Convenio")

    parsed = parse_upload("caratula.pdf", pdf, LOGISTICA)

    assert parsed.medio == "application/pdf"
    assert parsed.archivo == pdf
    assert any("tres por ciento" in f for f in parsed.fragmentos)


def test_a_file_that_is_not_really_a_pdf_is_refused():
    with pytest.raises(IngestError, match="PDF"):
        parse_upload("falso.pdf", b"esto no es un pdf" * 40, LOGISTICA)


def test_an_empty_file_is_refused():
    with pytest.raises(IngestError, match="vacío"):
        subir("a.md", "   ")


def test_a_file_over_the_size_cap_is_refused():
    with pytest.raises(IngestError, match="MB"):
        parse_upload("a.md", b"x" * (3 * 1024 * 1024), LOGISTICA)


def test_a_file_with_no_usable_text_is_refused():
    with pytest.raises(IngestError, match="aprovechable"):
        subir("a.md", "# T\n\nhola\n")


def test_latin1_is_decoded_rather_than_rejected():
    """Es la otra codificación que una oficina hispanohablante produce de verdad."""
    crudo = ("Pólizas con acentos y eñes. " * 20).encode("latin-1")

    parsed = parse_upload("a.md", crudo, LOGISTICA)

    assert "Pólizas" in parsed.fragmentos[0]


def test_a_binary_file_named_txt_is_refused():
    with pytest.raises(IngestError):
        parse_upload("a.md", bytes(range(256)) * 20, LOGISTICA)


# --- nombres -----------------------------------------------------------------


def test_the_owner_is_part_of_the_stored_name():
    """Dos clientes subiendo 'condiciones.md' no pueden pisarse entre sí."""
    uno = subir("condiciones.md", "x" * 200, "a@uno.mx").nombre
    otro = subir("condiciones.md", "x" * 200, "b@dos.mx").nombre

    assert uno != otro
    assert uno.startswith("carga/a-uno-mx/")


@pytest.mark.parametrize(
    "entrada",
    ["../../etc/passwd.md", "..\\..\\windows\\system32\\a.md", "/absoluto/ruta.md"],
)
def test_path_traversal_is_stripped_from_the_name(entrada: str):
    """Un nombre de archivo nunca es una ruta."""
    limpio = safe_name(entrada)

    assert "/" not in limpio
    assert "\\" not in limpio
    assert ".." not in limpio


def test_a_name_with_no_usable_characters_still_gets_one():
    assert safe_name("???") == "documento.txt"


def test_the_slug_drops_accents_and_symbols():
    assert slug("José.Pérez+etiqueta@Correo.MX") == "jose-perez-etiqueta-correo-mx"


def test_a_file_full_of_nul_bytes_is_refused():
    """El byte NUL no aparece en texto escrito por ningún editor."""
    with pytest.raises(IngestError, match="binario"):
        parse_upload("a.md", b"texto\x00texto" * 100, LOGISTICA)


def test_prose_with_accents_is_not_mistaken_for_binary():
    """El detector no puede rechazar español legítimo."""
    from app.ingest import looks_like_text

    assert looks_like_text("Póliza, cobertura, deducible y años. ¿Cuánto?\n\tSangría.")
