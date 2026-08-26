#!/usr/bin/env python3
"""Repasa lo ya guardado: entidades HTML sin traducir y noticias sin etiquetas.

Dos arreglos del parser que el archivo no ve, porque lo escrito se queda como
estaba:

1. **Entidades escapadas dos veces.** Cuatro medios publican el titular como
   `&amp;#8217;`. Al analizar el HTML se quedaba en `&#8217;` y así se
   guardaba, de forma que la página --y el `<title>`, y el `headline` de los
   datos estructurados, y la tarjeta al compartir-- enseñaban el código en vez
   del apóstrofo.

2. **El medio etiquetándose a sí mismo.** Fox News marca sus noticias con
   "fox news media" y TechCrunch con "TechCrunch Disrupt 2026". Eso viaja a
   `keywords` y a `mentions`, y el sitio no nombra ninguna fuente.

3. **Texto UTF-8 leído como latin-1.** `£` guardado como `Â£`. `requests` no
   deja el charset a `None` cuando el servidor no lo declara: pone ISO-8859-1,
   así que la guarda del fetcher nunca saltaba.

4. **Noticias sin ninguna etiqueta.** Un tercio del archivo. Sin etiquetas la
   noticia sale sin `mentions` ni `keywords`, que es justo lo que mira un
   buscador generativo para saber de qué va sin leérsela entera. Se sacan del
   propio texto.

    python tools/enriquecer.py --dry-run
    python tools/enriquecer.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.parser import desescapar, entidades_del_texto          # noqa: E402
from scraper.sources import FUENTES                                 # noqa: E402
from scraper.storage import Almacen, escribir_json, leer_json       # noqa: E402

ENTIDAD = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,10});")

# Bytes UTF-8 leídos como latin-1: `£` sale `Â£` y `ñ` sale `Ã±`. Estas son las
# firmas que no aparecen en un texto bien codificado.
MOJIBAKE = re.compile(r"Â[£€¢©®°±·»«\xa0]|â€[™œ\x9d\x9c\x99]|Ã[©¡³±¨ºª\x89]")


def arreglar_codificacion(texto: str) -> str:
    """Deshace un texto UTF-8 que se leyó como latin-1.

    Se vuelve a codificar con la codificación equivocada y se lee con la
    correcta. Si algún carácter no sobrevive al viaje de vuelta, el texto no
    era mojibake y se deja como estaba.
    """
    try:
        arreglado = texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto
    return arreglado if not MOJIBAKE.search(arreglado) else texto
CAMPOS = ("title", "standfirst", "summary", "body")

# El nombre del medio nunca puede acabar de etiqueta: delataría de dónde salió
# la noticia, y el sitio no menciona fuentes.
VETADOS = tuple({f.nombre for f in FUENTES} | {f.clave for f in FUENTES})


def desescapar_articulo(articulo: dict) -> bool:
    """Traduce las entidades de un registro. Devuelve si tocó algo."""
    cambiado = False
    for campo in CAMPOS:
        valor = articulo.get(campo)
        if isinstance(valor, str) and MOJIBAKE.search(valor):
            nuevo = arreglar_codificacion(valor)
            if nuevo != valor:
                articulo[campo] = valor = nuevo
                cambiado = True
        if isinstance(valor, str) and ENTIDAD.search(valor):
            nuevo = desescapar(valor).replace("\xa0", " ").strip()
            if nuevo != valor:
                articulo[campo] = nuevo
                cambiado = True
    # Los medios se etiquetan a sí mismos --"fox news media", "TechCrunch
    # Disrupt 2026", "Radio Marca"-- y eso acaba en `keywords` y en `mentions`.
    # Se quita el nombre del medio del que salió esta noticia, no una lista
    # fija: "Steam" delata a una noticia de Steam, pero es el tema del que
    # habla una de IGN.
    propio = tuple(
        n.lower() for n in (articulo.get("source_name"), articulo.get("source")) if n
    )
    etiquetas = [
        desescapar(t).strip()
        for t in articulo.get("tags") or []
        if t and t.strip() and not any(n in t.lower() for n in propio)
    ]
    if etiquetas != (articulo.get("tags") or []):
        articulo["tags"] = etiquetas
        cambiado = True
    if cambiado:
        articulo["word_count"] = len((articulo.get("body") or "").split())
    return cambiado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    desescapados = etiquetados = sin_remedio = 0

    for parte in sorted(data_dir.rglob("part-*.json")):
        contenido = leer_json(parte, None)
        if not isinstance(contenido, dict):
            continue

        cambiado = False
        for articulo in contenido.get("articles", []):
            if desescapar_articulo(articulo):
                desescapados += 1
                cambiado = True

            if not articulo.get("tags"):
                entidades = entidades_del_texto(
                    articulo.get("title") or "",
                    articulo.get("body") or "",
                    prohibidos=VETADOS,
                )
                if entidades:
                    articulo["tags"] = entidades
                    etiquetados += 1
                    cambiado = True
                else:
                    sin_remedio += 1

        if cambiado and not args.dry_run:
            escribir_json(parte, contenido)

    print(f"noticias con entidades traducidas: {desescapados}")
    print(f"noticias que estrenan etiquetas:   {etiquetados}")
    print(f"  se quedan sin ninguna:           {sin_remedio}")

    if (desescapados or etiquetados) and not args.dry_run:
        indice = Almacen(data_dir).reconstruir_indices()
        print(f"\n{indice['total_articles']} noticias en {indice['total_categories']} temas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
