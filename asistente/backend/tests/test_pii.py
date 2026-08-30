"""Redacción de datos personales antes del prompt.

Dos mitades, y la segunda importa tanto como la primera. Que redacte lo que debe
es la mitad obvia. La otra es que **no toque lo que la aplicación necesita**: si
la redacción se come el folio de una factura, la consulta llega sin número y se
responde con el resumen de la cuenta — que parece una respuesta razonable, y por
eso el fallo pasaría desapercibido.
"""

from __future__ import annotations

import pytest

from app.pii import redactar

# Número de prueba de Visa: pasa Luhn y no pertenece a nadie.
TARJETA = "4111 1111 1111 1111"


# --- lo que sí se redacta -----------------------------------------------------


@pytest.mark.parametrize(
    "texto,etiqueta",
    [
        ("mi RFC es GME180922K41 para la factura", "[RFC]"),
        ("mi CURP es ROCM850315HDFSZR04 por si acaso", "[CURP]"),
        ("escríbanme a marco.rosas@ejemplo.mx", "[correo]"),
        ("mi CLABE es 012180001234567895", "[CLABE]"),
        ("llámenme al 55 1234 5678", "[teléfono]"),
        (f"pagué con la tarjeta {TARJETA}", "[tarjeta]"),
    ],
)
def test_structured_identifiers_never_reach_the_prompt(texto: str, etiqueta: str):
    resultado = redactar(texto)

    assert etiqueta in resultado.texto
    assert resultado.hubo


def test_the_value_is_replaced_not_deleted():
    """Borrarlo dejaría la frase coja y el modelo intentaría rellenar el hueco."""
    resultado = redactar("mi RFC es GME180922K41 y quiero saber mi deducible")

    assert resultado.texto == "mi RFC es [RFC] y quiero saber mi deducible"


def test_what_was_found_is_reported_without_its_value():
    """El tipo va al log para poder medir; el dato nunca."""
    resultado = redactar(f"soy marco@ejemplo.mx y pagué con {TARJETA}")

    assert set(resultado.tipos) == {"correo", "tarjeta"}
    assert "4111" not in str(resultado.tipos)


def test_several_of_the_same_kind_all_go():
    resultado = redactar("escriban a uno@ejemplo.mx o a dos@ejemplo.mx")

    assert "@ejemplo.mx" not in resultado.texto


def test_a_curp_is_not_split_in_half_by_the_rfc_pattern():
    """Los primeros doce caracteres de una CURP tienen forma de RFC.

    Buscar el RFC primero dejaría los seis últimos a la vista.
    """
    resultado = redactar("ROCM850315HDFSZR04")

    assert resultado.texto == "[CURP]"
    assert "HDFSZR04" not in resultado.texto


# --- lo que NO puede tocar ----------------------------------------------------


def test_an_invoice_number_survives_untouched():
    """El fallo que rompería la ruta transaccional sin dar la cara."""
    for pregunta in ("¿Cómo va la factura INV-2001?", "estado de INV 3001", "inv1007"):
        assert redactar(pregunta).texto == pregunta


def test_a_policy_number_survives_untouched():
    texto = "mi póliza POL-RCG-2026-01193 vence pronto"

    assert redactar(texto).texto == texto


def test_amounts_are_not_mistaken_for_phone_numbers():
    """"118,400.00" tiene los dígitos de un teléfono. Los separadores lo salvan:
    el patrón admite espacio y guion, nunca punto ni coma."""
    texto = "la prima anual es $118,400.00 MXN y la suma asegurada $6,000,000.00"

    assert redactar(texto).texto == texto
    assert not redactar(texto).hubo


def test_a_long_number_that_is_not_a_card_is_left_alone():
    """La longitud no basta: Luhn es lo que distingue una tarjeta de una cifra."""
    texto = "el límite es 1234567812345678"

    assert redactar(texto).texto == texto


def test_dates_and_years_are_left_alone():
    texto = "la vigencia va del 1 de febrero de 2026 al 31 de enero de 2027"

    assert redactar(texto).texto == texto


def test_an_ordinary_question_comes_out_identical():
    texto = "¿Cuántos días tengo de periodo de gracia?"

    assert redactar(texto).texto == texto
    assert redactar(texto).tipos == ()


def test_fragment_identifiers_survive():
    """La respuesta cita fragmentos por su identificador; mutilarlos rompería
    la verificación de anclaje."""
    texto = "según caratula-grupo-meridiano#4"

    assert redactar(texto).texto == texto


# --- forma ---------------------------------------------------------------------


def test_redacting_twice_changes_nothing_the_second_time():
    """Se aplica en más de un sitio a propósito; tiene que ser idempotente."""
    una = redactar("mi RFC es GME180922K41").texto

    assert redactar(una).texto == una


def test_empty_text_does_not_blow_up():
    assert redactar("") == redactar("")
    assert redactar("").texto == ""
    assert not redactar("").hubo
