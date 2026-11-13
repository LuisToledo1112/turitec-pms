"""Entidades de dominio de Turitec PMS.

Este módulo NO depende de Flask, SQLAlchemy ni de ninguna librería web.
Solo contiene las reglas de negocio del hotel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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

    @classmethod
    def desde_reservacion(
        cls,
        reservacion: Reservacion,
        precio_noche: Decimal,
        consumos: list[Consumo] | None = None,
        rfc: str = "",
        folio: str = "",
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
        )


@dataclass
class Usuario:
    id: int | None = None
    nombre: str = ""
    email: str = ""
    password_hash: str = ""
    rol: str = "recepcion"  # administrador | recepcion
    activo: bool = True
