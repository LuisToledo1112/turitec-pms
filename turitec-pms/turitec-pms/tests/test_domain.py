"""Pruebas del dominio de Turitec PMS (sin Flask ni base de datos).

Se ejecutan con: pytest
"""
from datetime import date
from decimal import Decimal

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from turitec.application.use_cases import (
    CrearReservacion, GenerarFactura, HacerCheckIn, HacerCheckOut, ReporteOcupacionMensual,
)
from turitec.domain.entities import Factura, Habitacion, Huesped, Reservacion


# ---- Repositorios en memoria (fakes que implementan los puertos) ----
class RepoHabitaciones:
    def __init__(self, habitaciones):
        self.datos = {h.id: h for h in habitaciones}

    def listar(self):
        return list(self.datos.values())

    def obtener(self, hid):
        return self.datos.get(hid)

    def guardar(self, h):
        self.datos[h.id] = h
        return h

    def eliminar(self, hid):
        self.datos.pop(hid, None)


class RepoReservaciones:
    def __init__(self):
        self.datos = {}
        self.seq = 1

    def listar(self):
        return list(self.datos.values())

    def obtener(self, rid):
        return self.datos.get(rid)

    def guardar(self, r):
        if r.id is None:
            r.id = self.seq
            self.seq += 1
        self.datos[r.id] = r
        return r

    def buscar_solapadas(self, habitacion_id, entrada, salida, excluir_id=None):
        candidata = Reservacion(habitacion_id=habitacion_id, fecha_entrada=entrada, fecha_salida=salida)
        return [
            r for r in self.datos.values()
            if r.id != excluir_id and r.estado in ("reservada", "check-in") and candidata.se_solapa_con(r)
        ]

    def listar_por_habitacion(self, habitacion_id):
        return [r for r in self.datos.values() if r.habitacion_id == habitacion_id]


class RepoFacturas:
    def __init__(self):
        self.datos = {}

    def listar(self):
        return list(self.datos.values())

    def obtener(self, fid):
        return self.datos.get(fid)

    def guardar(self, f):
        if f.id is None:
            f.id = len(self.datos) + 1
        self.datos[f.id] = f
        return f

    def obtener_por_reservacion(self, rid):
        return next((f for f in self.datos.values() if f.reservacion_id == rid), None)


class RepoConsumos:
    def listar_por_reservacion(self, rid):
        return []

    def guardar(self, c):
        return c


class RepoHuespedes:
    def __init__(self, huespedes):
        self.datos = {h.id: h for h in huespedes}

    def listar(self):
        return list(self.datos.values())

    def obtener(self, hid):
        return self.datos.get(hid)

    def guardar(self, h):
        self.datos[h.id] = h
        return h

    def eliminar(self, hid):
        self.datos.pop(hid, None)


@pytest.fixture
def entorno():
    hab = RepoHabitaciones([Habitacion(id=1, numero="101", tipo="Sencilla", precio_noche=Decimal("650"))])
    res = RepoReservaciones()
    hues = RepoHuespedes([Huesped(id=1, nombre="Ana López", rfc="LOMA850101ABC")])
    return hab, res, hues


def test_solapamiento_rechazado(entorno):
    hab, res, _ = entorno
    crear = CrearReservacion(res, hab)
    crear.execute(1, 1, date(2026, 11, 10), date(2026, 11, 13))
    # Se solapa: 12 al 15 se cruza con 10 al 13
    with pytest.raises(ValueError, match="ya está reservada"):
        crear.execute(1, 1, date(2026, 11, 12), date(2026, 11, 15))
    # No se solapa: la salida de una es la entrada de otra
    segunda = crear.execute(1, 1, date(2026, 11, 13), date(2026, 11, 15))
    assert segunda.noches() == 2


def test_noches_y_total_correctos(entorno):
    hab, res, _ = entorno
    reserva = CrearReservacion(res, hab).execute(1, 1, date(2026, 11, 10), date(2026, 11, 13))
    assert reserva.noches() == 3
    assert reserva.total == Decimal("1950")  # 3 x 650


def test_fechas_invalidas_rechazadas(entorno):
    hab, res, _ = entorno
    with pytest.raises(ValueError):
        CrearReservacion(res, hab).execute(1, 1, date(2026, 11, 13), date(2026, 11, 10))


def test_check_in_y_check_out(entorno):
    hab, res, _ = entorno
    reserva = CrearReservacion(res, hab).execute(1, 1, date(2026, 11, 10), date(2026, 11, 12))
    HacerCheckIn(res, hab).execute(reserva.id)
    assert res.obtener(reserva.id).estado == "check-in"
    assert hab.obtener(1).estado == "ocupada"
    HacerCheckOut(res, hab).execute(reserva.id)
    assert res.obtener(reserva.id).estado == "check-out"
    assert hab.obtener(1).estado == "libre"
    # Ya no se puede hacer check-out dos veces
    with pytest.raises(ValueError):
        HacerCheckOut(res, hab).execute(reserva.id)


def test_factura_con_iva(entorno):
    hab, res, hues = entorno
    reserva = CrearReservacion(res, hab).execute(1, 1, date(2026, 11, 10), date(2026, 11, 12))
    HacerCheckIn(res, hab).execute(reserva.id)
    HacerCheckOut(res, hab).execute(reserva.id)
    factura = GenerarFactura(res, hab, hues, RepoFacturas(), RepoConsumos()).execute(reserva.id)
    assert factura.subtotal == Decimal("1300")  # 2 x 650
    assert factura.iva == Decimal("208.00")  # 16%
    assert factura.total == Decimal("1508.00")
    assert factura.rfc == "LOMA850101ABC"


def test_reporte_ocupacion_mensual(entorno):
    hab, res, _ = entorno
    crear = CrearReservacion(res, hab)
    crear.execute(1, 1, date(2026, 11, 10), date(2026, 11, 13))  # 3 noches
    reporte = ReporteOcupacionMensual(res, hab, RepoFacturas()).execute(2026, 11)
    assert reporte["noches_ocupadas"] == 3
    assert reporte["total_habitaciones"] == 1
    assert reporte["tasa_ocupacion"] == round(3 / 30 * 100, 1)
