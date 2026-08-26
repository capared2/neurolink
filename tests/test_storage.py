"""Particionado, indices derivados y estado reanudable."""
import json

from scraper.storage import Almacen, Estado, EstadoFuente


def noticia(id_, categoria="sports/soccer", titulo="Un titular", cuando="2026-08-25T10:00:00Z",
            fuente="diario"):
    vertical, tema = categoria.split("/")
    return {
        "id": id_, "url": f"https://x.test/{id_}", "category": categoria,
        "vertical": vertical, "topic": tema, "topic_name": tema.capitalize(),
        "title": titulo, "standfirst": "", "summary": "Resumen", "body": "Cuerpo",
        "paragraphs": ["Cuerpo"], "word_count": 100, "authors": [], "tags": [],
        "published_at": cuando, "modified_at": None, "language": "es",
        "images": [{"url": "https://x.test/i.jpg", "caption": ""}], "videos": [],
        "is_premium": False, "source": fuente, "source_name": fuente, "scraped_at": cuando,
    }


def test_parte_lo_que_pasa_del_tamano(tmp_path):
    almacen = Almacen(tmp_path, tam_parte=2)
    for i in range(5):
        almacen.anadir(noticia(f"a{i}"))
    almacen.volcar()

    partes = sorted((tmp_path / "sports" / "soccer").glob("part-*.json"))
    assert len(partes) == 3
    assert json.loads(partes[0].read_text())["count"] == 2
    assert json.loads(partes[-1].read_text())["count"] == 1


def test_no_guarda_dos_veces_la_misma_url(tmp_path):
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1"))
    almacen.volcar()
    almacen.anadir(noticia("a1"))
    assert almacen.volcar() == {}


def test_los_indices_cuadran(tmp_path):
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1", "sports/soccer"))
    almacen.anadir(noticia("a2", "sports/soccer"))
    almacen.anadir(noticia("b1", "news/world"))
    almacen.volcar()

    indice = almacen.reconstruir_indices()
    assert indice["total_articles"] == 3
    assert indice["total_categories"] == 2
    assert {v["vertical"] for v in indice["verticals"]} == {"sports", "news"}

    lookup = json.loads((tmp_path / "sports" / "soccer" / "lookup.json").read_text())
    assert lookup["parts"]["a1"] == 1


def test_los_listados_no_llevan_ni_cuerpo_ni_medio(tmp_path):
    """latest.json y portada.json alimentan el sitio: no pueden filtrar la fuente."""
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1"))
    almacen.volcar()
    almacen.reconstruir_indices()

    ultimas = json.loads((tmp_path / "latest.json").read_text())
    tarjeta = ultimas["articles"][0]
    assert "body" not in tarjeta
    assert "source" not in tarjeta and "_source" not in tarjeta
    assert "url" not in tarjeta
    # La imagen viaja como ruta propia, no como URL del medio original.
    assert tarjeta["image"].startswith("/img/")
    assert "x.test" not in tarjeta["image"]

    portada = json.loads((tmp_path / "portada.json").read_text())
    historia = portada["stories"][0]
    assert "source" not in historia
    assert historia["coverage"] >= 1


def test_la_portada_agrupa_y_solo_cuenta_medios(tmp_path):
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1", titulo="Real Madrid beat Barcelona in the Clasico", fuente="uno"))
    almacen.anadir(noticia("a2", titulo="Real Madrid beats Barcelona in the Clasico", fuente="dos"))
    almacen.volcar()
    almacen.reconstruir_indices()

    portada = json.loads((tmp_path / "portada.json").read_text())
    assert portada["count"] == 1
    historia = portada["stories"][0]
    assert historia["coverage"] == 2
    assert len(historia["also"]) == 1
    # La otra cobertura se lista, pero sin decir de quien es.
    assert set(historia["also"][0]) == {"id", "category", "title", "published_at"}


