"""Nombres cortos para las listas donde se elige algo.

En el historial cada consulta se llamaba igual que la pregunta entera —«Resume
«Carátula de póliza — Grupo Meridiano»»— y en la lista de documentos pasaba lo
mismo. Dos problemas: el verbo con el que se pidió algo no lo identifica, y el
nombre de la empresa está repetido en cada fila de la cuenta de esa empresa,
que además ya lo lleva escrito en el encabezado. Con el ancho de una barra
lateral, lo que se recorta es justo la parte que distingue una fila de otra.

Esto SOLO cambia cómo se nombra algo al elegirlo. La pregunta original se sigue
guardando entera en la base, y las citas y la evidencia siguen mostrando el
título completo del documento: ahí el nombre largo es la referencia exacta, y
acortarlo sería quitar precisión donde precisamente se aporta.
"""

from __future__ import annotations

import re
import unicodedata

# Cómo se pide algo, que no es lo que ese algo es. Van al principio y se quitan.
# El orden importa: las formas largas antes que las cortas, porque "resumen de"
# también empieza por "resumen".
PREFIJOS = (
    "dame un resumen de",
    "hazme un resumen de",
    "necesito un resumen de",
    "quiero un resumen de",
    "un resumen de",
    "resumen de",
    "resumeme",
    "resumelo",
    "resumir",
    "resume",
)

# Comillas y guillemets con los que llega envuelto un título.
COMILLAS = "«»\"'“”‘’"

# Si quitar la empresa deja una de estas colgando al final, se va con ella:
# «el deducible de Grupo Meridiano» no puede quedarse en «el deducible de».
CONECTORES = ("de", "del", "de la", "para", "en")


def limpiar(texto: str, empresa: str = "") -> str:
    """El nombre corto de algo, para una lista.

    Nunca devuelve vacío: si al quitarlo todo no queda nada —alguien preguntó
    literalmente "resume"— se devuelve el texto original, que dice poco pero es
    verdad.
    """
    original = (texto or "").strip()
    if not original:
        return ""

    limpio = _sin_empresa(original, empresa)
    limpio = _sin_prefijo(limpio)
    limpio = _sin_comillas(limpio)
    limpio = _rematar(limpio)

    if not limpio:
        return original
    return limpio[0].upper() + limpio[1:]


def _plano(texto: str) -> str:
    """Sin acentos y en minúsculas, para comparar sin depender de la tilde."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _sin_empresa(texto: str, empresa: str) -> str:
    """Quita el nombre de la empresa y el separador que lo unía a lo demás."""
    empresa = (empresa or "").strip()
    if len(empresa) < 3:
        return texto

    # Se busca sobre el texto sin acentos pero se recorta sobre el original, así
    # que las posiciones tienen que corresponder. Eso solo se cumple si el
    # original está en NFC: en NFD una "ú" ocupa dos posiciones y `_plano` la
    # deja en una, con lo que el índice hallado apuntaría más allá de su sitio y
    # el recorte se comería una letra buena.
    texto = unicodedata.normalize("NFC", texto)
    plano, objetivo = _plano(texto), _plano(empresa)
    inicio = plano.find(objetivo)
    if inicio == -1:
        return texto

    izquierda = texto[:inicio].rstrip()
    derecha = texto[inicio + len(empresa):].lstrip()

    # El separador que quedó a la izquierda pierde su sentido sin lo que unía.
    izquierda = re.sub(r"[\s]*[—–\-·:,]+$", "", izquierda).rstrip()

    # Y un conector que ahora no rige nada tampoco.
    while True:
        for conector in CONECTORES:
            patron = rf"(?i)\s+{re.escape(conector)}$"
            if re.search(patron, izquierda):
                izquierda = re.sub(patron, "", izquierda)
                break
        else:
            break

    return f"{izquierda} {derecha}".strip() if derecha else izquierda


def _sin_prefijo(texto: str) -> str:
    texto = unicodedata.normalize("NFC", texto)
    plano = _plano(texto)
    for prefijo in PREFIJOS:
        if not plano.startswith(prefijo):
            continue
        resto = texto[len(prefijo):].lstrip()
        # Manda el primero que encaje y no se sigue probando. Con "Resúmelo",
        # el prefijo "resumelo" acierta y no deja nada detrás; caer entonces al
        # más corto, "resume", dejaba la fila llamándose "Lo".
        return resto if resto.strip(COMILLAS + " :,-—") else texto
    return texto


def _sin_comillas(texto: str) -> str:
    return texto.strip().strip(COMILLAS).strip()


def _rematar(texto: str) -> str:
    """Espacios de más y puntuación que quedó suelta en los extremos."""
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = re.sub(r"^[\s—–\-·:,]+", "", texto)
    texto = re.sub(r"[\s—–\-·:,]+$", "", texto)
    # "¿Cuál es el deducible ?" tras quitar algo de en medio.
    texto = re.sub(r"\s+([?!.])", r"\1", texto)
    # Un signo de apertura que se quedó sin su pregunta.
    if texto in {"¿", "¡"}:
        return ""
    return texto.strip()
