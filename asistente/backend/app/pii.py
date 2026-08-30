"""Redacción de datos personales antes de que el texto llegue al modelo.

El resto de controles impide que **salga** un documento que no corresponde. Este
impide que **entre** al prompt un dato personal que nadie necesitaba enviar.
Son problemas distintos: alguien que escribe "mi tarjeta es 4111 1111 1111 1111,
¿me cubre el seguro?" no está atacando nada, está siendo normal, y ese número
acabaría viajando a un proveedor externo y quedándose en trazas y en la memoria
de la conversación sin que hiciera falta para responderle.

**Se redacta lo que escribe la persona, no la documentación recuperada.** Los
documentos son suyos y tiene derecho a leerlos: un RFC en su propia carátula es
justamente lo que vino a consultar. Redactar la evidencia rompería las
respuestas sin proteger a nadie de nada.

Lo que esto NO es: un detector de nombres, direcciones o de PII no estructurada.
Reconoce identificadores con forma —RFC, CURP, tarjetas, CLABE, correos,
teléfonos— porque tienen una y se pueden acertar sin un modelo. Para lo demás
haría falta un servicio de reconocimiento de entidades; en Azure sería AI
Language, y está anotado como pendiente en el README.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Identificadores del negocio que NO son datos personales y que la aplicación
# necesita intactos: el folio de factura es lo que se busca en la cuenta, y el
# número de póliza aparece en las carátulas.
#
# Van primero y se apartan del texto antes de buscar nada, porque varios de sus
# formatos son indistinguibles de un teléfono o de una CLABE si se miran solos.
# Comerse un folio en la redacción rompería la consulta de facturas en silencio:
# la pregunta llegaría sin número y se respondería con el resumen de la cuenta,
# que parece una respuesta razonable y no lo es.
NEGOCIO = re.compile(r"\b(?:INV[-\s]?\d{3,}|POL[-A-Z0-9]{4,})\b", re.IGNORECASE)

MARCA = "\x00%d\x00"

# Cada patrón con el nombre que se registra. El nombre viaja al log; el valor
# nunca, que es el sentido de todo esto.
PATRONES: tuple[tuple[str, re.Pattern], ...] = (
    # La CURP va antes que el RFC: sus primeros doce caracteres tienen la misma
    # forma que un RFC de persona física, así que buscar el RFC primero partiría
    # la CURP en dos y dejaría media a la vista.
    ("CURP", re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b")),
    ("RFC", re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b")),
    ("correo", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("CLABE", re.compile(r"\b\d{18}\b")),
    # Separadores: espacio y guion, nunca punto ni coma. Un importe como
    # "118,400.00" tiene la longitud de un teléfono, y admitir sus separadores
    # convertiría cada cifra de una póliza en un dato personal.
    ("teléfono", re.compile(r"(?<!\d)(?:\+?52[\s-]?)?(?:\d[\s-]?){9}\d(?!\d)")),
)

# Las tarjetas se tratan aparte porque la longitud no basta para reconocerlas.
TARJETA = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


@dataclass(frozen=True)
class Redaccion:
    """El texto ya limpio y qué tipos aparecían en él, sin sus valores."""

    texto: str
    tipos: tuple[str, ...]

    @property
    def hubo(self) -> bool:
        return bool(self.tipos)


def redactar(texto: str) -> Redaccion:
    """Sustituye los identificadores personales por su etiqueta.

    Se sustituye por «[RFC]» y no se borra: quitarlo dejaría la frase coja y el
    modelo intentaría rellenar el hueco. Con la etiqueta a la vista, la pregunta
    sigue leyéndose y queda claro que ahí había un dato que no se envió.
    """
    if not texto:
        return Redaccion("", ())

    limpio, guardados = _apartar_negocio(texto)
    tipos: list[str] = []

    limpio, hubo_tarjeta = _redactar_tarjetas(limpio)
    if hubo_tarjeta:
        tipos.append("tarjeta")

    for nombre, patron in PATRONES:
        limpio, cuantos = patron.subn(f"[{nombre}]", limpio)
        if cuantos:
            tipos.append(nombre)

    return Redaccion(_restaurar_negocio(limpio, guardados), tuple(tipos))


def _apartar_negocio(texto: str) -> tuple[str, list[str]]:
    guardados: list[str] = []

    def cambiar(m: re.Match) -> str:
        guardados.append(m.group(0))
        return MARCA % (len(guardados) - 1)

    return NEGOCIO.sub(cambiar, texto), guardados


def _restaurar_negocio(texto: str, guardados: list[str]) -> str:
    for i, original in enumerate(guardados):
        texto = texto.replace(MARCA % i, original)
    return texto


def _redactar_tarjetas(texto: str) -> tuple[str, bool]:
    """Redacta lo que pasa Luhn, y deja lo demás.

    La longitud sola daría demasiados falsos positivos: una suma asegurada de
    dieciséis dígitos no es una tarjeta. Luhn es la comprobación que los propios
    emisores usan, cuesta nada, y equivocarse hacia el lado de no redactar un
    número que no es una tarjeta es preferible a mutilar cifras de una póliza.
    """
    encontrada = False

    def cambiar(m: re.Match) -> str:
        nonlocal encontrada
        digitos = re.sub(r"\D", "", m.group(0))
        if not (13 <= len(digitos) <= 19) or not _luhn(digitos):
            return m.group(0)
        encontrada = True
        return "[tarjeta]"

    return TARJETA.sub(cambiar, texto), encontrada


def _luhn(digitos: str) -> bool:
    total = 0
    for posicion, caracter in enumerate(reversed(digitos)):
        valor = int(caracter)
        if posicion % 2 == 1:
            valor *= 2
            if valor > 9:
                valor -= 9
        total += valor
    return total % 10 == 0
