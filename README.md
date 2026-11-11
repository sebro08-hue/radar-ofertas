# Radar de Ofertas

[![actualizar-sitio](https://github.com/sebro08-hue/radar-ofertas/actions/workflows/update.yml/badge.svg)](https://github.com/sebro08-hue/radar-ofertas/actions/workflows/update.yml)

Comparador de precios de videojuegos. Revisa varias tiendas todos los días, empareja el mismo juego entre ellas aunque cada una lo nombre distinto, guarda el historial de precios y publica un sitio estático con las mejores ofertas.

**Sitio:** https://sebro08-hue.github.io/radar-ofertas/

## Qué hace

- Arma el catálogo automáticamente desde los más vendidos de Steam.
- Consulta precios en Steam, GOG y Amazon; calificaciones de Metacritic y de usuarios de Steam; y duración en How Long To Beat.
- Empareja títulos entre tiendas ("Zelda BOTW" vs. "The Legend of Zelda: Breath of the Wild – Nintendo Switch") con un algoritmo propio de similitud.
- Guarda cada precio observado, sin sobrescribir, para mostrar el historial y marcar mínimos históricos.
- Si una fuente falla, publica lo que tiene y avisa qué dato está desactualizado.
- Se actualiza solo todos los días con GitHub Actions y se publica en GitHub Pages.

## Cómo funciona

```mermaid
flowchart LR
    A[Descubrimiento<br/>Steam Top Sellers] --> B[Descarga<br/>pool de hilos]
    B -->|cola acotada| C[Parseo]
    C --> D[Emparejamiento<br/>pool de procesos]
    D --> E[(SQLite<br/>historial)]
    E --> F[Sitio estático]
```

El pipeline tiene tres modos de ejecución con el mismo código, útiles para medir y depurar:

| Modo | Descarga | Emparejamiento |
|---|---|---|
| `sequential` | una solicitud a la vez | un solo hilo |
| `concurrent` | pool de hilos | un solo hilo |
| `parallel` | pool de hilos | repartido entre núcleos |

Las descargas respetan un límite de conexiones por sitio, tienen timeout y reintentos con backoff exponencial y jitter. Lo que sigue fallando se reintenta una vez más al final y queda registrado en el panel de métricas.

## Inicio rápido

Requiere Python 3.11 o superior.

```bash
git clone https://github.com/sebro08-hue/radar-ofertas.git
cd radar-ofertas
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m radar init-db
python -m radar discover          # llena el catálogo
python -m radar run --limit 20    # primera corrida corta
python -m radar build-site        # genera site/dist
python3 -m http.server -d site/dist 8000
```

## Uso

```bash
python -m radar run [--mode sequential|concurrent|parallel] [--source live|replay]
                    [--fetch-workers N] [--match-workers N] [--match-executor process|thread]
                    [--limit N] [--discover]
```

### Catálogo

```bash
python -m radar games list
python -m radar games add "Hollow Knight" --alt "Hollow Knight: Voidheart Edition" --platform PC "Nintendo Switch"
python -m radar games set 42 hltb_id 68151      # id de la URL howlongtobeat.com/game/68151
python -m radar games remove 42
python -m radar games import juegos.csv
```

### Modo replay

En modo `live`, cada respuesta se guarda en `data/replay/`. El modo `replay` sirve esas mismas respuestas desde un servidor local con latencia configurable. Así se puede trabajar sin internet, depurar parsers o medir rendimiento con resultados repetibles.

```bash
python -m radar run --source replay --start-replay --latency-ms 150 --jitter-ms 50
```

### Benchmarks

```bash
python bench/run_sweeps.py          # barrido de workers: speedup, eficiencia, Karp–Flatt
python bench/contention.py          # estrategias de agregación entre hilos
```

Los resultados quedan en `bench/results/`. El último resumen aparece en la página de métricas del sitio.

### Calidad del emparejamiento

```bash
python -m radar validate-matching --sweep
```

Mide precisión y exhaustividad contra `data/validation_pairs.csv` (pares revisados a mano) para varios umbrales.

## Fuentes

| Fuente | Datos | Acceso |
|---|---|---|
| Steam | catálogo, precio, reseñas de usuarios | HTML |
| GOG | precio | API pública de catálogo |
| Amazon | precio (físico y digital) | HTML |
| Metacritic | Metascore | HTML |
| How Long To Beat | duración | HTML, por id del juego |

El scraper se identifica con su propio User-Agent, limita las solicitudes por dominio, usa caché local y no intenta saltarse bloqueos ni CAPTCHAs. Si un sitio rechaza la solicitud, se registra el fallo y se sigue con lo demás.

## Estructura

```
radar/            código del pipeline
  scrapers/       una fuente por módulo
  matching/       normalización y similitud de títulos
bench/            benchmarks
config/           settings.yaml
data/             base SQLite y pares de validación
site/static/      sitio (HTML + JS, sin backend)
tests/
```

## Tests

```bash
python -m unittest discover tests
```
