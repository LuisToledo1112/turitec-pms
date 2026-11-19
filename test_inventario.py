"""Tests del inventario de productos y de facturar ventas desde Caja."""
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from decimal import Decimal

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")

from src.turitec import create_app  # noqa: E402
from src.turitec.application.use_cases import RegistrarVenta  # noqa: E402
from src.turitec.domain.entities import DetalleVenta, Producto, Venta  # noqa: E402
from src.turitec.infrastructure.db import FacturaModel, ProductoModel, VentaModel, db  # noqa: E402


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
    def listar(self):
        return []

    def obtener(self, habitacion_id):
        return None


class FakeVentas:
    def __init__(self):
        self._ventas = []

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


def _venta_con_stock(stock, cantidad):
    productos = FakeProductos([
        Producto(id=1, nombre="Café americano", categoria="alimentos",
                 precio=Decimal("35"), stock=stock),
    ])
    ventas = FakeVentas()
    caso = RegistrarVenta(productos, FakeHabitaciones(), ventas)
    return caso, productos, ventas


def test_venta_descuenta_stock():
    caso, productos, ventas = _venta_con_stock(10, 3)
    venta = caso.execute("Cliente", "efectivo",
                         [{"tipo": "producto", "producto_id": 1, "cantidad": 3}],
                         monto_recibido=Decimal("150"))
    assert venta is not None
    assert productos.obtener(1).stock == 7


def test_venta_sin_stock_suficiente_se_bloquea():
    caso, productos, ventas = _venta_con_stock(2, 3)
    with pytest.raises(ValueError, match="No hay suficiente stock"):
        caso.execute("Cliente", "efectivo",
                     [{"tipo": "producto", "producto_id": 1, "cantidad": 3}],
                     monto_recibido=Decimal("150"))
    assert productos.obtener(1).stock == 2  # no se descontó nada
    assert ventas.listar() == []  # no se guardó la venta


def test_venta_sin_control_de_stock_no_tiene_limite():
    caso, productos, ventas = _venta_con_stock(None, 50)
    venta = caso.execute("Cliente", "efectivo",
                         [{"tipo": "producto", "producto_id": 1, "cantidad": 50}],
                         monto_recibido=Decimal("3000"))
    assert venta is not None
    assert productos.obtener(1).stock is None


# ---------------- Inventario en la app web ----------------

@pytest.fixture
def cliente():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/login", data={"email": "admin@turitec.mx", "password": "admin123"})
        yield c, app


def _agregar_producto(c, nombre, stock):
    c.post("/caja/productos", data={
        "nombre": nombre, "categoria": "alimentos", "precio": "35",
        "costo": "10", "descripcion": "Prueba de inventario.", "stock": str(stock),
    }, follow_redirects=True)


def _id_producto(app, nombre):
    with app.app_context():
        return ProductoModel.query.filter_by(nombre=nombre).first().id


def _cobrar_producto(c, producto_id, cantidad):
    items = json.dumps([{"tipo": "producto", "producto_id": producto_id, "cantidad": cantidad}])
    return c.post("/caja/cobrar", data={
        "cliente": "Cliente general", "metodo_pago": "efectivo",
        "monto_recibido": "5000", "items": items, "documento": "recibo",
    }, follow_redirects=True)


def test_inventario_agregar_editar_y_vender_descuenta(cliente):
    c, app = cliente
    _agregar_producto(c, "Café Inventario", 5)
    pid = _id_producto(app, "Café Inventario")
    # La caja muestra el stock.
    r = c.get("/caja")
    assert "Stock: 5".encode() in r.data
    # Vender 2 descuenta el inventario.
    _cobrar_producto(c, pid, 2)
    with app.app_context():
        assert ProductoModel.query.get(pid).stock == 3
    # Editar la cantidad a 8.
    c.post(f"/caja/productos/{pid}/editar", data={
        "nombre": "Café Inventario", "categoria": "alimentos", "precio": "35",
        "costo": "10", "descripcion": "Prueba de inventario.", "stock": "8", "activo": "on",
    }, follow_redirects=True)
    with app.app_context():
        assert ProductoModel.query.get(pid).stock == 8
    # Vender más de lo que hay se bloquea.
    r = _cobrar_producto(c, pid, 9)
    assert "No hay suficiente stock".encode() in r.data
    with app.app_context():
        assert ProductoModel.query.get(pid).stock == 8


