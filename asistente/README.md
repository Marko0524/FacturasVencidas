# Asistente interno de pólizas y facturación — bloque 3

Implementación del diseño descrito en [`docs/asistente-ia.md`](../docs/asistente-ia.md): un asistente que responde dudas de pólizas con RAG, consulta el estado de facturas contra el sistema de registro, y escala a un humano en cuanto algo no encaja.

Los usuarios entran con correo y contraseña contra la tabla de usuarios, **suben sus propios documentos** en PDF, Markdown o texto, y le preguntan al chat sobre ellos. Los vectores viven en **Postgres con pgvector**, igual que las conversaciones y los casos escalados.

**Funciona con Azure OpenAI, con Vertex AI y con Gemini**, elegido por configuración. Y con un proveedor falso que no necesita red ni llaves, para que el proyecto arranque en un clon nuevo.

---

## 1. Qué demuestra

Las tres decisiones del diseño, hechas código y con pruebas que fallan si alguien las revierte:

| Decisión | Dónde vive | Prueba que la sostiene |
|---|---|---|
| El recorte por permisos ocurre en la **recuperación**, no en el prompt | `store.py` · el predicado va en el `WHERE` de la consulta que ordena por distancia | `test_the_database_never_ranks_another_customers_fragment` |
| **Nada se afirma sin evidencia** | `guardrails.py` · citar un fragmento no recuperado descarta la respuesta | `test_an_answer_citing_an_invented_fragment_is_refused` |
| Las **cifras no las escribe el modelo** | `invoices.py` · plantillas deterministas | `test_an_overdue_invoice_reports_the_days_and_the_figures` |
| Dos casos distintos se leen **idénticos** cuando distinguirlos filtraría algo | `assistant.py` · factura ajena y factura inexistente | `test_an_invoice_that_does_not_exist_reads_the_same_as_one_you_may_not_see` |

El modelo decide *de qué* se está hablando. Los importes, las fechas, el estatus, la fecha de hoy y la lista de capacidades los inserta el código.

**Un matiz que cambió al usarlo, y que el diseño original no tenía:** no toda rama negativa escala. Cuando lo que falta es un dato que quien pregunta tiene delante —qué documento resumir, una palabra que acote la búsqueda, el folio correcto— el asistente lo pide y ofrece la salida humana como un botón. Escalar eso gastaba un traspaso antes de saber si hacía falta. Lo que sigue yendo directo a una persona: lo que está fuera de alcance, la inyección, el proveedor caído y el modelo que devuelve algo malformado.

---

## 2. Arquitectura

```text
                    pregunta + identidad autenticada
                                 ↓
                          assistant.py          clasifica la intención
                                 ↓
            ┌────────────────────┼────────────────────┐
            ↓                    ↓                    ↓
          POLIZA              FACTURA               HUMANO
            ↓                    ↓                    ↓
       store.py             invoices.py          escalamiento
   pgvector: el WHERE     consulta al sistema    (mensaje único)
   recorta ANTES de         de registro
   ordenar por distancia   → plantilla
            ↓                determinista
      guardrails.py                ↓
   verificación de citas           ↓
            ↓                      ↓
            └──────────→ respuesta ←┘
                     o escalamiento
```

| Módulo | Responsabilidad |
|---|---|
| `config.py` | Único lector del entorno; umbrales por proveedor |
| `providers/` | Azure OpenAI, Gemini, Vertex AI y el doble falso tras una interfaz de dos métodos |
| `retrieval.py` | Corpus del repositorio, troceado y búsqueda en memoria |
| `store.py` | **Postgres + pgvector**: documentos, fragmentos y el filtro de permisos en SQL |
| `ingest.py` | Validación y troceado de lo que suben los usuarios |
| `invoices.py` | Consulta transaccional y redacción determinista |
| `guardrails.py` | Verificación de anclaje, detección de inyección, límites de entrada |
| `assistant.py` | Orquestación y escalamiento |
| `api.py` | FastAPI: preguntas, documentos y la identidad del cliente |

