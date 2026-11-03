# nq-bias-data

Descarga velas de NQ (Yahoo Finance, `NQ=F`) en 4h, 1h, 15 min y 5 min, y calcula:

- FVG abiertos más cercanos al precio (alcistas y bajistas)
- Estructura (swings, tendencia, ruptura del último swing)
- Fibonacci del último impulso (50 % y OTE 62–79 %) en 4h y 1h
- PDH/PDL, máximo y mínimo semanal, rangos de Asia, Londres y overnight
- Máximos y mínimos iguales (liquidez)

Resultados en `data/latest/` (`summary.md`, `summary.json`, `chart_*.png`, `candles_*.csv`) y un histórico en `data/history/`.

Se ejecuta con GitHub Actions de lunes a viernes antes de Londres y antes de Nueva York, y también a mano desde la pestaña **Actions → Actualizar datos NQ → Run workflow**. Lo leen las tareas programadas de Claude "Bias NQ mañana" y "Bias NQ pre-NY".

Datos gratuitos con posible retraso de minutos; verifica siempre los niveles en tu plataforma. No es asesoramiento financiero.

## Plan del día (dibujo)

`python draw_plan.py plan.json plan.png` dibuja sobre las velas de 15 min el camino hipotético de los escenarios A y B, con zonas y niveles. Las horas van en hora de Alemania. Es un ejemplo ilustrativo, no un consejo financiero. El formato de `plan.json` está explicado al principio de `draw_plan.py`.

## Estadísticas

`summary.json` → `estadisticas`: rango medio diario, % de días que toman el PDH/PDL, en qué sesión se suele hacer el máximo y el mínimo, y qué pasa tras días bajistas o alcistas fuertes (calculado con las velas de 1h de los últimos ~60 días).
