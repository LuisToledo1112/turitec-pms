"""Tests de reporte de ventas, corte del día y edición de reservaciones."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from src.turitec.application.use_cases import CorteDia, EditarReservacion, ReporteVentas
from src.turitec.domain.entities import (
    DetalleVenta, Factura, Habitacion, Producto, Reservacion, Venta,
)


class FakeProductos:
    def __init__(self, productos):
        self._productos = {p.id: p for p in productos}

    def listar(self, solo_activos=True):
        return [p for p in self._productos.values() if not solo_activos or p.activo]

    def obtener(self, producto_id):
        return self._productos.get(producto_id)

    def guardar(self, producto):
        self._productos[producto.id] = producto
        return producto


class FakeHabitaciones:
    def __init__(self, habitaciones):
        self._habitaciones = {h.id: h for h in habitaciones}

    def listar(self):
        return list(self._habitaciones.values())

    def obtener(self, habitacion_id):
        return self._habitaciones.get(habitacion_id)

    def guardar(self, habitacion):
        self._habitaciones[habitacion.id] = habitacion
        return habitacion

    def eliminar(self, habitacion_id):
        self._habitaciones.pop(habitacion_id, None)


class FakeReservaciones:
    def __init__(self, reservas):
        self._reservas = {r.id: r for r in reservas}

    def listar(self):
        return list(self._reservas.values())

    def obtener(self, reservacion_id):
        return self._reservas.get(reservacion_id)

    def guardar(self, reserva):
        if reserva.id is None:
            reserva.id = max(self._reservas, default=0) + 1
        self._reservas[reserva.id] = reserva
        return reserva

    def buscar_solapadas(self, habitacion_id, entrada, salida, excluir_id=None):
        return [
            r for r in self._reservas.values()
            if r.id != excluir_id and r.se_solapa_con(
                Reservacion(habitacion_id=habitacion_id, fecha_entrada=entrada, fecha_salida=salida)
            )
        ]

    def listar_por_habitacion(self, habitacion_id):
        return [r for r in self._reservas.values() if r.habitacion_id == habitacion_id]


class FakeVentas:
    def __init__(self, ventas):
        self._ventas = list(ventas)

    def listar(self):
        return list(self._ventas)

    def obtener(self, venta_id):
        return next((v for v in self._ventas if v.id == venta_id), None)

    def guardar(self, venta):
        if venta.id is None:
            venta.id = len(self._ventas) + 1
        if not venta.folio:
            venta.folio = f"VTA-{venta.id:05d}"
        self._ventas.append(venta)
        return venta


class FakeFacturas:
    def __init__(self, facturas):
        self._facturas = list(facturas)

    def listar(self):
        return list(self._facturas)

    def obtener(self, factura_id):
        return next((f for f in self._facturas if f.id == factura_id), None)

    def guardar(self, factura):
        self._facturas.append(factura)
        return factura

    def obtener_por_reservacion(self, reservacion_id):
        return next((f for f in self._facturas if f.reservacion_id == reservacion_id), None)


def _venta(vid, fecha, metodo, items, recibido=None):
    venta = Venta(
        id=vid, folio=f"VTA-{vid:05d}", fecha=fecha, cliente="Cliente",
        items=items, metodo_pago=metodo,
        monto_recibido=Decimal(recibido) if recibido is not None else Decimal("0"),
    )
    venta.calcular_totales()
    venta.validar_pago()
    return venta


@pytest.fixture
def datos_ventas():
    productos = FakeProductos([
        Producto(id=1, nombre="Café americano", categoria="alimentos", precio=Decimal("35"), costo=Decimal("10")),
        Producto(id=2, nombre="Desayuno buffet", categoria="alimentos", precio=Decimal("120"), costo=Decimal("60")),
        Producto(id=3, nombre="Paquete Romántico", categoria="paquetes", precio=Decimal("2500"), costo=Decimal("1800")),
    ])
    ventas = FakeVentas([
        _venta(1, datetime(2026, 11, 11, 9, 0), "efectivo",
               [DetalleVenta(concepto="Café americano", categoria="alimentos", producto_id=1, precio_unitario=Decimal("35"), cantidad=3)],
               recibido="150"),
        _venta(2, datetime(2026, 11, 11, 13, 0), "tarjeta",
               [DetalleVenta(concepto="Desayuno buffet", categoria="alimentos", producto_id=2, precio_unitario=Decimal("120"), cantidad=2),
                DetalleVenta(concepto="Paquete Romántico", categoria="paquetes", producto_id=3, precio_unitario=Decimal("2500"), cantidad=1)]),
    ])
    facturas = FakeFacturas([
        Factura(id=1, folio="TUR-00001", reservacion_id=1, huesped_id=1,
                fecha_emision=date(2026, 11, 6), subtotal=Decimal("2310"), iva=Decimal("369.60"), total=Decimal("2679.60")),
    ])
    return productos, ventas, facturas


def test_reporte_ventas_agrega_por_producto_y_ganancia(datos_ventas):
    productos, ventas, facturas = datos_ventas
    reporte = ReporteVentas(ventas, facturas, productos).execute(2026, 11)
    assert reporte["num_ventas"] == 2
    # Ingresos: (105 + 2740) * 1.16 = 121.80 + 3178.40 = 3300.20
    assert reporte["ingresos"] == pytest.approx(3300.20)
    # Costos: café 3x10 + desayuno 2x60 + paquete 1x1800 = 1950
    assert reporte["costos"] == pytest.approx(1950.0)
    assert reporte["ganancia"] == pytest.approx(1350.20)
    por_producto = {p["concepto"]: p for p in reporte["por_producto"]}
    assert por_producto["Café americano"]["cantidad"] == 3
    assert por_producto["Café americano"]["ganancia"] == pytest.approx(105 - 30)
    assert por_producto["Paquete Romántico"]["ganancia"] == pytest.approx(2500 - 1800)
    # El más vendido primero
    assert reporte["por_producto"][0]["concepto"] == "Café americano"
    assert reporte["por_metodo"]["efectivo"] == pytest.approx(121.80)
    assert reporte["por_metodo"]["tarjeta"] == pytest.approx(3178.40)
    assert reporte["por_dia"][11] == pytest.approx(3300.20)
    assert reporte["num_facturas"] == 1
    assert reporte["total_facturas"] == pytest.approx(2679.60)


def test_reporte_ventas_sin_ventas(datos_ventas):
    productos, ventas, facturas = datos_ventas
    reporte = ReporteVentas(ventas, facturas, productos).execute(2026, 10)
    assert reporte["num_ventas"] == 0
    assert reporte["ingresos"] == 0.0
    assert reporte["ganancia"] == 0.0
    assert reporte["por_producto"] == []


def test_corte_dia_efectivo_neto(datos_ventas):
    productos, ventas, facturas = datos_ventas
    corte = CorteDia(ventas, facturas).execute(date(2026, 11, 11))
    assert corte["num_ventas"] == 2
    assert corte["total_ventas"] == pytest.approx(3300.20)
    # Efectivo: venta 1 total 121.80, recibió 150, cambio 28.20 -> neto 121.80
    assert corte["efectivo"] == pytest.approx(121.80)
    assert corte["recibido_efectivo"] == pytest.approx(150.0)
    assert corte["cambio_total"] == pytest.approx(28.20)
    assert corte["efectivo_neto"] == pytest.approx(121.80)
    assert corte["tarjeta"] == pytest.approx(3178.40)
    assert corte["num_facturas"] == 0


def test_editar_reservacion_cambia_fechas_y_total():
    habitaciones = FakeHabitaciones([Habitacion(id=5, numero="105", tipo="Sencilla", precio_noche=Decimal("700"))])
    reservas = FakeReservaciones([
        Reservacion(id=1, habitacion_id=5, huesped_id=1, fecha_entrada=date(2026, 11, 12), fecha_salida=date(2026, 11, 14), estado="reservada", total=Decimal("1400")),
    ])
    actualizada = EditarReservacion(reservas, habitaciones).execute(
        1, 5, 1, date(2026, 11, 20), date(2026, 11, 23))
    assert actualizada.fecha_entrada == date(2026, 11, 20)
    assert actualizada.total == Decimal("2100")  # 3 noches x 700


def test_editar_reservacion_rechaza_solape_con_otra():
    habitaciones = FakeHabitaciones([Habitacion(id=5, numero="105", tipo="Sencilla", precio_noche=Decimal("700"))])
    reservas = FakeReservaciones([
        Reservacion(id=1, habitacion_id=5, huesped_id=1, fecha_entrada=date(2026, 11, 12), fecha_salida=date(2026, 11, 14), estado="reservada", total=Decimal("1400")),
        Reservacion(id=2, habitacion_id=5, huesped_id=2, fecha_entrada=date(2026, 11, 20), fecha_salida=date(2026, 11, 22), estado="reservada", total=Decimal("1400")),
    ])
    with pytest.raises(ValueError, match="ya está reservada"):
        EditarReservacion(reservas, habitaciones).execute(
            1, 5, 1, date(2026, 11, 21), date(2026, 11, 23))


def test_editar_reservacion_sus_propias_fechas_no_chocan():
    habitaciones = FakeHabitaciones([Habitacion(id=5, numero="105", tipo="Sencilla", precio_noche=Decimal("700"))])
    reservas = FakeReservaciones([
        Reservacion(id=1, habitacion_id=5, huesped_id=1, fecha_entrada=date(2026, 11, 12), fecha_salida=date(2026, 11, 14), estado="reservada", total=Decimal("1400")),
    ])
    # Guardar sin cambiar fechas: no debe rechazar por solaparse consigo misma.
    actualizada = EditarReservacion(reservas, habitaciones).execute(
        1, 5, 1, date(2026, 11, 12), date(2026, 11, 14))
    assert actualizada.total == Decimal("1400")


def test_editar_reservacion_con_check_out_rechazada():
    habitaciones = FakeHabitaciones([Habitacion(id=5, numero="105", tipo="Sencilla", precio_noche=Decimal("700"))])
    reservas = FakeReservaciones([
        Reservacion(id=1, habitacion_id=5, huesped_id=1, fecha_entrada=date(2026, 11, 3), fecha_salida=date(2026, 11, 6), estado="check-out", total=Decimal("2100")),
    ])
    with pytest.raises(ValueError, match="No se puede editar"):
        EditarReservacion(reservas, habitaciones).execute(
            1, 5, 1, date(2026, 11, 20), date(2026, 11, 22))
