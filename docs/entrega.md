# Entrega: API serverless observable en GCP

El enunciado (`docs/laboratorio.md`) no lista de forma explícita qué se entrega. Este documento reúne todo lo que sustenta el laboratorio: código, infraestructura, datos de la corrida, dashboard y análisis.

- Repositorio: https://github.com/Diegoval-Dev/RAI-Observabilidad
- Proyecto GCP: `lab-observabilidad-030120` (región `us-central1`)
- API desplegada: https://us-central1-lab-observabilidad-030120.cloudfunctions.net/orders-api
- Dashboard: "Orders API Observability" en Cloud Monitoring
  (https://console.cloud.google.com/monitoring/dashboards?project=lab-observabilidad-030120)
- Log Analytics: https://console.cloud.google.com/logs/analytics?project=lab-observabilidad-030120

## 1. Qué se construyó

| Parte | Archivos | Resumen |
|-------|----------|---------|
| Backend | `lab/backend/main.py` | API de pedidos en Python (función `handle`) con crear, consultar, enviar y cancelar |
| Infraestructura | `lab/infra/*.tf` | Terraform: función Cloud Run, bucket de código, cuentas de servicio, Log Analytics en `_Default`, métricas basadas en logs |
| Simulador | `lab/simulator/simulator.py`, `config.json` | Una hora de tráfico, N(30, 10) minutos truncada a [0, 60], 6000 solicitudes |
| Datos | `lab/simulator/requests.csv` | Una fila por solicitud de la corrida válida |
| Dashboard | `lab/dashboard.json` | Un solo dashboard con métricas nativas, basadas en logs, SQL y logs |
| Consultas | `lab/queries.sql` | Seis consultas de Log Analytics |

### Backend

- Responde 201/200 en solicitudes válidas y rechaza con 400 (payload inválido), 404 (pedido inexistente) y 409 (cambio de estado imposible).
- `FAILURE_RATE` (probabilidad de 500) y `EXTRA_LATENCY_MS` (latencia extra) se leen de variables de entorno.
- Escribe una línea JSON por solicitud con `severity`, `message` (`request_completed`), `request_id`, `endpoint`, `status_code`, `outcome`, `error_type`, `latency_ms` y los atributos de negocio `customer_tier`, `category` y `amount`.
- No usa base de datos: el estado del pedido va dentro del ID (`ord_<estado>_<tier>_<categoría>_<centavos>_<nonce>`), de modo que varias instancias dan resultados consistentes.

### Simulador

- Dispara cada solicitud en su instante programado sin esperar a la anterior, con un `X-Request-ID` único.
- 8 % de errores intencionales (payload inválido, ID inexistente, transición imposible).
- Escenario `double_errors`: entre los minutos 35 y 45 duplica la proporción de errores para la categoría `electronics`.

## 2. Despliegue

```bash
cd lab/infra
terraform init
terraform apply        # 22 recursos
```

La salida `function_url` se copió a `lab/simulator/config.json`. Las tres métricas basadas en logs se crean con el mismo `apply`, es decir, antes de correr el simulador.

Métricas basadas en logs (`lab/infra/monitoring.tf`):

| Métrica | Tipo | Etiquetas |
|---------|------|-----------|
| `orders-api/requests_by_outcome` | contador | `endpoint`, `outcome`, `category` |
| `orders-api/errors_by_type` | contador | `error_type`, `status_code` |
| `orders-api/request_latency_ms` | distribución | `endpoint` |

## 3. La corrida

Se hicieron dos corridas. La primera (02:15 UTC) se descartó: la laptop se durmió y las solicitudes salieron repartidas en unas 12 horas, con retrasos de hasta 11 horas, así que los tiempos no eran válidos. La segunda se ejecutó con `caffeinate`.

| Dato | Valor |
|------|-------|
| Ventana | 15:05:46 a 16:05:41 UTC del 15 de octubre de 2026 |
| Solicitudes | 6000 (6000 líneas en Cloud Logging, una por solicitud) |
| Retraso máximo de envío | 0,1 s |
| Latencia del cliente | p50 91 ms, p95 128 ms, máximo 1,7 s |

## 4. Resultados y análisis

Todas las cifras salen de los 6000 logs de la segunda corrida.

### Resultado de las solicitudes

| Resultado | Solicitudes | Proporción |
|-----------|------------:|-----------:|
| success | 5362 | 89,4 % |
| client_error | 514 | 8,6 % |
| server_error | 124 | 2,1 % |

El 2,1 % de errores 500 corresponde al `failure_rate = 0.02` configurado.

### Errores por tipo

| `error_type` | Código | Solicitudes |
|--------------|-------:|------------:|
| order_not_found | 404 | 153 |
| simulated_failure | 500 | 124 |
| invalid_transition_from_cancelled | 409 | 99 |
| invalid_amount | 400 | 91 |
| invalid_transition_from_shipped | 409 | 75 |
| invalid_customer_tier | 400 | 51 |
| invalid_category | 400 | 45 |

### Tasa de error y latencia por operación

| Endpoint | Solicitudes | Tasa de error | p50 | p95 | p99 |
|----------|------------:|--------------:|----:|----:|----:|
| `GET /orders/{id}` | 1964 | 7,9 % | 0,03 ms | 0,05 ms | 0,16 ms |
| `POST /orders` | 2442 | 9,5 % | 0,14 ms | 0,27 ms | 0,75 ms |
| `POST /orders/{id}/cancel` | 920 | 14,7 % | 0,03 ms | 0,05 ms | 0,12 ms |
| `POST /orders/{id}/ship` | 674 | 17,2 % | 0,03 ms | 0,05 ms | 0,18 ms |

La latencia es la que mide la aplicación (`latency_ms`), sin red ni arranque en frío. La que ve el cliente (p50 91 ms) es mucho mayor. Cancelar y enviar tienen más errores porque son las operaciones que admiten transiciones imposibles (409).

### Escenario `double_errors` (electronics, minutos 35 a 45)

| Grupo | Solicitudes | Errores 4xx | Tasa |
|-------|------------:|------------:|-----:|
| electronics, dentro de la ventana | 322 | 27 | 8,4 % |
| electronics, fuera de la ventana | 1120 | 57 | 5,1 % |
| otras categorías, dentro de la ventana | 1091 | 51 | 4,7 % |
| otras categorías, fuera de la ventana | 3265 | 181 | 5,5 % |

El escenario se nota en electronics y las demás categorías no cambian. El aumento es de 1,6 veces y no de 2: la ventana tiene pocas muestras y solo se duplica la proporción de errores intencionales de esa categoría.

### Vista de negocio (pedidos creados con 201)

| `customer_tier` | Pedidos | Monto total |
|-----------------|--------:|------------:|
| premium | 707 | 183 351 |
| vip | 756 | 187 008 |
| standard | 747 | 185 626 |

## 5. Dashboard

"Orders API Observability" es un solo dashboard con:

| Panel | Fuente |
|-------|--------|
| Requests by response class | métrica nativa `run.googleapis.com/request_count` |
| Platform latency p50/p95/p99 | métrica nativa `request_latencies` |
| Container instances | métrica nativa `container/instance_count` |
| Requests by outcome | métrica basada en logs |
| Errors by category | métrica basada en logs |
| Errors by type | métrica basada en logs |
| App latency p95 by endpoint | métrica basada en logs |
| Errors per minute | gráfica SQL de Log Analytics |
| Recent request logs | panel de logs |

Preguntas que responde: cuántas solicitudes terminan en cada resultado, qué tipos de error ocurren, si una categoría concentra errores, cómo cambia la latencia y cuántas instancias hace falta escalar.

Para ver la corrida válida en el dashboard, fija el rango entre 15:00 y 16:15 UTC del 15 de octubre. La primera corrida sigue en Cloud Logging, así que con rangos más amplios se mezcla.

## 6. Cómo reproducir

1. `gcloud auth application-default login`
2. Poner `project_id` en `lab/infra/terraform.tfvars`.
3. `cd lab/infra && terraform init && terraform apply`
4. Copiar `function_url` a `lab/simulator/config.json` (`target_url`).
5. `cd lab/simulator && pip install -r requirements.txt && python simulator.py` (una hora; en una laptop usar `caffeinate -dims`).
6. Crear el dashboard con `gcloud monitoring dashboards create --config-from-file=lab/dashboard.json`.

## 7. Limpieza

Al terminar: `cd lab/infra && terraform destroy`. Esto borra la función, el bucket, las cuentas de servicio y las métricas basadas en logs. El dashboard (creado con `gcloud`), los logs y el proyecto siguen existiendo. La mejora de `_Default` a Log Analytics es permanente. Para eliminar todo, incluido el dashboard: `gcloud projects delete lab-observabilidad-030120`.
