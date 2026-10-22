"""Reglas del Auto-Match programado (sin BD)."""
from datetime import date, datetime, time
from types import SimpleNamespace

from app.modulos.conciliacion import auto_match as A


def cuenta(frec="DIARIA", hora=time(4, 0), intervalo=1440, ultima=None, activo=True, hab=True):
    return SimpleNamespace(activo=activo, auto_match_habilitado=hab, auto_match_frecuencia=frec,
                           auto_match_hora=hora, auto_match_intervalo_minutos=intervalo,
                           auto_match_ultima_ejecucion=ultima)


MIE = datetime(2026, 10, 21, 0, 0)  # miércoles


def at(h, m=0, base=MIE):
    return base.replace(hour=h, minute=m)


def test_diaria_una_vez_al_dia():
    assert not A.toca_ejecutar(cuenta(), at(3, 59))
    assert A.toca_ejecutar(cuenta(), at(4, 0))
    assert not A.toca_ejecutar(cuenta(ultima=at(4, 1)), at(23, 0))          # ya corrió hoy
    assert A.toca_ejecutar(cuenta(ultima=at(4, 1)), at(4, 0).replace(day=22))  # al día siguiente


def test_diaria_recupera_si_el_servidor_estuvo_apagado():
    assert A.toca_ejecutar(cuenta(ultima=at(4, 0).replace(day=19)), at(15, 30))


def test_diaria_con_intervalo():
    c = cuenta(intervalo=60, ultima=at(4, 0))
    assert not A.toca_ejecutar(c, at(4, 59))
    assert A.toca_ejecutar(c, at(5, 0))


def test_semanal_y_mensual():
    lunes = datetime(2026, 10, 19)
    assert not A.toca_ejecutar(cuenta("SEMANAL"), at(10))
    assert A.toca_ejecutar(cuenta("SEMANAL"), at(10, base=lunes))
    assert not A.toca_ejecutar(cuenta("SEMANAL", ultima=at(4, 5, lunes)), at(10, base=lunes))
    uno = datetime(2026, 11, 1)
    assert A.toca_ejecutar(cuenta("MENSUAL"), at(4, base=uno))
    assert not A.toca_ejecutar(cuenta("MENSUAL"), at(10))


def test_deshabilitada_o_inactiva():
    assert not A.toca_ejecutar(cuenta(hab=False), at(10))
    assert not A.toca_ejecutar(cuenta(activo=False), at(10))


def test_periodos():
    assert A.periodos_programados(date(2026, 10, 21)) == ["2026-10"]
    assert A.periodos_programados(date(2026, 1, 5)) == ["2026-01", "2025-12"]
    assert A.periodos_entre(date(2026, 8, 25), date(2026, 10, 3)) == ["2026-10", "2026-09", "2026-08"]
    assert A.periodos_entre(None, date(2026, 10, 3)) == []
