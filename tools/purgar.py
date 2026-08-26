#!/usr/bin/env python3
"""Saca del dataset las noticias que no debieron guardarse.

Un scraper que guarda basura no falla: publica en silencio, y eso no se
descubre mirando el resumen de la corrida sino leyendo el sitio. Cuando pasa,
esto limpia lo ya escrito sin tener que rehacer el archivo entero.

    python tools/purgar.py --fuente fifa --dry-run
    python tools/purgar.py --consentimiento
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.parser import _es_consentimiento                    # noqa: E402
from scraper.storage import Almacen, escribir_json, leer_json    # noqa: E402


def purgar(data_dir: Path, fuentes: set[str], consentimiento: bool,
           min_palabras: int, en_seco: bool) -> int:
    fuera = 0
    for parte in sorted(data_dir.rglob("part-*.json")):
        contenido = leer_json(parte, None)
        if not isinstance(contenido, dict):
            continue

        quedan = []
        for articulo in contenido.get("articles", []):
            motivo = None
            if fuentes and articulo.get("source") in fuentes:
                motivo = "fuente"
            elif consentimiento and _es_consentimiento(articulo.get("body") or ""):
                motivo = "aviso de cookies"
            elif min_palabras and (articulo.get("word_count") or 0) < min_palabras:
                motivo = f"menos de {min_palabras} palabras"

            if motivo:
                fuera += 1
                print(f"  fuera ({motivo}): {(articulo.get('title') or '')[:66]}")
            else:
                quedan.append(articulo)

        if len(quedan) != len(contenido.get("articles", [])) and not en_seco:
            contenido["articles"] = quedan
            contenido["count"] = len(quedan)
            if quedan:
                escribir_json(parte, contenido)
            else:
                parte.unlink()
    return fuera


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--fuente", action="append", default=[],
                        help="clave de fuente a retirar entera (repetible)")
    parser.add_argument("--consentimiento", action="store_true",
                        help="retira las que guardaron el aviso de cookies como cuerpo")
    parser.add_argument("--min-palabras", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    fuera = purgar(data_dir, set(args.fuente), args.consentimiento,
                   args.min_palabras, args.dry_run)
    print(f"\n{fuera} noticias retiradas")

    if fuera and not args.dry_run:
        indice = Almacen(data_dir).reconstruir_indices()
        print(f"quedan {indice['total_articles']} en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
