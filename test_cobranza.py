"""Tests de cobranza: productos, ventas, pago y recibo."""
from datetime import datetime
from decimal import Decimal

import pytest

from src.turitec.application.use_cases import RegistrarVenta
from src.turitec.domain.entities import Habitacion, Producto, Venta


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


class FakeVentas:
    def __init__(self):
        self.ventas = []

    def listar(self):
        return list(self.ventas)

    def obtener(self, venta_id):
        return next((v for v in self.ventas if v.id == venta_id), None)

    def guardar(self, venta):
        if venta.id is None:
            venta.id = len(self.ventas) + 1
        if not venta.folio:
            venta.folio = f"VTA-{venta.id:05d}"
        self.ventas.append(venta)
        return venta


@pytest.fixture
def catalogo():
    productos = FakeProductos([
        Producto(id=1, nombre="Desayuno buffet", categoria="alimentos", precio=Decimal("120")),
        Producto(id=2, nombre="Paquete Romántico", categoria="paquetes", precio=Decimal("2500")),
    ])
    habitaciones = FakeHabitaciones([
        Habitacion(id=5, numero="105", tipo="Sencilla", precio_noche=Decimal("700")),
    ])
    return productos, habitaciones, FakeVentas()


def test_totales_con_iva(catalogo):
    productos, habitaciones, ventas = catalogo
    venta = RegistrarVenta(productos, habitaciones, ventas).execute(
        cliente="Ana María López", metodo_pago="tarjeta",
        items=[{"tipo": "producto", "producto_id": 1, "cantidad": 2},
               {"tipo": "producto", "producto_id": 2, "cantidad": 1}],
    )
    assert venta.subtotal == Decimal("2740")
    assert venta.iva == Decimal("438.40")
    assert venta.total == Decimal("3178.40")
    assert venta.folio == "VTA-00001"
    assert venta.monto_recibido == venta.total
    assert venta.cambio == Decimal("0")


def test_habitacion_se_cobra_por_noches(catalogo):
    productos, habitaciones, ventas = catalogo
    venta = RegistrarVenta(productos, habitaciones, ventas).execute(
        cliente="Cliente general", metodo_pago="tarjeta",
        items=[{"tipo": "habitacion", "habitacion_id": 5, "noches": 2}],
    )
    assert venta.items[0].concepto == "Habitación 105 (Sencilla) — 2 noche(s)"
    assert venta.items[0].precio_unitario == Decimal("1400")
    assert venta.subtotal == Decimal("1400")
    assert venta.total == Decimal("1624.00")


def test_efectivo_con_cambio(catalogo):
    productos, habitaciones, ventas = catalogo
    venta = RegistrarVenta(productos, habitaciones, ventas).execute(
        cliente="Cliente general", metodo_pago="efectivo",
        items=[{"tipo": "producto", "producto_id": 1, "cantidad": 1}],
        monto_recibido=Decimal("200"),
    )
    # Subtotal 120 + IVA 19.20 = 139.20; pagó 200 -> cambio 60.80
    assert venta.total == Decimal("139.20")
    assert venta.cambio == Decimal("60.80")


def test_efectivo_insuficiente_rechazado(catalogo):
    productos, habitaciones, ventas = catalogo
    with pytest.raises(ValueError, match="menor que el total"):
        RegistrarVenta(productos, habitaciones, ventas).execute(
            cliente="Cliente general", metodo_pago="efectivo",
            items=[{"tipo": "producto", "producto_id": 1, "cantidad": 1}],
            monto_recibido=Decimal("100"),
        )


def test_venta_sin_productos_rechazada(catalogo):
    productos, habitaciones, ventas = catalogo
    with pytest.raises(ValueError, match="no tiene productos"):
        RegistrarVenta(productos, habitaciones, ventas).execute(
            cliente="Cliente general", metodo_pago="efectivo", items=[],
        )


def test_producto_inexistente_rechazado(catalogo):
    productos, habitaciones, ventas = catalogo
    with pytest.raises(ValueError, match="no existe"):
        RegistrarVenta(productos, habitaciones, ventas).execute(
            cliente="Cliente general", metodo_pago="tarjeta",
            items=[{"tipo": "producto", "producto_id": 99, "cantidad": 1}],
        )


def test_metodo_invalido_rechazado(catalogo):
    productos, habitaciones, ventas = catalogo
    with pytest.raises(ValueError, match="Método de pago no válido"):
        RegistrarVenta(productos, habitaciones, ventas).execute(
            cliente="Cliente general", metodo_pago="cheque",
            items=[{"tipo": "producto", "producto_id": 1, "cantidad": 1}],
        )


def test_venta_entidad_calcula_totales():
    venta = Venta(
        fecha=datetime(2026, 11, 11, 10, 0),
        cliente="Prueba",
        items=[],
        metodo_pago="efectivo",
        monto_recibido=Decimal("0"),
    )
    from src.turitec.domain.entities import DetalleVenta

    venta.items = [DetalleVenta(concepto="Café americano", categoria="alimentos", precio_unitario=Decimal("35"), cantidad=2)]
    venta.calcular_totales()
    assert venta.subtotal == Decimal("70")
    assert venta.iva == Decimal("11.20")
    assert venta.total == Decimal("81.20")