### Por qué las facturas no están en el índice vectorial

Porque son **datos vivos con permisos por cliente**. Indexarlas haría dos daños a la vez: congelar una cifra que cambia a diario, y convertir el índice en un canal de fuga entre clientes. Las pólizas son documentos y se resuelven con recuperación semántica; el estado de una factura se resuelve preguntándole al sistema de registro.

El asistente lee **el mismo `sample_data/invoices.json` que el job de recordatorios**, así que lo que reporta es exactamente aquello sobre lo que el job actuó.

### El permiso, como cláusula SQL

Aquí es donde la afirmación central del diseño deja de ser una promesa. La consulta que ordena por distancia lleva el predicado de permisos dentro:

```sql
SELECT d.nombre, d.titulo, f.texto,
       1 - (f.embedding <=> %s::vector) AS similitud
  FROM fragmentos f
  JOIN documentos d ON d.id = f.documento_id
 WHERE (d.alcance = 'publico'
        OR (d.alcance = 'cliente' AND lower(d.cliente) = lower(%s)))
 ORDER BY f.embedding <=> %s::vector
 LIMIT %s
```

Un fragmento no autorizado no se recupera y luego se descarta: **la base nunca lo puntúa**. No hay camino de código que pueda olvidarse de filtrar, porque filtrar y ordenar son la misma consulta.

La restricción también vive en el esquema: `CHECK (alcance <> 'cliente' OR cliente <> '')`. Un documento de cliente sin dueño no sería autorizable, y la base lo rechaza en vez de confiar en que la aplicación se acuerde.

### Dos decisiones del almacén, dichas y no escondidas

- **Sin índice ANN.** HNSW e IVFFlat de pgvector topan en 2000 dimensiones y `gemini-embedding-001` devuelve 3072, así que el barrido exacto es lo que cabe. Con unos cientos de fragmentos también es más rápido. A escala de corpus, se reduce la dimensionalidad de salida o se pasa a `halfvec`.
- **La columna se dimensiona en la primera ingesta.** Los proveedores no coinciden (3072, 1536, 768) y una columna no puede ser polimórfica, así que la dimensión se registra y una mezcla se rechaza en voz alta en lugar de producir distancias silenciosamente absurdas.

---

## 3. Puesta en marcha

### Base de datos

```bash
cd asistente
docker compose up -d
```

Levanta Postgres 17 con pgvector. Los datos viven en un volumen con nombre: sin él, cada `down` borraría los documentos cargados y habría que volver a pagar los embeddings de todo.

Sin base de datos el asistente también arranca — usa el índice en memoria sobre el corpus del repositorio — pero **la carga de documentos queda deshabilitada**, y la interfaz lo dice en lugar de fallar al intentarlo.

### Backend

```bash
cd asistente/backend
pip install -r requirements.txt
python -m uvicorn app.api:app --reload --port 8000
```

Arranca sin configuración: `LLM_PROVIDER` es `fake` y `RETRIEVAL_BACKEND` es `memoria` por omisión, así que no hace falta llave, red ni base de datos.

Para usar el almacén vectorial, las cargas y el acceso con contraseña:

```bash
RETRIEVAL_BACKEND=postgres AUTH_MODE=local SESSION_SECRET=local   python -m uvicorn app.api:app --reload --port 8000
```

En el primer arranque siembra el corpus del repositorio en Postgres. Es idempotente **por documento**: un reinicio no vuelve a embeber lo que ya esté ahí, pero sí indexa lo que se haya añadido, porque los embeddings cuestan dinero y el corpus sí cambia cuando alguien mete un archivo nuevo.

También siembra las cuatro cuentas de demostración, todas con la contraseña `asistente2026` —o la que pongas en `SEED_PASSWORD`—:

