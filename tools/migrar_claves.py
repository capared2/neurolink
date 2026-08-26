#!/usr/bin/env python3
"""Renombra las claves de categoría del dataset ya guardado.

Cuando la taxonomía cambia de nombres --por ejemplo al pasar el sitio a otro
idioma-- el dataset que ya está en disco se queda con las claves viejas, y el
índice acabaría mezclando unas y otras. Esto reescribe los ficheros y mueve las
carpetas, conservando las noticias y, sobre todo, el estado: las URLs no
cambian, así que ``seen.txt`` sigue valiendo y no hay que volver a descargar
nada.

    python tools/migrar_claves.py --dry-run     # ver qué cambiaría
    python tools/migrar_claves.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import taxonomy                                  # noqa: E402
from scraper.storage import Almacen, escribir_json, leer_json  # noqa: E402

# Claves viejas -> nuevas. Se deja escrito en el propio fichero porque una
# migración es de una vez: la próxima traerá su propio mapa.
MAPA = {
    "noticias/mundo": "news/world",
    "noticias/politica": "news/politics",
    "noticias/economia": "news/business",
    "noticias/sociedad": "news/society",
    "noticias/salud": "news/health",
    "noticias/cultura": "news/culture",
    "deportes/futbol": "sports/soccer",
    "deportes/baloncesto": "sports/basketball",
    "deportes/nfl": "sports/nfl",
    "deportes/beisbol": "sports/baseball",
    "deportes/tenis": "sports/tennis",
    "deportes/motor": "sports/motorsport",
    "deportes/golf": "sports/golf",
    "deportes/ciclismo": "sports/cycling",
    "deportes/combate": "sports/combat",
    "deportes/cricket": "sports/cricket",
    "deportes/rugby": "sports/rugby",
    "deportes/olimpismo": "sports/olympics",
    "deportes/otros": "sports/more",
    "gamer/juegos": "gaming/games",
    "gamer/esports": "gaming/esports",
    "gamer/streaming": "gaming/streaming",
    "tecnologia/ia": "tech/ai",
    "tecnologia/gadgets": "tech/gadgets",
    "tecnologia/empresas": "tech/companies",
    "tecnologia/ciencia": "tech/science",
    "tecnologia/software": "tech/software",
    "tecnologia/cripto": "tech/crypto",
}


def migrar(data_dir: Path, en_seco: bool) -> int:
    movidas = 0
    articulos = 0

    for vieja, nueva in sorted(MAPA.items()):
        origen = data_dir.joinpath(*vieja.split("/"))
        if not origen.is_dir():
            continue

        destino = data_dir.joinpath(*nueva.split("/"))
        partes = sorted(origen.glob("part-*.json"))
        cuantas = sum(leer_json(p, {}).get("count", 0) for p in partes)
        print(f"  {vieja:<24} -> {nueva:<20} {len(partes)} ficheros, {cuantas} noticias")
        movidas += 1
        articulos += cuantas

        if en_seco:
            continue

        destino.mkdir(parents=True, exist_ok=True)
        vertical, tema = nueva.split("/", 1)

        for parte in partes:
            contenido = leer_json(parte, None)
            if not isinstance(contenido, dict):
                continue
            contenido["category"] = nueva
            for articulo in contenido.get("articles", []):
                articulo["category"] = nueva
                articulo["vertical"] = vertical
                articulo["topic"] = tema
                articulo["topic_name"] = taxonomy.nombre_tema(nueva)
            escribir_json(destino / parte.name, contenido)

        # El lookup se regenera solo al reconstruir los índices.
        shutil.rmtree(origen)

    if not en_seco:
        # Las carpetas de vertical vacías estorban al recorrer el dataset.
        for viejo_vertical in ("noticias", "deportes", "gamer", "tecnologia"):
            carpeta = data_dir / viejo_vertical
            if carpeta.is_dir() and not any(carpeta.rglob("part-*.json")):
                shutil.rmtree(carpeta)

    print(f"\n{movidas} categorías, {articulos} noticias")
    return movidas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true", help="no toca nada, solo enseña")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"no existe {data_dir}", file=sys.stderr)
        return 1

    if migrar(data_dir, args.dry_run) and not args.dry_run:
        print("\nreconstruyendo índices…")
        indice = Almacen(data_dir).reconstruir_indices()
        print(f"{indice['total_articles']} noticias en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
