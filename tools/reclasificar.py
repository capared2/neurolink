#!/usr/bin/env python3
"""Vuelve a clasificar lo ya guardado con la taxonomía de ahora.

Un tercio del archivo vivía en `news/world`, el cajón de la actualidad
general, porque la clasificación solo miraba la ruta de la URL y la sección
comparada entera. Con el subdominio y la sección por palabras --
`sports.yahoo.com`, "Yahoo Sports"-- casi mil noticias encuentran su sitio.

La categoría decide en qué carpeta vive la noticia, así que esto mueve
ficheros: se lee el archivo entero, se recalcula cada registro y se reescribe
cada tema con lo que le toca. El identificador no cambia --sale de la URL--
así que los enlaces que ya existieran siguen sirviendo.

    python tools/reclasificar.py --dry-run
    python tools/reclasificar.py
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import config, taxonomy                                  # noqa: E402
from scraper import urls as urlutil                                   # noqa: E402
from scraper.sources import FUENTES                                   # noqa: E402
from scraper.storage import (                                         # noqa: E402
    Almacen, PLANTILLA_PARTE, leer_json,
)

POR_CLAVE = {f.clave: f for f in FUENTES}


def categoria_de(articulo: dict) -> str:
    """La categoría que le tocaría hoy a este registro."""
    fuente = POR_CLAVE.get(articulo.get("source") or "")
    if fuente is None:
        return articulo.get("category") or "news/world"
    url = articulo.get("url") or ""
    return taxonomy.clasificar(
        segmentos=urlutil.segmentos(url),
        host=urlutil.etiquetas_host(url),
        seccion=articulo.get("section") or "",
        etiquetas=articulo.get("tags") or [],
        texto=" ".join(
            str(articulo.get(c) or "") for c in ("title", "standfirst", "summary")
        ),
        vertical_por_defecto=fuente.vertical,
        tema_por_defecto=fuente.tema_por_defecto,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--shard-size", type=int, default=config.DEFAULT_SHARD_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    partes = sorted(data_dir.rglob("part-*.json"))

    por_categoria: dict[str, list[dict]] = defaultdict(list)
    movimientos: Counter = Counter()
    vistos: set[str] = set()

    for parte in partes:
        contenido = leer_json(parte, None)
        if not isinstance(contenido, dict):
            continue
        for articulo in contenido.get("articles", []):
            # El archivo se lee entero de una vez, así que aquí también se
            # cierra la puerta a que una noticia quede en dos carpetas.
            clave = articulo.get("id") or articulo.get("url") or ""
            if not clave or clave in vistos:
                continue
            vistos.add(clave)

            antigua = articulo.get("category") or ""
            nueva = categoria_de(articulo)
            if nueva != antigua:
                movimientos[(antigua, nueva)] += 1
                articulo["category"] = nueva
                articulo["vertical"], articulo["topic"] = nueva.split("/", 1)
                articulo["topic_name"] = taxonomy.nombre_tema(nueva)
            por_categoria[nueva].append(articulo)

    total = sum(len(v) for v in por_categoria.values())
    print(f"{total} noticias · {sum(movimientos.values())} cambian de categoría")
    for (antigua, nueva), cuantas in movimientos.most_common(15):
        print(f"  {cuantas:5}  {antigua or '(ninguna)':18} -> {nueva}")

    if args.dry_run:
        return 0

    almacen = Almacen(data_dir, args.shard_size)
    for parte in partes:
        parte.unlink()
    for carpeta in {p.parent for p in partes}:
        for sobrante in carpeta.glob("lookup.json"):
            sobrante.unlink()

    for categoria, articulos in sorted(por_categoria.items()):
        carpeta = data_dir / categoria
        carpeta.mkdir(parents=True, exist_ok=True)
        articulos.sort(key=lambda a: (a.get("published_at") or "", a.get("id") or ""))
        lotes = [
            articulos[i:i + args.shard_size]
            for i in range(0, len(articulos), args.shard_size)
        ] or [[]]
        for numero, lote in enumerate(lotes, start=1):
            almacen._guardar_parte(
                carpeta / PLANTILLA_PARTE.format(numero), categoria, numero, lote
            )

    # Las carpetas que se quedaron vacías no deben seguir anunciándose.
    for carpeta in sorted({p.parent for p in partes}, reverse=True):
        if carpeta.exists() and not any(carpeta.iterdir()):
            carpeta.rmdir()

    indice = almacen.reconstruir_indices()
    print(f"\n{indice['total_articles']} noticias en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
