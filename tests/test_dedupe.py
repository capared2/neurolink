"""Agrupacion de la misma historia contada por varios medios."""
from scraper import dedupe


def articulo(id_, titulo, fuente, vertical="sports", cuando="2026-08-25T10:00:00Z"):
    return {"id": id_, "title": titulo, "source": fuente, "vertical": vertical,
            "published_at": cuando, "category": f"{vertical}/futbol"}


def test_junta_lo_que_es_la_misma_historia():
    historias = dedupe.agrupar([
        articulo("1", "Real Madrid beat Barcelona 3-1 in the Clasico", "a"),
        articulo("2", "Real Madrid beats Barcelona 3-1 in Clasico thriller", "b"),
        articulo("3", "El Real Madrid gana 3-1 al Barcelona en el Clasico", "c"),
    ])
    assert len(historias) == 1
    assert historias[0].fuentes == 3


def test_no_junta_lo_que_no_tiene_que_ver():
    historias = dedupe.agrupar([
        articulo("1", "Real Madrid beat Barcelona in the Clasico", "a"),
        articulo("2", "El Congreso aprueba la reforma de las pensiones", "b", vertical="news"),
    ])
    assert len(historias) == 2


def test_una_misma_fuente_no_cuenta_dos_veces():
    historias = dedupe.agrupar([
        articulo("1", "Real Madrid beat Barcelona 3-1 in the Clasico", "a"),
        articulo("2", "Real Madrid beats Barcelona 3-1 in the Clasico", "a"),
    ])
    assert historias[0].fuentes == 1


def test_una_historia_grande_cruza_verticales():
    """Una compra millonaria es economia y es tecnologia: es la misma historia."""
    historias = dedupe.agrupar([
        articulo("1", "Apple compra una startup de inteligencia artificial", "a", vertical="tech"),
        articulo("2", "Apple compra una startup de inteligencia artificial", "b", vertical="news"),
    ])
    assert len(historias) == 1
    assert historias[0].fuentes == 2


def test_entre_verticales_se_pide_mas_parecido():
    """Con un parecido intermedio se juntan dentro de un nicho, pero no entre nichos."""
    parecidas = (
        "Apple compra una startup de inteligencia artificial",
        "Apple compra una empresa de inteligencia artificial hoy",
    )
    mismo_nicho = dedupe.agrupar([
        articulo("1", parecidas[0], "a", vertical="tech"),
        articulo("2", parecidas[1], "b", vertical="tech"),
    ])
    assert len(mismo_nicho) == 1

    distinto_nicho = dedupe.agrupar([
        articulo("1", parecidas[0], "a", vertical="tech"),
        articulo("2", parecidas[1], "b", vertical="news"),
    ])
    assert len(distinto_nicho) == 2


def test_la_ventana_de_tiempo_separa_historias_repetidas():
    """El mismo titular un año despues no es la misma noticia."""
    historias = dedupe.agrupar([
        articulo("1", "Real Madrid gana la final de la Champions", "a", cuando="2026-06-01T20:00:00Z"),
        articulo("2", "Real Madrid gana la final de la Champions", "b", cuando="2025-06-01T20:00:00Z"),
    ])
    assert len(historias) == 2


def test_manda_la_mas_reciente():
    historias = dedupe.agrupar([
        articulo("viejo", "Real Madrid beat Barcelona in the Clasico", "a", cuando="2026-08-25T10:00:00Z"),
        articulo("nuevo", "Real Madrid beats Barcelona in the Clasico", "b", cuando="2026-08-25T18:00:00Z"),
    ])
    assert historias[0].principal["id"] == "nuevo"


def test_el_titular_no_pierde_el_resultado():
    """El guion de "3-1" no es la coleta del medio."""
    assert "3-1" in dedupe.normalizar_titular("Gana 3-1 en el clasico")
    assert dedupe.normalizar_titular("Arsenal win the league - BBC Sport") == "arsenal win the league"


def test_ordena_por_numero_de_medios():
    historias = dedupe.agrupar([
        articulo("1", "Una noticia que cuenta un solo medio hoy", "a"),
        articulo("2", "Real Madrid beat Barcelona in the Clasico", "b"),
        articulo("3", "Real Madrid beats Barcelona in the Clasico", "c"),
    ])
    assert historias[0].fuentes == 2
