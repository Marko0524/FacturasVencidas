/* ============================================================================
   Bloque 5 - Consultas SQL
   ============================================================================

   Dialecto: T-SQL (Azure SQL / SQL Server). Validadas ejecutándolas contra
   SQL Server 2019. Al final del archivo hay un juego de datos de prueba con
   los casos límite, para que cualquiera pueda reproducir los resultados.

   Esquema asumido -- el enunciado nombra la tabla Facturas y pide un JOIN,
   así que asumo una tabla de clientes. Los campos son los mismos que maneja
   el job de Python:

       Clientes (ClienteId, Nombre, Correo)
       Facturas (FacturaId, ClienteId, Monto, Moneda, FechaVencimiento, Estatus)

   Tres decisiones que aplican a las dos consultas:

   1. Sólo cuentan las facturas 'pending'. Una factura pagada hace meses sigue
      teniendo la fecha de vencimiento en el pasado; sin este filtro aparecería
      como vencida. Es el error más fácil de cometer aquí.

   2. El filtro va sobre la columna sin envolverla en una función:
      FechaVencimiento < DATEADD(...) en lugar de DATEDIFF(...) > 10. Ambas dan
      el mismo resultado, pero la segunda anula el uso del índice y fuerza un
      recorrido completo de la tabla. El DATEDIFF se queda en el SELECT, donde
      es para mostrar y no para filtrar.

   3. @Hoy es un parámetro, no GETDATE() incrustado en el WHERE. Así el
      resultado de cualquier día es reproducible -- la misma razón por la que
      en el código Python la fecha de hoy se inyecta en lugar de leerse del
      reloj.
   ============================================================================ */


/* ----------------------------------------------------------------------------
   CONSULTA 1
   Facturas con más de 10 días de atraso.

   "Más de 10" es estrictamente mayor: una factura con exactamente 10 días NO
   aparece. Es la misma regla que aplica el job (atraso > umbral), así que SQL
   y aplicación no se contradicen.
   ---------------------------------------------------------------------------- */

DECLARE @Hoy DATE = CAST(GETDATE() AS DATE);

SELECT
    f.FacturaId,
    c.Nombre                                 AS Cliente,
    f.Monto,
    f.Moneda,
    f.FechaVencimiento,
    DATEDIFF(DAY, f.FechaVencimiento, @Hoy)  AS DiasAtraso
FROM dbo.Facturas AS f
JOIN dbo.Clientes AS c ON c.ClienteId = f.ClienteId
WHERE f.Estatus = 'pending'
  AND f.FechaVencimiento < DATEADD(DAY, -10, @Hoy)
ORDER BY DiasAtraso DESC, f.FacturaId;
GO


/* ----------------------------------------------------------------------------
   CONSULTA 2
   Por cliente: saldo total vencido y promedio de días de atraso, mostrando
   sólo los clientes cuyo saldo vencido supera un umbral.

   Aquí "vencido" es cualquier factura pasada de su fecha (atraso > 0), no sólo
   las de más de 10 días: el umbral de 10 es la regla de escalamiento a
   Operaciones, no la definición de vencido. Para restringirla a más de 10 días
   basta cambiar el WHERE por el de la Consulta 1.

   El CAST del promedio no es decorativo: DATEDIFF devuelve entero y AVG sobre
   enteros hace división entera en SQL Server. Con los datos de prueba de abajo,
   sin el CAST el promedio de 'Empresa Demo' sale 10 en lugar de 10.75.

   El JOIN es interno a propósito: un cliente sin facturas vencidas no debe
   aparecer en un reporte de cobranza.
   ---------------------------------------------------------------------------- */

DECLARE @Hoy    DATE          = CAST(GETDATE() AS DATE);
DECLARE @Umbral DECIMAL(12,2) = 10000.00;

SELECT
    c.ClienteId,
    c.Nombre                                     AS Cliente,
    COUNT(*)                                     AS FacturasVencidas,
    SUM(f.Monto)                                 AS SaldoVencido,
    CAST(AVG(CAST(DATEDIFF(DAY, f.FechaVencimiento, @Hoy) AS DECIMAL(10,2)))
         AS DECIMAL(10,2))                       AS PromedioDiasAtraso,
    MAX(DATEDIFF(DAY, f.FechaVencimiento, @Hoy)) AS MaxDiasAtraso
FROM dbo.Facturas AS f
JOIN dbo.Clientes AS c ON c.ClienteId = f.ClienteId
WHERE f.Estatus = 'pending'
  AND f.FechaVencimiento < @Hoy
