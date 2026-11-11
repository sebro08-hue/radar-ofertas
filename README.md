# Radar de Ofertas Multinúcleo

Pipeline de web scraping de ofertas de videojuegos en tres modos (secuencial, concurrente y paralelo) que genera un sitio estático.

## Instalación

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Uso

```bash
python -m radar init-db
python -m radar discover                          # etapa 0: catálogo desde Steam Top Sellers
python -m radar run --source live --mode parallel # sitios reales; guarda capturas en data/replay
python -m radar run --source replay --start-replay --mode sequential   # mismo pipeline, datos capturados
python -m radar build-site                        # genera site/dist
```

- Modos: `--mode sequential | concurrent | parallel`
- Workers: `--fetch-workers N --match-workers N --match-executor process|thread`
- Latencia simulada (replay): `--latency-ms 150 --jitter-ms 50`
- Prueba corta: `--limit 20`

Otros comandos:

```bash
python -m radar replay-server --latency-ms 150    # servidor replay aparte
python bench/run_sweeps.py                        # barridos y métricas de speedup
python -m radar contention                        # experimento de contención
python -m radar validate-matching --sweep         # precisión / exhaustividad por umbral
python -m radar games list | add | set | remove | import archivo.csv
python -m unittest discover tests
```

## Fuentes

| Fuente | Dato | Acceso |
|---|---|---|
| Steam | catálogo, precio, reseñas de usuarios | HTML (fragmento de búsqueda) |
| GOG | precio | JSON público |
| Amazon | precio minorista | HTML |
| Metacritic | calificación | HTML |
| How Long To Beat | duración | HTML (requiere `hltb_id` por juego) |