def test_inventario_bajo_stock_se_marca_y_agotado_no_se_vende(cliente):
    c, app = cliente
    _agregar_producto(c, "Té Inventario", 3)
    r = c.get("/caja/productos")
    assert "bajo".encode() in r.data
    pid = _id_producto(app, "Té Inventario")
    _cobrar_producto(c, pid, 3)
    r = c.get("/caja")
    assert "Agotado".encode() in r.data


def test_facturar_desde_ultimas_ventas_de_caja(cliente):
    c, app = cliente
    _agregar_producto(c, "Jugo Inventario", 10)
    pid = _id_producto(app, "Jugo Inventario")
    _cobrar_producto(c, pid, 1)
    with app.app_context():
        venta = VentaModel.query.order_by(VentaModel.id.desc()).first()
    # En Caja, la venta tiene botón Facturar.
    r = c.get("/caja")
    assert "Facturar".encode() in r.data
    # Facturarla desde ahí: pide los datos y luego genera la factura.
    r = c.get(f"/caja/recibo/{venta.id}/factura")
    assert r.status_code == 200
    assert "RFC".encode() in r.data
    r = c.post(f"/caja/recibo/{venta.id}/factura", data={
        "cliente": "Cliente general", "rfc": "XAXX010101000", "correo": "",
    }, follow_redirects=False)
    assert r.status_code == 302 and "/facturacion/" in r.headers["Location"]
    # Ahora en Caja aparece Ver factura.
    r = c.get("/caja")
    assert "Ver factura".encode() in r.data


def test_migracion_productos_viejos_sin_stock():
    """La BD vieja (productos sin costo ni stock) se migra sola al
    arrancar, con cantidad inicial para seguir vendiendo."""
    dbpath = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(dbpath)
    con.execute(
        "CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre VARCHAR(150) NOT NULL,"
        " categoria VARCHAR(30) NOT NULL, precio NUMERIC(10,2) NOT NULL,"
        " descripcion TEXT, activo BOOLEAN DEFAULT 1)"
    )
    con.execute(
        "INSERT INTO productos (nombre, categoria, precio, descripcion, activo)"
        " VALUES ('Café viejo', 'alimentos', 35, 'De antes.', 1)"
    )
    con.commit()
    con.close()
    anterior = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite:///" + dbpath
    try:
        app = create_app()
        with app.app_context():
            cols = {c["name"] for c in __import__("sqlalchemy").inspect(db.engine).get_columns("productos")}
            assert "costo" in cols and "stock" in cols
            viejo = ProductoModel.query.filter_by(nombre="Café viejo").first()
            assert viejo is not None and viejo.stock == 100
    finally:
        if anterior is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = anterior


def test_agotado_vuelve_con_nuevo_stock(cliente):
    """Con 0 en stock dice Agotado hasta que se registre nuevo stock."""
    import re

    c, app = cliente
    _agregar_producto(c, "Café Prueba Stock", 1)
    pid = _id_producto(app, "Café Prueba Stock")
    _cobrar_producto(c, pid, 1)
    # Quedó en 0: Agotado en el inventario y en Caja.
    r = c.get("/caja/productos")
    assert "Agotado".encode() in r.data
    r = c.get("/caja")
    assert "Agotado".encode() in r.data
    # Al registrar nuevo stock vuelve a aparecer disponible.
    c.post(f"/caja/productos/{pid}/editar", data={
        "nombre": "Café Prueba Stock", "categoria": "alimentos", "precio": "35",
        "costo": "10", "descripcion": "Prueba de inventario.", "stock": "10", "activo": "on",
    }, follow_redirects=True)
    r = c.get("/caja")
    texto = r.get_data(as_text=True)
    assert "Stock: 10" in texto
    boton = re.search(r'<button[^>]*data-nombre="Café Prueba Stock"[^>]*>', texto)
    assert boton is not None and "disabled" not in boton.group(0)
    assert "Agotado" not in boton.group(0)
    # Y en el inventario su fila ya no dice Agotado.
    r = c.get("/caja/productos")
    texto = r.get_data(as_text=True)
    fila = re.search(r"<tr>.*?Café Prueba Stock.*?</tr>", texto, re.DOTALL)
    assert fila is not None and "Agotado" not in fila.group(0)