GROUP BY c.ClienteId, c.Nombre
HAVING SUM(f.Monto) > @Umbral
ORDER BY SaldoVencido DESC;
GO


/* ============================================================================
   NOTAS
   ============================================================================

   La moneda. SUM(Monto) mezclando MXN y USD da un número sin significado. Lo
   dejo así porque el enunciado pide un saldo por cliente, pero en un reporte
   real se resuelve de una de tres formas: agregando f.Moneda al GROUP BY,
   filtrando una sola moneda, o normalizando a una divisa base con tipo de
   cambio. Los datos de prueba incluyen una factura en USD justamente para que
   el problema sea visible.

   Índice recomendado para las dos consultas:

       CREATE INDEX IX_Facturas_Estatus_Vencimiento
           ON dbo.Facturas (Estatus, FechaVencimiento)
           INCLUDE (ClienteId, Monto, Moneda);

   Cubre el filtro por estatus y el rango de fechas, e incluye las columnas que
   se proyectan, así el plan no necesita volver a la tabla.

   Portabilidad. En PostgreSQL el filtro es
   FechaVencimiento < CURRENT_DATE - INTERVAL '10 days', y el promedio no
   necesita CAST porque la resta de fechas ya devuelve entero y AVG regresa
   numeric. En MySQL, DATEDIFF invierte el orden de los argumentos respecto a
   T-SQL: DATEDIFF(@Hoy, FechaVencimiento).
   ============================================================================ */


/* ============================================================================
   DATOS DE PRUEBA -- descomentar para reproducir los resultados
   ============================================================================

   Con @Hoy = '2026-08-22', la Consulta 1 devuelve 4 filas (F-1003 con 30 días,
   F-1005 con 21, F-1007 con 20 y F-1001 con 11) y la Consulta 2 devuelve
   Grupo Meridiano (61,250.00 y 30.00 días) y Empresa Demo (20,250.50 y 10.75).

   Los casos límite que quedan fuera, y por qué:
     F-1002  exactamente 10 días de atraso -- "más de 10" es estrictamente mayor
     F-1004  30 días de atraso pero pagada -- el filtro de estatus la excluye
     F-1006  aún no vence
     Textiles del Bajío queda fuera de la Consulta 2 por el HAVING (500.00)
     Logística Pacífico no aparece: no tiene facturas vencidas

CREATE TABLE dbo.Clientes (
    ClienteId INT           NOT NULL PRIMARY KEY,
    Nombre    NVARCHAR(120) NOT NULL,
    Correo    NVARCHAR(160) NOT NULL
);

CREATE TABLE dbo.Facturas (
    FacturaId        NVARCHAR(20)  NOT NULL PRIMARY KEY,
    ClienteId        INT           NOT NULL REFERENCES dbo.Clientes(ClienteId),
    Monto            DECIMAL(12,2) NOT NULL,
    Moneda           CHAR(3)       NOT NULL,
    FechaVencimiento DATE          NOT NULL,
    Estatus          VARCHAR(20)   NOT NULL
);

INSERT INTO dbo.Clientes VALUES
    (1, N'Empresa Demo',       N'cliente@empresa.com'),
    (2, N'Grupo Meridiano',    N'finanzas@meridiano.mx'),
    (3, N'Textiles del Bajio', N'tesoreria@textilesbajio.mx'),
    (4, N'Logistica Pacifico', N'pagos@logpacifico.mx');

INSERT INTO dbo.Facturas VALUES
    (N'F-1001', 1, 15000.50, 'MXN', '2026-08-11', 'pending'),  -- 11 dias
    (N'F-1002', 1,  4000.00, 'MXN', '2026-08-12', 'pending'),  -- 10 dias, borde
    (N'F-1007', 1,  1000.00, 'USD', '2026-08-02', 'pending'),  -- 20 dias, otra moneda
    (N'F-1008', 1,   250.00, 'MXN', '2026-08-20', 'pending'),  --  2 dias
    (N'F-1003', 2, 61250.00, 'MXN', '2026-07-23', 'pending'),  -- 30 dias
    (N'F-1004', 2, 99999.00, 'MXN', '2026-07-23', 'paid'),     -- pagada
    (N'F-1005', 3,   500.00, 'MXN', '2026-08-01', 'pending'),  -- 21 dias
    (N'F-1006', 4,  8000.00, 'MXN', '2026-09-01', 'pending');  -- aun no vence

   ============================================================================ */
