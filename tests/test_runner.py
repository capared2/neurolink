"""El pipeline entero, de las tres fuentes de mentira al dataset publicado."""
import json

from scraper.runner import Opciones, ejecutar


def _opciones(tmp_path, **extra):
    base = dict(
        vias=["rss", "sitemap", "crawl"],
        workers=2,
        delay=0.0,
        presupuesto=0,          # sin limite: el sitio falso responde al instante
        min_palabras=30,
        data_dir=str(tmp_path / "data"),
        state_dir=str(tmp_path / "state"),
        respetar_robots=False,
        site_url="https://gigantum.test",
    )
    base.update(extra)
    return Opciones(**base)


def test_de_las_fuentes_al_dataset(tmp_path, fetcher, fuentes_falsas):
    resumen = ejecutar(_opciones(tmp_path))

    assert resumen["sources"] == 3
    assert resumen["saved"] == 8, resumen
    assert resumen["failed"] == 0

    datos = tmp_path / "data"
    indice = json.loads((datos / "index.json").read_text())
    assert indice["total_articles"] == 8

    # Las tres verticales estan representadas: es un agregador multinicho.
    assert set(resumen["verticals"]) == {"noticias", "deportes", "gamer"}

    # Y cada noticia acabo en la carpeta de su tema.
    assert (datos / "deportes" / "futbol").is_dir()
    assert (datos / "gamer").is_dir()


def test_la_portada_mezcla_todo_y_agrupa(tmp_path, fetcher, fuentes_falsas):
    ejecutar(_opciones(tmp_path))
    portada = json.loads((tmp_path / "data" / "portada.json").read_text())

    verticales = {h["vertical"] for h in portada["stories"]}
    assert len(verticales) >= 3, "la portada tiene que mezclar nichos"

    # Los dos medios que cuentan la cumbre tienen que salir como una historia.
    agrupadas = [h for h in portada["stories"] if h["coverage"] > 1]
    assert agrupadas, "no se agrupo la historia que publican dos medios"
    assert "clasico" in agrupadas[0]["title"].lower()
    # Y la historia aparece una sola vez, no una por medio.
    assert sum(1 for h in portada["stories"] if "clasico" in h["title"].lower()) == 1


def test_el_sitio_no_puede_ver_las_fuentes(tmp_path, fetcher, fuentes_falsas):
    """Los ficheros que alimentan los listados van sin identidad del medio."""
    ejecutar(_opciones(tmp_path))
    for nombre in ("latest.json", "portada.json"):
        crudo = (tmp_path / "data" / nombre).read_text()
        assert "diario.test" not in crudo
        assert "cancha.test" not in crudo
        assert '"source"' not in crudo


def test_se_reanuda_donde_lo_dejo(tmp_path, fetcher, fuentes_falsas):
    primera = ejecutar(_opciones(tmp_path, max_articulos=3))
    assert primera["saved"] <= 3
    pendientes = json.loads((tmp_path / "state" / "run.json").read_text())["pending"]
    assert pendientes > 0

    segunda = ejecutar(_opciones(tmp_path, saltar_descubrimiento=True))
    assert segunda["saved"] > 0
    total = json.loads((tmp_path / "data" / "index.json").read_text())["total_articles"]
    assert total == 8


def test_no_repite_lo_que_ya_guardo(tmp_path, fetcher, fuentes_falsas):
    ejecutar(_opciones(tmp_path))
    segunda = ejecutar(_opciones(tmp_path))
    assert segunda["saved"] == 0
    assert json.loads((tmp_path / "data" / "index.json").read_text())["total_articles"] == 8


def test_min_palabras_descarta_lo_que_no_trae_cuerpo(tmp_path, fetcher, fuentes_falsas, sitio):
    sitio.anadir("https://cancha.test/futbol/1003-un-directo-sin-cuerpo",
                 "<html><head><title>Un directo sin cuerpo ninguno</title>"
                 "<meta property='og:title' content='Un directo sin cuerpo ninguno'>"
                 "</head><body><div id='app'></div></body></html>")
    sitio.anadir("https://cancha.test/futbol",
                 '<html><body><a href="/futbol/1003-un-directo-sin-cuerpo">Directo</a></body></html>')

    resumen = ejecutar(_opciones(tmp_path))
    assert resumen["skipped_empty"] >= 1
    # No se da por vista: puede traer la cronica cuando acabe el partido.
    vacias = json.loads((tmp_path / "state" / "fuentes" / "cancha" / "empty.json").read_text())
    assert any("1003" in u for u in vacias)


def test_se_puede_scrapear_una_sola_fuente(tmp_path, fetcher, fuentes_falsas):
    resumen = ejecutar(_opciones(tmp_path, fuentes=["cancha"]))
    assert resumen["sources"] == 1
    assert set(resumen["verticals"]) == {"deportes"}


def test_una_fuente_rota_no_se_lleva_a_las_demas(tmp_path, fetcher, fuentes_falsas, monkeypatch):
    from scraper import discovery

    original = discovery.descubrir

    def falla_en_diario(fuente, *args, **kwargs):
        if fuente.clave == "diario":
            raise RuntimeError("esta fuente ha explotado")
        return original(fuente, *args, **kwargs)

    monkeypatch.setattr("scraper.runner.discovery.descubrir", falla_en_diario)

    resumen = ejecutar(_opciones(tmp_path))
    assert resumen["saved"] == 4          # las dos fuentes sanas siguen publicando
    assert "deportes" in resumen["verticals"]


def test_desde_descarta_lo_viejo(tmp_path, fetcher, fuentes_falsas):
    resumen = ejecutar(_opciones(tmp_path, desde="2026-08-25"))
    assert resumen["skipped_old"] >= 1
    assert resumen["saved"] < 8


def test_el_presupuesto_corta_pero_deja_todo_guardado(tmp_path, fetcher, fuentes_falsas):
    """Aunque se agote el tiempo, los indices tienen que quedar coherentes."""
    resumen = ejecutar(_opciones(tmp_path, presupuesto=1, max_articulos=2))
    indice = json.loads((tmp_path / "data" / "index.json").read_text())
    assert indice["total_articles"] == resumen["saved"]
    assert (tmp_path / "data" / "latest.json").exists()
    assert (tmp_path / "data" / "seo" / "sitemap.xml").exists()


def test_la_salud_de_cada_fuente_queda_publicada(tmp_path, fetcher, fuentes_falsas):
    ejecutar(_opciones(tmp_path))
    salud = json.loads((tmp_path / "data" / "fuentes.json").read_text())
    assert set(salud["sources"]) == {"diario", "cancha", "pixel"}
    assert salud["sources"]["diario"]["saved"] == 4
