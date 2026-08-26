#!/usr/bin/env python3
"""Quita del dataset los campos que se pueden derivar al leerlo.

Cada kilobyte guardado se paga en el sitio: para pintar **una** noticia hay que
parsear el fichero entero que la contiene, y Cloudflare Workers corta la
invocación a los 10 ms de CPU. `paragraphs` era `body` partido por los saltos
dobles --el mismo texto, guardado dos veces-- y suponía la mitad del peso.

    python tools/adelgazar.py --dry-run
    python tools/adelgazar.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.storage import Almacen, CAMPOS_DERIVADOS, escribir_json, leer_json  # noqa: E402


def adelgazar(data_dir: Path, en_seco: bool) -> tuple[int, int, int]:
    antes = despues = tocados = 0
    for parte in sorted(data_dir.rglob("part-*.json")):
        antes += parte.stat().st_size
        contenido = leer_json(parte, None)
        if not isinstance(contenido, dict):
            despues += parte.stat().st_size
            continue

        cambiado = False
        for articulo in contenido.get("articles", []):
            for campo in CAMPOS_DERIVADOS:
                if campo in articulo:
                    articulo.pop(campo)
                    cambiado = True

        if cambiado and not en_seco:
            escribir_json(parte, contenido)
            tocados += 1
        elif cambiado:
            tocados += 1
        despues += parte.stat().st_size
    return antes, despues, tocados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    antes, despues, tocados = adelgazar(data_dir, args.dry_run)
    print(f"{tocados} ficheros con campos derivados")
    print(f"  antes:   {antes / 1024 / 1024:6.1f} MB")
    print(f"  después: {despues / 1024 / 1024:6.1f} MB")

    if tocados and not args.dry_run:
        indice = Almacen(data_dir).reconstruir_indices()
        print(f"\n{indice['total_articles']} noticias en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
