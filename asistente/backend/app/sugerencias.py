"""Qué ofrecerle preguntar a esta cuenta en concreto.

Antes eran seis frases escritas a mano en el JSX, iguales para todo el mundo.
Medido contra el sistema en marcha: **tres de las seis escalaban** para Comercial
Aurora y dos para Grupo Meridiano. La primera tarjeta preguntaba por la factura
INV-1007, que no es de ninguna cuenta que pueda entrar — o sea que la primera
cosa que la aplicación invitaba a pulsar era un callejón sin salida.

Aquí se construyen del expediente real: su factura vencida, sus documentos. Una
sugerencia que se cumple enseña lo que el asistente hace; una que escala enseña
lo contrario de lo que se quería enseñar.
"""

from __future__ import annotations

import logging

from app.invoices import STATUS_PENDING
from app.titulos import limpiar

logger = logging.getLogger(__name__)

# Las que no dependen de la cuenta y funcionan para cualquiera, porque se apoyan
# en el corpus público que todo cliente tiene autorizado.
GENERALES = [
    "¿Cuántos días tengo de periodo de gracia?",
    "¿Qué cubre mi póliza en transporte de mercancías?",
]

# Cuántas se ofrecen. Más de seis dejan de leerse y se vuelven decoración.
MAXIMO = 6


def para_cliente(facturas: list, documentos: list, empresa: str = "") -> list[str]:
    """Preguntas que esta cuenta puede hacer y obtener respuesta.

    ``facturas`` son las suyas y ``documentos`` los que tiene autorizados, así
    que todo lo que sale de aquí ya pasó el filtro de permisos: no hace falta
    volver a comprobarlo, y no se puede filtrar la existencia de nada ajeno.
    """
    sugerencias: list[str] = []

    if facturas:
        # La pendiente que vence antes: es la que la persona venía a mirar. Una
        # ya pagada como primera sugerencia sería una pregunta sin urgencia.
        pendientes = [f for f in facturas if f.status == STATUS_PENDING]
        destacada = min(pendientes or facturas, key=lambda f: f.due_date)
        sugerencias.append(f"¿Cuál es el estatus de la factura {destacada.id}?")
        sugerencias.append("¿Cuánto debo en total?")

    for documento in documentos[:2]:
        titulo = getattr(documento, "titulo", "") or getattr(documento, "title", "")
        # Sin el nombre de la empresa: quien lee la sugerencia ya está dentro de
        # su cuenta y lo tiene en el encabezado. Basta para que el asistente
        # identifique el documento, porque lo que lo distingue de los demás es
        # "carátula" o "anexo", no el nombre que llevan todos.
        titulo = limpiar(titulo, empresa)
        if titulo:
            sugerencias.append(f"Resume «{titulo}»")

    sugerencias.extend(GENERALES)

    # Sin duplicados y conservando el orden: `dict.fromkeys` mantiene el primero.
    return list(dict.fromkeys(sugerencias))[:MAXIMO]
