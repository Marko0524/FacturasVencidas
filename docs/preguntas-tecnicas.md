# Preguntas técnicas conceptuales

Respuestas ancladas en lo que este repositorio implementa, para poder señalar código en lugar de hablar en abstracto.

---

## 1. Webhook, API y polling

**No son tres alternativas del mismo eje.** La API es la interfaz; webhook y polling son dos respuestas a *quién inicia la conversación*. Ambos suelen viajar sobre una API REST.

**Polling** — tú preguntas cada cierto tiempo. Simple, funciona detrás de un firewall, no expone nada, y es resiliente: si estuviste caído, la próxima corrida se pone al día. Cuesta latencia, igual al intervalo, y llamadas desperdiciadas.

**Webhook** — el productor te avisa cuando algo pasa. Casi tiempo real y eficiente, pero exige endpoint público con HTTPS, verificación de firma, manejo idempotente (los duplicados están garantizados) y una estrategia de reintentos con cola de fallidos, porque si estabas caído el evento puede perderse.

**Cuándo cada uno.** Webhook para eventos discretos que importan en segundos: un pago recibido, un ticket creado. Polling para lotes, reconciliación, o cuando el proveedor no ofrece webhooks.

En producción la respuesta suele ser **híbrida**: webhook para la latencia, más un polling periódico de reconciliación como red de seguridad, porque los webhooks se pierden.

> **En este repositorio:** el job es polling por diseño. Corre una vez al día; la latencia no importa y un webhook agregaría infraestructura sin comprar nada.

---

## 2. Idempotencia

**Una operación es idempotente si aplicarla N veces deja el sistema igual que aplicarla una vez.**

Importa al reintentar porque **un timeout no te dice si la operación ocurrió**: se perdió la respuesta, no necesariamente el efecto. Reintentar es obligatorio, y por eso reintentar tiene que ser seguro.

El punto fino: la idempotencia cubre **la acción, no la lectura**. Releer facturas mil veces es inofensivo; enviar el mismo correo mil veces no.

> **En este repositorio:** la clave de idempotencia es `factura | tipo | fecha` ([`app/idempotency.py`](../app/idempotency.py)), es decir, como máximo una notificación de cada tipo por factura por día. Volver a ejecutar el job el mismo día no envía nada; al día siguiente sí vuelve a recordar, que es el punto de un proceso de cobranza.
>
> Hay un *trade-off* explícito: la clave se marca **después** del envío exitoso, lo que da garantía *at-least-once* — una caída entre enviar y marcar podría duplicar. Marcar antes daría *at-most-once* y podría perder la notificación. Elegí duplicar antes que perder, porque un recordatorio repetido molesta y uno faltante cuesta dinero.

En HTTP esto ya está en la semántica: `GET`, `PUT` y `DELETE` son idempotentes, `POST` no. Por eso las APIs de pago exigen una cabecera `Idempotency-Key`.

---

## 3. ROI de una automatización

**ROI = (beneficio anual − costo anual) / costo anual**, acompañado del periodo de recuperación. Lo difícil no es la fórmula: es ser honesto con las dos partes.

**Beneficio**, en orden creciente de valor:

1. Horas liberadas × costo cargado por hora. *Liberadas* no es *ahorradas* a menos que se reasignen a algo medible.
2. Reducción de errores × costo por error.
3. **Efecto de ciclo**, que en cobranza suele ser el componente grande: cobrar cinco días antes mejora el flujo de caja. Bajar el DSO vale más que las horas ahorradas.

**Costo** — desarrollo, infraestructura, el costo esperado de las fallas, y el que casi siempre se omite: **mantenimiento**, entre 15 % y 25 % anual del costo de desarrollo.

Lo que hace creíble el número es **medir la línea base antes de automatizar**. Sin datos del proceso manual, el ROI es una narrativa, no un cálculo. Métricas útiles: horas por semana dedicadas al seguimiento, tasa de error, DSO, porcentaje de facturas cobradas a tiempo.

Un ROI negativo también es un resultado válido: automatizar algo que ocurre dos veces al año no se paga nunca.

---

## 4. Consumir una API REST desde Power Automate y desde Python

| | Power Automate | Python |
|---|---|---|
| Autenticación | La conexión gestiona OAuth | Explícita; JWT bearer para servidor a servidor |
| Reintentos | Política básica del conector | Control total: selectivos, con backoff y jitter |
| Pruebas | Difíciles de automatizar | Suite determinista en CI |
| Versionado | Exportar solución; el diff es ilegible | Git normal |
| Velocidad inicial | Horas | Días |

La diferencia que más importa no es técnica, es de **gobierno**: en Power Automate la credencial vive en una *conexión* que pertenece a un usuario. Si esa persona deja la empresa, el flujo se rompe — de ahí la práctica de usar cuenta de servicio. En Python la credencial sale de Key Vault con Managed Identity y no pertenece a nadie.