| Cuenta | Cliente |
|---|---|
| `pagos@aurora.mx` | Comercial Aurora |
| `finanzas@meridiano.mx` | Grupo Meridiano |
| `kayelo3614@neowd.com` | Logistica Pacifico |
| `contabilidad@zenit.mx` | Constructora Zenit |

Entrar con dos de ellas en ventanas distintas es la forma más rápida de comprobar el aislamiento: los documentos, las facturas, las conversaciones y los casos escalados de una no existen para la otra.

`SESSION_SECRET` firma los tokens. Sin él se genera uno por proceso y las sesiones no sobreviven a un reinicio; en Cloud Run directamente no arranca.

### Frontend

```bash
cd asistente/frontend
npm install
npm run dev
```

Abre <http://localhost:5173>. Vite proxea `/api` al backend, así que el navegador habla con un solo origen.

### Con un proveedor real

```bash
cp asistente/.env.example asistente/.env    # y rellena lo que vayas a usar
```

`asistente/.env` está ignorado por Git. Se carga al arrancar y **el entorno real siempre gana**, así que `LLM_PROVIDER=gemini python -m uvicorn ...` sigue funcionando como override puntual.

| Proveedor | Qué necesitas |
|---|---|
| **Gemini** | `GEMINI_API_KEY` de [AI Studio](https://aistudio.google.com/apikey). El nivel gratuito basta |
| **Vertex AI** | `GOOGLE_APPLICATION_CREDENTIALS` apuntando al JSON de una cuenta de servicio |
| **Azure OpenAI** | Endpoint, llave y el **nombre del deployment** de chat y de embeddings |

En Azure el *deployment* es el nombre que le pusiste tú al publicar el modelo, no el nombre del modelo. Por eso es configuración y no aparece en el código.

**Vertex AI existe porque una política real lo exigió.** El proyecto corporativo donde se probó esto prohíbe las claves de API: *"La política de seguridad de tu organización no permite las claves de API. Usa las credenciales predeterminadas de la aplicación (ADC) en su lugar."* Es el mismo razonamiento que el documento de diseño da para Key Vault y Managed Identity — una llave de larga vida que se puede copiar de una laptop es justo lo que esa política evita. El canal usa un token OAuth2 de vida corta, renovado al expirar, y toma el proyecto del propio JSON.

**Las credenciales de Vertex son opcionales donde hay identidad ambiental.** Si no se configura ninguna ruta, se usan las credenciales por defecto — que es como corre en Cloud Run, con la cuenta de servicio adjunta y ninguna llave dentro de la imagen. Configurar una ruta que no existe sigue siendo un error ruidoso: caer ahí en la identidad del entorno silenciaría la errata para acabar hablando con Vertex como otra cuenta.

**Los identificadores de modelo caducan.** `gemini-2.5-flash` responde hoy con 404 a cuentas nuevas, nombrando su reemplazo, y `text-embedding-004` ya no está en AI Studio. `GET /v1beta/models` lista lo que una llave concreta alcanza. Por eso los modelos son configuración y no constantes enterradas en el proveedor.

---

## 4. Pruebas

```bash
cd asistente/backend
pytest
```

341 pruebas. Ninguna sale a la red: el proveedor falso las hace deterministas.

| Archivo | Cubre |
|---|---|
| `test_providers.py` | Reintentos —incluida la conexión caída—, `Retry-After`, forma de cada petición, respuestas degeneradas, credenciales por archivo o por identidad adjunta |
| `test_conversaciones.py` | Memoria por cuenta, de qué documento se habla, valoraciones, historial |
| `test_usuarios.py` | `scrypt` con sal por usuario, comparación en tiempo constante, correo desconocido indistinguible de contraseña mala |
| `test_assistant.py` | Ruteo de las intenciones, ramas que escalan y ramas que preguntan |
| `test_ingest.py` | Troceado de cargas, tipos y tamaños, travesía de rutas, binarios renombrados |
| `test_guardrails.py` | Citas inventadas, JSON en bloque de código, inyección, y «no lo encontré» separado del modelo que se porta mal |
| `test_pii.py` | Redacción antes del prompt, y que **no se coma** el folio ni los importes |
| `test_auth.py` | Firma y expiración del token de sesión |
| `test_resolver.py` | Qué documento nombra una pregunta, y cuándo preferir preguntar |
| `test_titulos.py` | Nombres cortos sin romper la frase al quitar el de la empresa |
| `test_store.py` | **pgvector**: el `WHERE` de permisos, reemplazo al resubir, borrado acotado al dueño |
| `test_retrieval.py` | Troceado, front matter, **visibilidad por cliente**, piso de similitud, orden |
| `test_invoices.py` | Consulta acotada al cliente, plantillas, singular/plural, registro corrupto |
| `test_contexto.py` | La fecha y la cuenta las escribe el código, con un proveedor que revienta si se le pide redactar |
| `test_resumen.py` | Sin referente se pregunta, no se adivina ni se traspasa |
| `test_escalamientos.py` | El caso se guarda; el folio no es una credencial |
| `test_sugerencias.py` | Salen del expediente de la cuenta, no de una lista fija |

Las de `test_store.py`, `test_conversaciones.py`, `test_escalamientos.py` y `test_usuarios.py` son de integración: necesitan la base de `docker-compose.yml` y **se saltan, diciéndolo, cuando no está**. Usan `asistente_test`, otra base, para no tocar los datos de desarrollo. Falsear un almacén vectorial probaría el falso, y lo que se verifica aquí es justamente que *la base de datos* se niega a puntuar lo que no corresponde.

Las suites de los bloques 2 y 3 se ejecutan por separado, cada una desde su directorio: ambas tienen un paquete `app` y dependencias distintas.

---

## 5. Sobre el proveedor falso

No es un modelo: es un doble de pruebas. Clasifica por palabras clave y sus embeddings solo cuentan palabras compartidas, una dimensión por palabra distinta.

Eso basta para ejercitar **el cableado** — ruteo, permisos, anclaje, escalamiento — pero no la calidad de la recuperación. Un modelo entrenado entiende que "cuánto tengo que desembolsar" y "deducible" hablan de lo mismo; una bolsa de palabras no.

Por eso **el piso de similitud es por proveedor**: la similitud coseno no es una escala universal. Dos embeddings entrenados colocan textos relacionados alrededor de 0.7–0.9; la bolsa de palabras llega a 0.27 en su mejor acierto. Una sola constante o amordazaría a los proveedores reales, o dejaría pasar todo en el falso.

---

## 6. Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/api/salud` | Proveedor, almacén, si las cargas están disponibles y si quedó listo |
| `GET` | `/api/clientes` | Identidades de demostración que ofrece la UI |
| `POST` | `/api/acceso` | Correo y contraseña contra la tabla de usuarios → token de sesión |
| `GET` | `/api/sesion` | Quién eres y qué cuenta puedes leer |
| `POST` | `/api/preguntar` | `{"pregunta": "..."}` → respuesta, intención, fuentes, escalado |
| `POST` | `/api/preguntar/flujo` | Lo mismo, contando por SSE en qué etapa va |
| `GET` | `/api/sugerencias` | Preguntas construidas con el expediente de **esta** cuenta |
| `GET` | `/api/documentos` | Lo que este cliente puede consultar, y nada más |
| `POST` | `/api/documentos` | Sube un PDF, `.md` o `.txt`, lo trocea, lo embebe y lo indexa |
| `GET` | `/api/documentos/{nombre}/archivo` | Descarga el original **propio** |
| `DELETE` | `/api/documentos/{nombre}` | Borra un documento **propio** |
| `GET` | `/api/conversaciones` | Sus conversaciones, la más reciente primero |
| `GET` `DELETE` | `/api/conversacion/{id}` | Reabrirla entera, u olvidarla |
| `POST` | `/api/valoracion` | Si una respuesta sirvió, y por qué no |
| `GET` | `/api/escalamientos` | Sus casos escalados |
| `POST` | `/api/escalamientos/{folio}/contacto` | Cómo prefiere que le contacten |

La identidad del cliente sale del **token de sesión**, nunca del cuerpo de la petición. Un cliente que pudiera nombrar su propio identificador podría nombrar el de otro. El token demuestra quién entró; los permisos se releen de la base en cada petición, así que desactivar una cuenta surte efecto sin esperar a que caduque la sesión. En producción el token local se sustituye por el validado de Entra ID; el resto del código no se entera.

**El alcance de una carga no lo elige quien sube.** Todo documento cargado entra con `alcance=cliente` y la dirección del autenticado como dueño. No es un valor por omisión que alguien pueda olvidarse de poner: el cliente nunca tiene ocasión de proponerlo, porque un alcance elegido por quien sube es un alcance que un atacante también puede elegir.

Borrar y consultar un documento inexistente responden **igual** que hacerlo con uno ajeno. Distinguirlos confirmaría la existencia del documento de otro cliente.

---

## 7. Despliegue

`Dockerfile` multietapa: Node compila la interfaz, Python la sirve junto a la API. Un solo contenedor y un solo origen, así que en producción no hace falta CORS ni hay forma de publicar una mitad y olvidar la otra.

```bash
./desplegar.sh <id-del-proyecto>       # Cloud Run + Cloud SQL, idempotente
```

Dos cosas que el guion hace a propósito: **ninguna llave viaja en la imagen** —la identidad la da la cuenta de servicio adjunta— y el servicio queda **privado**, accesible por `gcloud run services proxy`. Y una que impide: sin `SESSION_SECRET` no arranca en Cloud Run, porque con varias instancias cada una firmaría con una clave distinta y las sesiones se caerían al azar sin dejar rastro en ningún log.

---

## 8. Límites conocidos

- **Se admiten PDF, `.md` y `.txt`; nada más.** Un DOCX necesitaría otra dependencia. La validación rechaza lo demás en lugar de indexar basura: un binario renombrado a `.md` se detecta por sus bytes de control, no por la extensión.
- **La búsqueda es un barrido exacto, sin índice ANN.** Correcta y suficiente con cientos de fragmentos; con cientos de miles hace falta reducir dimensiones o pasar a `halfvec` para poder indexar.
- **Los documentos cargados no se versionan.** Volver a subir el mismo archivo reemplaza los fragmentos anteriores; no queda historial de lo que decía antes.
- **La verificación de anclaje comprueba la cita, no el razonamiento.** Que un fragmento exista no demuestra que la frase se siga de él. Es una afirmación barata y verificable que encarece inventar, y falla cerrando.
- **La detección de inyección es un cable trampa, no una frontera.** Una reescritura decidida pasa cualquier lista de patrones. La frontera real es que los documentos ajenos nunca entran al prompt y que las cifras no las escribe el modelo.
- **La redacción de PII reconoce formatos, no nombres.** Acierta con RFC, CURP, tarjeta —con Luhn, no solo por longitud—, CLABE, correo y teléfono, porque tienen forma. Un nombre o una dirección necesitan reconocimiento de entidades; en Azure sería AI Language.
- **La redacción vale de aquí en adelante.** Lo guardado antes de activarla conserva lo que se escribió entonces; encenderla sobre un historial existente exige además limpiarlo o acortar la retención.
- **Sin cuota por usuario ni detección de sondeo.** Cincuenta folios distintos en cinco minutos no son una duda, y hoy nada lo nota.
- **Sin evaluación automatizada de calidad.** La sección 5 del diseño propone el conjunto dorado y las métricas. Lo que sí existe es la señal que la alimentaría: la valoración por respuesta, con el porqué cuando es negativa.
- **La memoria recuerda seis turnos.** Suficiente para resolver un pronombre; una conversación larga pierde el principio.
- **El resumen recorre hasta 24 fragmentos.** Un documento más largo se resume por partes, y la respuesta lo dice en vez de fingir que está completo.