def test_cada_tarjeta_lleva_marcada_su_historia(tmp_path):
    """Es lo que permite al sitio no repetir una noticia en ningun listado."""
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1", titulo="Real Madrid beat Barcelona in the Clasico", fuente="uno"))
    almacen.anadir(noticia("a2", titulo="Real Madrid beats Barcelona in the Clasico", fuente="dos"))
    almacen.anadir(noticia("b1", titulo="Una noticia que no tiene nada que ver", fuente="uno"))
    almacen.volcar()
    almacen.reconstruir_indices()

    tarjetas = json.loads((tmp_path / "latest.json").read_text())["articles"]
    porid = {t["id"]: t["story"] for t in tarjetas}
    assert porid["a1"] == porid["a2"], "las dos coberturas comparten historia"
    assert porid["b1"] != porid["a1"]
    # Y la marca no dice de que medio salio cada una.
    assert all(not t["story"].startswith(("uno", "dos")) for t in tarjetas)


def test_fuentes_json_recoge_la_salud(tmp_path):
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1"))
    almacen.volcar()
    almacen.reconstruir_indices({"diario": {"name": "Diario", "saved": 1, "failed": 0}})

    salud = json.loads((tmp_path / "fuentes.json").read_text())
    assert salud["sources"]["diario"]["saved"] == 1


def test_un_json_corrupto_no_tumba_la_reconstruccion(tmp_path):
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1"))
    almacen.volcar()
    (tmp_path / "sports" / "soccer" / "part-0002.json").write_text("{ esto no es JSON")

    indice = almacen.reconstruir_indices()
    assert indice["total_articles"] == 1


# -- estado ----------------------------------------------------------------

def test_el_estado_sobrevive_entre_ejecuciones(tmp_path):
    primero = EstadoFuente("bbc", tmp_path)
    assert primero.encolar(["https://a.test/1", "https://a.test/2"]) == 2
    primero.marcar_vista("https://a.test/1")
    primero.guardar()

    segundo = EstadoFuente("bbc", tmp_path)
    assert "https://a.test/1" in segundo.vistas
    assert segundo.pendientes == ["https://a.test/2"]
    # Lo ya visto no se vuelve a encolar.
    assert segundo.encolar(["https://a.test/1"]) == 0


def test_se_abandona_una_url_tras_demasiados_fallos(tmp_path):
    estado = EstadoFuente("bbc", tmp_path, max_fallos=2)
    for _ in range(2):
        estado.marcar_fallida("https://a.test/rota")
    assert estado.encolar(["https://a.test/rota"]) == 0


def test_una_pagina_sin_cuerpo_se_reintenta_pero_no_para_siempre(tmp_path):
    estado = EstadoFuente("bbc", tmp_path, reintentos_vacio=2)
    estado.marcar_vacia("https://a.test/directo")
    # Todavia se puede volver a pedir: el directo se llenara de texto al acabar.
    assert estado.encolar(["https://a.test/directo"]) == 1
    estado.tomar(1)
    estado.marcar_vacia("https://a.test/directo")
    assert estado.encolar(["https://a.test/directo"]) == 0


def test_cada_fuente_lleva_su_propia_carpeta(tmp_path):
    estado = Estado(tmp_path, max_fallos=3, reintentos_vacio=2)
    estado.de("bbc").encolar(["https://bbc.test/1"])
    estado.de("cnn").encolar(["https://cnn.test/1"])
    estado.guardar()

    assert (tmp_path / "fuentes" / "bbc" / "pending.txt").exists()
    assert (tmp_path / "fuentes" / "cnn" / "pending.txt").exists()
    resumen = json.loads((tmp_path / "run.json").read_text())
    assert resumen["pending"] == 2


def test_publica_los_hosts_de_imagen(tmp_path):
    """El sitio los necesita para no acabar siendo un proxy abierto."""
    almacen = Almacen(tmp_path, tam_parte=10)
    almacen.anadir(noticia("a1"))
    almacen.volcar()
    almacen.reconstruir_indices()

    imagenes = json.loads((tmp_path / "imagenes.json").read_text())
    assert imagenes["hosts"] == ["x.test"]
