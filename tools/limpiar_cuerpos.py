#!/usr/bin/env python3
"""Vuelve a limpiar los cuerpos ya guardados.

Cuando el parser aprende a quitar algo --etiquetas HTML que un medio metió
dentro del JSON-LD, URLs escritas en el texto, bloques de enlaces
promocionales-- lo ya escrito se queda como estaba. Esto lo pasa otra vez por
el mismo filtro, sin volver a descargar nada.

Las URLs importan más de lo que parece: dicen de qué medio salió la noticia, y
el sitio que consume este dataset no debe enseñarlo.

    python tools/limpiar_cuerpos.py --dry-run
    python tools/limpiar_cuerpos.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.parser import _texto_plano, limpiar_texto              # noqa: E402
from scraper.storage import Almacen, escribir_json, leer_json       # noqa: E402

ETIQUETA = re.compile(r"</?(p|ul|ol|li|strong|em|a|div|br|span|h[1-6]|img)\b[^>]*>", re.I)
URL = re.compile(r"(?:https?://|www\.)\S+", re.I)


def limpiar(cuerpo: str) -> str:
    if ETIQUETA.search(cuerpo):
        # Llegó HTML crudo: se parsea igual que si viniera de una página.
        limpio, _ = _texto_plano(cuerpo)
        if limpio:
            return limpio
    parrafos = [limpiar_texto(p) for p in cuerpo.split("\n\n")]
    return "\n\n".join(p for p in parrafos if p)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    con_html = con_url = tocados = vaciados = 0

    for parte in sorted(data_dir.rglob("part-*.json")):
        contenido = leer_json(parte, None)
        if not isinstance(contenido, dict):
            continue

        cambiado = False
        quedan = []
        for articulo in contenido.get("articles", []):
            cuerpo = articulo.get("body") or ""
            tenia_html = bool(ETIQUETA.search(cuerpo))
            tenia_url = bool(URL.search(cuerpo))
            nuevo = limpiar(cuerpo) if (tenia_html or tenia_url) else cuerpo

            if nuevo != cuerpo:
                con_html += tenia_html
                con_url += tenia_url
                tocados += 1
                cambiado = True
                articulo["body"] = nuevo
                articulo["word_count"] = len(nuevo.split())

            if articulo.get("word_count", 0) > 0:
                quedan.append(articulo)
            else:
                vaciados += 1
                cambiado = True

        if cambiado and not args.dry_run:
            contenido["articles"] = quedan
            contenido["count"] = len(quedan)
            escribir_json(parte, contenido)

    print(f"noticias reescritas: {tocados}")
    print(f"  llevaban HTML: {con_html}")
    print(f"  llevaban URLs: {con_url}")
    print(f"  se quedaron sin cuerpo y salen: {vaciados}")

    if tocados and not args.dry_run:
        indice = Almacen(data_dir).reconstruir_indices()
        print(f"\n{indice['total_articles']} noticias en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