**Cuándo cada uno.** Power Automate para integraciones ligeras con humanos en el ciclo: aprobaciones, notificaciones, todo dentro de Microsoft 365. Python cuando hay lógica de negocio real, volumen, o necesidad de probar y desplegar con control.

> **En este repositorio:** este job en Power Automate sería frágil. La idempotencia habría que emularla con una lista de SharePoint, y los reintentos selectivos —429 sí, 404 no— no se expresan bien en un lienzo.

---

## 5. Secretos y credenciales: Key Vault frente a variables de entorno

**Tampoco son alternativas del mismo nivel.** La variable de entorno es el *mecanismo de entrega*; Key Vault es el *almacén*. La pregunta real es de dónde sale el valor.

Con solo variables de entorno, el secreto vive en la configuración de la aplicación o en un archivo: rotación manual, sin auditoría de quién lo leyó, y visible para cualquiera con acceso a esa configuración.

Key Vault aporta cuatro cosas concretas: **rotación sin redeploy**, auditoría de cada lectura, RBAC granular y versionado del secreto. Combinado con **Managed Identity** desaparece el problema del arranque — no hace falta un secreto para obtener otro secreto: la aplicación se autentica por identidad.

El criterio, en una frase: **si rotar un secreto exige un despliegue, en la práctica nadie lo rota.**

> **En este repositorio:** [`app/config.py`](../app/config.py) es el único módulo que llama a `os.getenv`, así la superficie de configuración se audita de un vistazo. Los defaults son inofensivos para que corra sin `.env`; `.env` está en `.gitignore` y `.dockerignore`; la imagen no contiene secretos y corre como usuario no privilegiado. En producción el token vendría de Key Vault referenciado desde la configuración: el secreto sigue *llegando* como variable de entorno, pero su origen es rotable y auditable.

---

## 6. Alucinaciones y fuga de datos en un asistente interno

Son **dos problemas distintos**, y conviene separarlos porque las defensas no se parecen. El diseño completo, con diagramas, está en [`asistente-ia.md`](asistente-ia.md).

### Contra alucinación

- **Umbral de relevancia.** La palanca más efectiva y la más simple: si la evidencia recuperada no llega al umbral, no se genera respuesta. «No lo encontré, te paso con una persona» es un resultado correcto — y hay que *medir* su frecuencia, porque si sube, faltan documentos, no falla el modelo.
- **Citas obligatorias**, visibles como enlaces para que el usuario verifique.
- **Las cifras no las escribe el modelo.** Importes, fechas y estatus los inserta el código con plantilla determinista. Un modelo que puede reformular una cifra puede equivocarla.
- **Verificación de anclaje** posterior a la generación, y alcance cerrado en el prompt.

### Contra fuga

- **El recorte por permisos vive en la recuperación, no en el prompt.** Al modelo no se le pide que ignore lo que no debe ver: se le impide recibirlo. Un fragmento no autorizado no es un candidato descartado — no es candidato.
- **Identidad delegada** hacia los sistemas de registro, no un *service principal* con permisos totales.
- **El documento recuperado es dato, no instrucción**, que es lo que contiene la inyección indirecta desde un PDF.
- Minimización de campos, revisión de la salida y telemetría sin PII.

---

## 7. Versionado, ramas y CI/CD

**Ramas: *trunk-based* con ramas cortas y PR obligatorio, no GitFlow.** GitFlow tiene sentido cuando se empaquetan releases y se da soporte a versiones viejas; para un job de despliegue continuo es sobrecarga. `main` protegido: PR, checks en verde, sin *force push*.

**Versionado:** SemVer donde hay un contrato que romper — una librería, una API. Para un job desplegable, lo que se versiona de verdad es la **imagen etiquetada con el SHA del commit**, nunca `latest`: es lo que da trazabilidad, y convierte el rollback en volver a la etiqueta anterior. Conventional Commits para que el changelog se genere solo.

**CI en cada PR:** lint, tipos, pruebas con cobertura, build de la imagen, y dos escaneos que suelen faltar — vulnerabilidades de la imagen y secretos filtrados en el diff.

**CD:** despliegue automático a staging, a producción con aprobación.

Dos cosas que se olvidan:

- **Los prompts y el índice son artefactos versionados.** Un cambio de prompt es un cambio de código y necesita pruebas de regresión: la suite de evaluación corre en CI, y la suite adversaria de seguridad es una compuerta de liberación, no una métrica más.
- **El formato del estado necesita compatibilidad hacia atrás.** Por eso el archivo de idempotencia lleva un campo `version`: si mañana cambia el esquema, la corrida vieja no se rompe.

> **En este repositorio:** las pruebas son deterministas —`today` se inyecta, no hay red y el `sleep` también es inyectable—, que es la condición para que sirvan como compuerta en CI. El estado se escribe de forma atómica y lleva su número de versión.
