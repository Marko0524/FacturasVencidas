# Power Automate frente a Python

Comparación del job de recordatorios de este repositorio contra la misma solución hecha en Power Automate: cuándo conviene *low-code*, cuándo conviene código, y qué se paga en cada caso.

---

## Veredicto

**Python fue la decisión correcta, pero no por goleada, y por una razón específica: el estado por entidad.** No fue la llamada a la API ni las reglas de negocio — eso Power Automate lo hace bien. Fue la idempotencia con garantía *at-least-once* y las reglas de fecha que necesitan pruebas.

Si el requisito hubiera sido «mándale a Operaciones un correo diario con la lista de facturas vencidas», Power Automate ganaba fácil: un digest no necesita estado por factura.

---

## Requisito por requisito

| Requisito | Power Automate | Python | Gana |
|---|---|---|---|
| Disparo programado | Trigger de recurrencia, nativo | Cron externo o Timer Trigger | Power Automate |
| Consumir la API REST | Acción HTTP, sin código | `requests` y manejo manual | Power Automate |
| Reglas de negocio | Condiciones en el diseñador | Funciones puras | Python |
| Descartar registro inválido sin abortar | *Configure run after* y scopes | `try/except` por registro | Python |
| Reintentos selectivos | Política fija, no por código de estado | Política explícita | Python |
| Backoff con *jitter* | No existe | Seis líneas | Python |
| Idempotencia por factura | Lista externa, no atómica | Archivo con escritura atómica | Python |
| Logging | Historial visual por acción | `clave=valor` a stdout | Empate |
| Secretos | Conexiones y conector de Key Vault | Key Vault con Managed Identity | Python |
| Empaquetado | No aplica: la plataforma es el runtime | Dockerfile que mantener | Power Automate |
| Pruebas | No hay framework | Pruebas deterministas | Python |

---

## Los tres lugares donde de verdad se rompe

### 1. La aritmética de fechas se vuelve ilegible

Calcular los días de atraso en Power Automate es una expresión de WDL dentro de un campo del diseñador:

```
div(sub(ticks(utcNow()), ticks(item()?['due_date'])), 864000000000)
```

Contra esto en Python:

```python
(today - invoice.due_date).days
```

La alternativa, `dateDifference()`, devuelve una cadena como `11.00:00:00` que hay que partir con `split()`. No es *imposible* — es **irrevisable**. Y sobre todo: esa expresión no se puede probar. El borde de 10 contra 11 días tiene una prueba dedicada en este repositorio; en el diseñador lo compruebas cambiando datos y ejecutando.

### 2. La idempotencia hay que construirla con las piezas equivocadas

Un flujo es sin estado entre corridas, así que la clave `factura | tipo | fecha` necesita un almacén externo. Con una lista de SharePoint —la opción común— pagas tres cosas:

- Un *Get items* con filtro OData **por notificación**: cinco llamadas extra hoy, quinientas cuando crezca, y ahí topas con los límites de *throttling*.
- **Sin escritura atómica.** El `os.replace()` de este repositorio garantiza que una caída a media escritura no corrompe el estado; una lista no da esa garantía.
- Carreras si dos corridas se traslapan. Se mitiga con el control de concurrencia del trigger en 1, pero es una mitigación, no una garantía.

Con Dataverse mejora —hay claves alternas que imponen unicidad— a cambio de costo de licencia.

### 3. No hay pruebas, y por lo tanto no hay compuerta

Esto es lo decisivo. Las pruebas de este repositorio son deterministas porque `today` se inyecta, no hay red y el `sleep` también es inyectable. En Power Automate un cambio entra en producción cuando alguien le da guardar en el diseñador.

Se puede exportar la solución y versionarla, pero el diff del JSON es ilegible: no revisas un cambio, lo aceptas.

---

## Donde Power Automate gana de verdad

Esta parte importa tanto como la anterior, porque decir «código siempre» no es criterio.

- **Humanos en el ciclo.** Una aprobación, un formulario, una tarjeta adaptativa en Teams. Esto en Python significa construir una aplicación web con autenticación y estado. La diferencia no es de grado, es de categoría.
- **Pegamento entre servicios de Microsoft 365.** SharePoint, Outlook, Excel, Teams: los conectores ya resolvieron la autenticación y la paginación.
- **No hay imagen que mantener.** El contenedor de este proyecto pesa 192 MB y hay que reconstruirlo cuando aparezca un CVE en la base de Debian. Un flujo no tiene esa deuda: la plataforma se parcha sola.
- **Depuración de una corrida concreta.** El historial muestra la entrada y la salida de cada acción con los datos reales. Para entender *por qué esta factura no se notificó*, eso es mejor que leer logs. El intercambio: retención de 28 días, y no se consulta con KQL, así que para observabilidad agregada pierdes.

---

## La regla de decisión

La pregunta que más rápido decide no es técnica: **¿quién es el dueño del cambio?**

Si un analista de negocio tiene que mover el umbral de 10 a 15 días sin levantar un ticket, *low-code* gana y punto. Si el cambio pasa por un PR con revisión, ya estás en el mundo del código, y llevar la lógica al diseñador solo te quita las herramientas.

Después de eso, tres preguntas:

1. **¿Hay garantías que sostener?** Idempotencia, atomicidad, orden. Si sí, código.
2. **¿La lógica necesita pruebas?** Si el negocio tiene bordes —y las fechas siempre los tienen—, código.
3. **¿Va a crecer el volumen?** Los límites de *throttling* llegan antes de lo que uno cree.

Y una de costo que suele olvidarse: la acción HTTP es *premium*, lo cual implica licenciamiento por usuario o por flujo. Un job en contenedor que corre diez segundos al día cuesta centavos.

---

## El híbrido, que es lo que haría en producción

No son excluyentes. La combinación que más rinde: **el job en Python hace la lógica, la idempotencia y las garantías; Power Automate hace la parte humana.**

Cuando una factura pasa de 60 días, el job no decide castigarla: deja un registro y un flujo lanza una aprobación a Finanzas con una tarjeta en Teams.

Cada uno en lo que es bueno — Python en el determinismo, Power Automate en la conversación con las personas.
