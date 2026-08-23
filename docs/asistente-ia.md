# Asistente interno de pólizas y facturación

Diseño de arquitectura y flujo para un asistente que responde dudas frecuentes de pólizas y estado de facturas, con el control de acceso como parte del camino de datos y no como una capa encima.

---

## Decisión

**Azure OpenAI con RAG, orquestado desde un backend propio en Python (FastAPI) y una SPA en React.** No Copilot Studio, por una razón que va más allá del stack solicitado: las dos intenciones principales necesitan *formas de datos distintas*. Las pólizas son documentos y se resuelven con recuperación semántica; el estado de una factura es un dato transaccional, vivo y con permisos por cliente, que se resuelve llamando al sistema de registro.

Meter facturas en un índice vectorial sería un error de diseño doble: el dato quedaría desactualizado y el índice se convertiría en un canal de fuga entre clientes. **RAG para documentos, *function calling* para transacciones.**

---

## 1. Arquitectura y por qué esta

El requisito de usar Python y React ya descarta un enfoque *low-code*, pero conviene justificar la elección por sus méritos, porque en otro escenario respondería distinto.

| Criterio | Copilot Studio | Azure OpenAI + RAG propio |
|---|---|---|
| Tiempo al primer piloto | Días | Semanas |
| Datos transaccionales con permisos | Conectores genéricos, autorización difícil de auditar | Control total: *on-behalf-of* y autorización en el API |
| Guardrails a medida | Los que trae la plataforma | Umbrales, verificación de anclaje y plantillas propias |
| Evaluación en CI | Limitada | Suite versionada junto al código |
| Costo de mantenimiento | Bajo, sin equipo dedicado | Requiere equipo de desarrollo |

**Elegiría Copilot Studio** si el alcance fuera sólo preguntas sobre documentos, sin consultar importes ni estados por cliente, y si no hubiera equipo de desarrollo para sostenerlo. En cuanto entra un dato por cliente con reglas de acceso, el control fino sobre la autorización deja de ser opcional.

Una tercera vía que sí consideraría en producción: **Copilot Studio como canal en Teams**, llamando por HTTP al mismo backend de Python. Se gana la distribución nativa en Microsoft 365 sin ceder el control del camino de datos.

![Arquitectura del asistente](diagramas/01-arquitectura.svg)

**Dos formas de datos, dos caminos.** El camino teal recupera documentos y el ámbar consulta transacciones; las respuestas regresan por la misma arista. Lo que el diagrama afirma es que el modelo nunca habla con el sistema de registro: recibe un contexto ya recortado por permisos, y los importes que aparecen en la respuesta los inserta el código, no el modelo.

### Componentes y para qué está cada uno

| Componente | Rol | Por qué está |
|---|---|---|
| React + MSAL | Interfaz de chat | Renderiza citas como enlaces verificables y captura el pulgar arriba/abajo que alimenta la evaluación |
| FastAPI | Orquestador | Un solo lugar donde viven el ruteo, los guardrails y las llamadas a herramientas: auditable de un vistazo |
| Azure AI Search | Recuperación | Híbrido (BM25 + vectorial) con reranker; los filtros de seguridad se aplican en la consulta |
| Azure OpenAI | Generación y embeddings | Despliegue dedicado, en región con residencia de datos definida |
| AI Content Safety | Escudos | *Prompt Shields* contra inyección y detección de anclaje sobre la respuesta |
| AI Language | Detección de PII | Redacta datos personales antes de que el texto llegue al modelo |
| Key Vault + Managed Identity | Secretos | Misma política que el resto del proyecto: ninguna credencial en configuración de aplicación |
| Application Insights | Telemetría | Traza por turno, con identificadores pseudonimizados |

---

## 2. Flujo de las tres intenciones

El ruteo empieza con un clasificador con umbral de confianza, y **no clasificar es una respuesta válida**: si la confianza no alcanza, la conversación va al humano en lugar de forzar una rama.

![Flujo de las tres intenciones](diagramas/02-flujo-intenciones.svg)

