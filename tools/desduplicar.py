#!/usr/bin/env python3
"""Funde las noticias que se guardaron dos veces con dos URLs distintas.

La BBC sirve cada noticia en `bbc.com` y en `bbc.co.uk` con la misma ruta. El
identificador sale de la URL, así que eran dos registros: dos ficheros, dos
tarjetas y dos historias en la portada, una debajo de otra.

`urls.normalizar` ya unifica los dos dominios, pero lo escrito se queda como
estaba. Esto lo repasa: agrupa por URL ya normalizada y deja un solo registro,
el más completo --el que trae más texto--, conservando de paso las etiquetas y
las imágenes que solo tuviera el otro.

    python tools/desduplicar.py --dry-run
    python tools/desduplicar.py
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import urls as urlutil                                 # noqa: E402
from scraper.storage import Almacen, escribir_json, leer_json       # noqa: E402


def _mejor(registros: list[dict]) -> dict:
    """El registro que se queda: el que más texto trae."""
    ganador = max(registros, key=lambda a: (a.get("word_count") or 0, a.get("scraped_at") or ""))
    for otro in registros:
        if otro is ganador:
            continue
        # Lo que solo tuviera la copia no se tira por el camino.
        if not ganador.get("images") and otro.get("images"):
            ganador["images"] = otro["images"]
        vistas = {t.lower() for t in ganador.get("tags") or []}
        ganador["tags"] = (ganador.get("tags") or []) + [
            t for t in otro.get("tags") or [] if t.lower() not in vistas
        ]
    return ganador


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    partes = sorted(data_dir.rglob("part-*.json"))

    # Primero se ve el archivo entero: las dos copias pueden estar en ficheros
    # distintos, y hasta en categorías distintas.
    donde: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    contenidos: dict[Path, dict] = {}
    for parte in partes:
        contenido = leer_json(parte, None)
        if not isinstance(contenido, dict):
            continue
        contenidos[parte] = contenido
        for articulo in contenido.get("articles", []):
            donde[urlutil.normalizar(articulo.get("url") or "")].append((parte, articulo))

    repetidas = {u: v for u, v in donde.items() if len(v) > 1}
    sobran: set[int] = set()
    for url, copias in repetidas.items():
        ganador = _mejor([a for _, a in copias])
        for _, articulo in copias:
            if articulo is not ganador:
                sobran.add(id(articulo))

    print(f"URLs guardadas por duplicado: {len(repetidas)}")
    print(f"registros que sobran:         {len(sobran)}")
    for url, copias in list(repetidas.items())[:5]:
        print(f"  {copias[0][1]['title'][:58]}")
        for _, a in copias:
            print(f"     {'se queda' if id(a) not in sobran else 'se va   '}  {a['url'][:78]}")

    if args.dry_run or not sobran:
        return 0

    for parte, contenido in contenidos.items():
        quedan = [a for a in contenido.get("articles", []) if id(a) not in sobran]
        if len(quedan) != len(contenido.get("articles", [])):
            contenido["articles"] = quedan
            contenido["count"] = len(quedan)
            escribir_json(parte, contenido)

    indice = Almacen(data_dir).reconstruir_indices()
    print(f"\n{indice['total_articles']} noticias en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
