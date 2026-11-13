"""Entidades de dominio de Turitec PMS.

Este módulo NO depende de Flask, SQLAlchemy ni de ninguna librería web.
Solo contiene las reglas de negocio del hotel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

IVA = Decimal("0.16")

# Estados válidos de una reservación
ESTADOS_RESERVACION = ("reservada", "check-in", "check-out", "cancelada")


@dataclass
class Habitacion:
    id: int | None = None
    numero: str = ""
    tipo: str = "Sencilla"  # Sencilla | Doble | Suite
    precio_noche: Decimal = Decimal("0")
    capacidad: int = 2
    estado: str = "libre"  # libre | ocupada | limpieza | mantenimiento
    descripcion: str = ""


@dataclass
class Huesped:
    id: int | None = None
    nombre: str = ""
    email: str = ""
    telefono: str = ""
    rfc: str = ""
    documento: str = ""
    # Id de Telegram del huésped (cuando reservó por el chatbot): así el
    # bot sabe que esa reservación es suya y solo él puede consultarla.
    telegram_user_id: int | None = None


@dataclass
class Consumo:
    id: int | None = None
    reservacion_id: int | None = None
    concepto: str = ""
    monto: Decimal = Decimal("0")
    fecha: date | None = None


@dataclass
class Reservacion:
    id: int | None = None
    habitacion_id: int | None = None
    huesped_id: int | None = None
    fecha_entrada: date | None = None
    fecha_salida: date | None = None
    estado: str = "reservada"
    total: Decimal = Decimal("0")
    origen: str = "directo"  # directo | booking | airbnb

    def noches(self) -> int:
        """Número de noches entre entrada y salida."""
        if not self.fecha_entrada or not self.fecha_salida:
            return 0
        return max(0, (self.fecha_salida - self.fecha_entrada).days)

    def calcular_total(self, precio_noche: Decimal) -> Decimal:
        """Total de hospedaje: noches x precio por noche."""
        return Decimal(self.noches()) * Decimal(precio_noche)

    def se_solapa_con(self, otra: "Reservacion") -> bool:
        """True si ambas reservaciones se cruzan en fechas.

        Regla anti-sobreventa: dos reservaciones de la misma habitación
        no pueden ocupar los mismos días. La salida de una puede ser la
        entrada de otra (ese día la habitación se libera y se vuelve a ocupar).
        """
        if self.habitacion_id != otra.habitacion_id:
            return False
        if self.estado == "cancelada" or otra.estado == "cancelada":
            return False
        if not self.fecha_entrada or not self.fecha_salida:
            return False
        if not otra.fecha_entrada or not otra.fecha_salida:
            return False
        return self.fecha_entrada < otra.fecha_salida and otra.fecha_entrada < self.fecha_salida

    def validar_fechas(self) -> None:
        if not self.fecha_entrada or not self.fecha_salida:
            raise ValueError("La reservación necesita fecha de entrada y de salida.")
        if self.fecha_salida <= self.fecha_entrada:
            raise ValueError("La fecha de salida debe ser posterior a la fecha de entrada.")

    # --- Máquina de estados: reservada -> check-in -> check-out / cancelada ---
    def hacer_check_in(self) -> None:
        if self.estado != "reservada":
            raise ValueError(f"No se puede hacer check-in desde el estado '{self.estado}'.")
        self.estado = "check-in"

    def hacer_check_out(self) -> None:
        if self.estado != "check-in":
            raise ValueError(f"No se puede hacer check-out desde el estado '{self.estado}'.")
        self.estado = "check-out"

    def cancelar(self) -> None:
        if self.estado == "check-out":
            raise ValueError("No se puede cancelar una reservación con check-out.")
        if self.estado == "cancelada":
            raise ValueError("La reservación ya está cancelada.")
        self.estado = "cancelada"


@dataclass
class Factura:
    """Una factura emitida: de una reservación o de una venta de caja.

    Antes solo existían facturas de reservación (``reservacion_id``).
    Una venta de caja también puede facturarse: entonces trae
    ``venta_id`` y ``reservacion_id`` queda vacío. ``cliente`` es el
    nombre de quien paga (para ventas sin huésped registrado).
    """

    id: int | None = None
    folio: str = ""
    reservacion_id: int | None = None
    huesped_id: int | None = None
    fecha_emision: date | None = None
    subtotal: Decimal = Decimal("0")
    iva: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    rfc: str = ""
    estado: str = "emitida"
    venta_id: int | None = None
    cliente: str = ""
    correo: str = ""  # correo de facturación (para enviarle la factura)

    @classmethod
    def desde_reservacion(
        cls,
        reservacion: Reservacion,
        precio_noche: Decimal,
        consumos: list[Consumo] | None = None,
        rfc: str = "",
        folio: str = "",
        cliente: str = "",
        correo: str = "",
    ) -> "Factura":
        """Genera la factura: (noches x precio + consumos) + IVA 16%."""
        consumos = consumos or []
        hospedaje = reservacion.calcular_total(precio_noche)
        total_consumos = sum((Decimal(c.monto) for c in consumos), Decimal("0"))
        subtotal = hospedaje + total_consumos
        iva = (subtotal * IVA).quantize(Decimal("0.01"))
        total = subtotal + iva
        return cls(
            folio=folio,
            reservacion_id=reservacion.id,
            huesped_id=reservacion.huesped_id,
            fecha_emision=date.today(),
            subtotal=subtotal,
            iva=iva,
            total=total,
            rfc=rfc,
            cliente=cliente,
            correo=correo,
        )

    @classmethod
    def desde_venta(cls, venta: "Venta", folio: str, rfc: str = "", correo: str = "") -> "Factura":
        """Genera la factura de una venta de caja (recibo con IVA desglosado)."""
        return cls(
            folio=folio,
            venta_id=venta.id,
            huesped_id=venta.huesped_id,
            fecha_emision=date.today(),
            subtotal=venta.subtotal,
            iva=venta.iva,
            total=venta.total,
            rfc=rfc,
            cliente=venta.cliente,
            correo=correo,
        )


@dataclass
class Usuario:
    id: int | None = None
    nombre: str = ""
    email: str = ""
    password_hash: str = ""
    rol: str = "recepcion"  # administrador | recepcion
    activo: bool = True


# ---------------- Cobranza (caja) ----------------
# Métodos de pago aceptados en caja
METODOS_PAGO = ("efectivo", "tarjeta", "transferencia")

# Categorías de productos en venta
CATEGORIAS_PRODUCTO = ("alimentos", "paquetes")


@dataclass
class Producto:
    """Algo en venta en la posada: alimentos o paquetes.

    Las habitaciones NO son productos: se cobran por noches desde
    el catálogo de habitaciones (precio por noche x noches).
    ``costo`` es lo que le cuesta al hotel (para calcular si hay
    ganancia o pérdida); si no se captura, la ganancia es el ingreso.
    ``stock`` es la cantidad en inventario: si tiene número, cada venta
    lo descuenta y no deja vender más de lo que hay; si está vacío
    (None), el producto se vende sin control de inventario.
    """

    id: int | None = None
    nombre: str = ""
    categoria: str = "alimentos"  # alimentos | paquetes
    precio: Decimal = Decimal("0")
    costo: Decimal = Decimal("0")
    descripcion: str = ""
    activo: bool = True
    stock: int | None = None


@dataclass
class DetalleVenta:
    """Una línea de la venta: concepto, precio y cantidad."""

    id: int | None = None
    venta_id: int | None = None
    producto_id: int | None = None  # None cuando es una habitación
    concepto: str = ""
    categoria: str = "alimentos"  # alimentos | paquetes | habitaciones
    precio_unitario: Decimal = Decimal("0")
    cantidad: int = 1

    def subtotal(self) -> Decimal:
        return Decimal(self.cantidad) * Decimal(self.precio_unitario)


@dataclass
class Venta:
    """Una venta de caja con su pago y su recibo.

    El total siempre se calcula en el servidor (nunca se confía en
    los precios que manda el navegador): subtotal + IVA 16%.
    En efectivo el cliente debe dar al menos el total y se devuelve
    cambio; con tarjeta o transferencia el monto cobrado es el total.
    """

    id: int | None = None
    folio: str = ""
    fecha: datetime | None = None
    cliente: str = "Cliente general"
    huesped_id: int | None = None
    items: list[DetalleVenta] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")
    iva: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    metodo_pago: str = "efectivo"  # efectivo | tarjeta | transferencia
    monto_recibido: Decimal = Decimal("0")
    cambio: Decimal = Decimal("0")
    estado: str = "pagada"

    def calcular_totales(self) -> None:
        self.subtotal = sum((i.subtotal() for i in self.items), Decimal("0"))
        self.iva = (self.subtotal * IVA).quantize(Decimal("0.01"))
        self.total = self.subtotal + self.iva

    def validar_pago(self) -> None:
        if not self.items:
            raise ValueError("La venta no tiene productos. Elige al menos uno.")
        if self.metodo_pago not in METODOS_PAGO:
            raise ValueError(f"Método de pago no válido: '{self.metodo_pago}'.")
        if self.metodo_pago == "efectivo":
            if self.monto_recibido < self.total:
                raise ValueError(
                    f"El monto recibido (${self.monto_recibido:,.2f}) es menor que "
                    f"el total (${self.total:,.2f})."
                )
            self.cambio = self.monto_recibido - self.total
        else:
            # Tarjeta o transferencia: se cobra exacto, sin cambio.
            self.monto_recibido = self.total
            self.cambio = Decimal("0")