**Todas las fallas drenan al mismo lugar.** Las cajas de borde punteado son decisiones, y ninguna tiene una rama que continúe adivinando: falta de evidencia, respuesta no anclada, falta de permiso, API sin datos y clasificación dudosa terminan en el humano. El fallback no es el caso raro; es el comportamiento por omisión cuando algo no se puede sostener.

### Consulta de póliza — documental

*«¿Mi póliza cubre daños por inundación?»*

1. Recuperación híbrida sobre el índice, **filtrada por los grupos del usuario**.
2. Si el mejor fragmento no supera el umbral de relevancia, no se genera respuesta.
3. Generación con temperatura baja y cita obligatoria de documento y cláusula.
4. Verificación de anclaje: si una afirmación no está sostenida por el contexto, se descarta la respuesta.

**Límite explícito:** el asistente informa qué dice la póliza, nunca dictamina si un siniestro procede. Esa es una decisión humana y el prompt la rechaza.

### Estado de factura — transaccional

*«¿Ya se pagó la factura de marzo?»*

1. Extracción de folio y periodo del mensaje.
2. **Autorización en el API**, no en el prompt: el backend llama con la identidad del usuario.
3. Consulta al sistema de registro; el dato nunca sale de un índice.
4. La respuesta se arma con **plantilla determinista**: el modelo redacta el envoltorio, el código inserta importe, fecha y estado.

**Por qué la plantilla:** un modelo que puede reformular una cifra puede equivocarla. Aquí la exactitud tiene que ser del 100 %, así que se saca del alcance del modelo.

### Contacto humano — fallback

Salida por omisión cuando algo no se sostiene:

- Confianza de clasificación baja, o pregunta fuera de alcance.
- Recuperación sin evidencia suficiente, o respuesta que no pasa el anclaje.
- Falta de permiso, o sistema de registro sin respuesta.
- Dos turnos sin resolver, frustración detectada, o el usuario lo pide.
- Cualquier mención de queja, cancelación o asunto legal: escala de inmediato.

Entrega al agente un resumen estructurado, la transcripción y **lo que ya se intentó**, para que el usuario no repita todo desde el principio.

---

## 3. Datos sensibles, PII y gobierno

El riesgo dominante en un asistente con RAG no es que invente: es que **entregue a una persona un documento que no le corresponde**. Si el índice no filtra por permisos, la recuperación se convierte en un canal de exfiltración perfectamente educado.

![Recorte por permisos en la recuperación](diagramas/03-recorte-permisos.svg)

**La diferencia es una sola arista.** El filtro por ACL en la consulta de recuperación es lo único que separa los dos diseños, y no se puede sustituir con una instrucción en el prompt: al modelo no se le pide que ignore lo que no debe ver, se le impide recibirlo.

| Control | Cómo se aplica |
|---|---|
| **Clasificación primero** | Antes de indexar nada, cada fuente se clasifica. Condiciones generales y FAQ son *internas sin PII*; carátulas de póliza y facturas son *datos personales y financieros*. Sólo el primer grupo entra al índice; el segundo se consulta en vivo. |
| **Recorte en la recuperación** | Cada fragmento se indexa con su etiqueta de acceso, y toda consulta se filtra por los grupos del solicitante. Un fragmento no permitido **no es candidato**, no es un candidato descartado después. |
| **Identidad delegada** | El backend llama al API de facturación con el token del usuario mediante *on-behalf-of*, no con una identidad de servicio con permisos totales. Si el usuario no puede ver la factura, el API responde 403 y el asistente no tiene nada que filtrar. |
| **Minimización** | Al modelo se le envían los campos que la respuesta necesita, no el objeto completo. Un estado de factura requiere folio, importe, fecha y estatus: no la dirección fiscal ni la cuenta bancaria. |
| **Redacción antes del modelo** | El texto del usuario pasa por detección de PII y se enmascara lo que no aporta a la consulta. Reduce lo que se expone y lo que queda en logs. |
| **Telemetría sin PII** | Las trazas guardan identificadores pseudonimizados y métricas, no el texto crudo. Las transcripciones completas viven en un almacén aparte, cifrado, con acceso restringido, retención corta y auditoría de lectura. |
| **Residencia y no entrenamiento** | Despliegue de Azure OpenAI en región definida, con el compromiso contractual de que los *prompts* no se usan para entrenar modelos. Relevante para cumplir la LFPDPPP y para poder responderlo por escrito en una auditoría. |
| **Secretos** | Key Vault con Managed Identity, sin credenciales en configuración de aplicación ni en la imagen. Misma política que el resto del proyecto. |

