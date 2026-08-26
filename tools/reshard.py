#!/usr/bin/env python3
"""Reparte las noticias de cada tema en ficheros del tamaño que se le diga.

Bajar `--shard-size` solo afecta a los ficheros nuevos: los ya escritos
conservan el tamaño que tenían. Esto rehace los que hay, que es lo que baja de
verdad el peor caso de CPU del sitio --pintar una noticia obliga a parsear el
fichero entero que la contiene--.

    python tools/reshard.py --shard-size 50 --dry-run
    python tools/reshard.py --shard-size 50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import config                                        # noqa: E402
from scraper.storage import Almacen, PLANTILLA_PARTE, leer_json    # noqa: E402


def rehacer(data_dir: Path, tam: int, en_seco: bool) -> tuple[int, int]:
    almacen = Almacen(data_dir, tam)
    temas = sorted({p.parent for p in data_dir.rglob("part-*.json")})
    ficheros_antes = ficheros_despues = 0

    for carpeta in temas:
        partes = sorted(carpeta.glob("part-*.json"))
        ficheros_antes += len(partes)

        articulos: list[dict] = []
        categoria = ""
        for parte in partes:
            contenido = leer_json(parte, {})
            categoria = contenido.get("category") or categoria
            articulos.extend(contenido.get("articles", []))
        if not categoria:
            continue

        articulos.sort(key=lambda a: (a.get("published_at") or "", a.get("id") or ""))
        lotes = [articulos[i:i + tam] for i in range(0, len(articulos), tam)] or [[]]
        ficheros_despues += len(lotes)

        if en_seco:
            continue

        for parte in partes:
            parte.unlink()
        for numero, lote in enumerate(lotes, start=1):
            almacen._guardar_parte(carpeta / PLANTILLA_PARTE.format(numero),
                                   categoria, numero, lote)

    return ficheros_antes, ficheros_despues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--shard-size", type=int, default=config.DEFAULT_SHARD_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    antes, despues = rehacer(data_dir, args.shard_size, args.dry_run)
    print(f"ficheros: {antes} -> {despues}  (a {args.shard_size} noticias por fichero)")

    if not args.dry_run:
        indice = Almacen(data_dir).reconstruir_indices()
        print(f"{indice['total_articles']} noticias en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