---

## 4. Guardrails contra alucinación e inyección

### Alucinación

- **Umbral de relevancia.** La palanca más efectiva y la más simple: si la evidencia recuperada no llega al umbral, no se genera respuesta. Decir «no lo encontré, te paso con una persona» es un resultado correcto.
- **Citas obligatorias.** Toda afirmación sobre una póliza se acompaña de documento y cláusula, y la interfaz las muestra como enlaces para que el usuario verifique. Una cita visible es un guardrail social además de técnico.
- **Las cifras no las escribe el modelo.** Importes, fechas y estatus se insertan por código desde la respuesta del API. El modelo redacta alrededor del dato, nunca lo produce.
- **Verificación de anclaje posterior.** Un segundo paso comprueba que cada afirmación esté sostenida por el contexto; si falla, la respuesta se descarta y se escala.
- **Alcance cerrado.** El prompt rechaza dictámenes de cobertura, asesoría legal y cualquier tema fuera de pólizas y facturación.

### Inyección y fuga

- **El documento recuperado es dato, no instrucción.** Separación estricta entre mensaje de sistema, entrada del usuario y contexto. Un PDF de póliza con texto malicioso incrustado no puede reescribir las reglas.
- **Prompt Shields** para detectar intentos de *jailbreak* y de extracción del prompt de sistema.
- **Revisión de la salida** antes de entregarla, buscando datos personales que no correspondan al solicitante.
- **Cuota y detección de anomalías.** Un usuario que consulta cincuenta folios distintos en cinco minutos no está resolviendo una duda; está sondeando.
- **Suite de *red team* en CI** con intentos de *jailbreak*, de extracción de PII y de acceso cruzado entre clientes. Cualquier fallo bloquea el despliegue.

---

## 5. Cómo evaluaría la calidad

Un cambio de prompt es un cambio de código y necesita pruebas de regresión. La base es un **conjunto dorado** de unas 200 preguntas reales tomadas del historial de tickets, etiquetadas por expertos del negocio y balanceadas entre las tres intenciones.

Lo importante es **medir recuperación y generación por separado**: si la recuperación no trae el fragmento correcto, ningún ajuste de prompt lo arregla, y una métrica agregada esconde exactamente eso.

| Capa | Métrica | Qué diagnostica |
|---|---|---|
| Clasificación | Precisión y exhaustividad por intención | Ruteo. Vigilo sobre todo los casos que *debieron* escalar y no lo hicieron |
| Recuperación | recall@k, MRR, nDCG | Calidad del índice, del *chunking* y del reranker |
| Generación | Anclaje, relevancia, completitud | Fidelidad al contexto. Juez automático calibrado contra etiquetas humanas |
| Facturación | Exactitud exacta del dato | Tiene que ser 100 %: el dato viene del sistema de registro |
| Seguridad | Bloqueo de la suite adversaria | Compuerta de liberación, no una métrica más |

### En producción

- **Tasa de resolución sin humano** y tasa de escalamiento, separadas por intención.
- **Proporción de «no lo encontré».** Si sube, el problema está en el corpus, no en el modelo: son huecos de documentación señalados gratis.
- **Pulgar arriba/abajo** y clics en las citas: si nadie abre las fuentes, o no confían o no las necesitan; ambas cosas importan.
- **Revisión humana semanal** de una muestra, priorizando escalamientos y votos negativos. Lo que aparezca ahí se convierte en casos nuevos del conjunto dorado y en documentos nuevos del corpus.

---

## Lo que dejaría fuera del primer alcance

Nada de acciones que escriban: el asistente informa, no cancela pólizas ni aplica pagos. Habilitar escritura cambia el perfil de riesgo por completo y exige confirmación explícita, idempotencia y bitácora de auditoría, igual que el *job* de recordatorios de este mismo repositorio.

Tampoco arrancaría con voz ni con múltiples idiomas, y mediría el costo por conversación desde el primer día: es la variable que decide si el piloto puede crecer.
