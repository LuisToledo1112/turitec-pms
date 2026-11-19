"""Puertos (interfaces) de la arquitectura hexagonal.

Los casos de uso dependen solo de estas interfaces, nunca de Flask
ni de SQLAlchemy. Los adaptadores concretos viven en infrastructure/.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal

from .entities import Consumo, Factura, Habitacion, Huesped, Producto, Reservacion, Usuario, Venta


class HabitacionRepository(ABC):
    @abstractmethod
    def listar(self) -> list[Habitacion]: ...

    @abstractmethod
    def obtener(self, habitacion_id: int) -> Habitacion | None: ...

    @abstractmethod
    def guardar(self, habitacion: Habitacion) -> Habitacion: ...

    @abstractmethod
    def eliminar(self, habitacion_id: int) -> None: ...


class HuespedRepository(ABC):
    @abstractmethod
    def listar(self) -> list[Huesped]: ...

    @abstractmethod
    def obtener(self, huesped_id: int) -> Huesped | None: ...

    @abstractmethod
    def guardar(self, huesped: Huesped) -> Huesped: ...

    @abstractmethod
    def eliminar(self, huesped_id: int) -> None: ...


class ReservacionRepository(ABC):
    @abstractmethod
    def listar(self) -> list[Reservacion]: ...

    @abstractmethod
    def obtener(self, reservacion_id: int) -> Reservacion | None: ...

    @abstractmethod
    def guardar(self, reservacion: Reservacion) -> Reservacion: ...

    @abstractmethod
    def buscar_solapadas(
        self, habitacion_id: int, entrada: date, salida: date, excluir_id: int | None = None
    ) -> list[Reservacion]:
        """Reservaciones activas (reservada / check-in) que se cruzan en fechas."""

    @abstractmethod
    def listar_por_habitacion(self, habitacion_id: int) -> list[Reservacion]: ...


class FacturaRepository(ABC):
    @abstractmethod
    def listar(self) -> list[Factura]: ...

    @abstractmethod
    def obtener(self, factura_id: int) -> Factura | None: ...

    @abstractmethod
    def guardar(self, factura: Factura) -> Factura: ...

    @abstractmethod
    def obtener_por_reservacion(self, reservacion_id: int) -> Factura | None: ...

    @abstractmethod
    def obtener_por_venta(self, venta_id: int) -> Factura | None: ...


class ConsumoRepository(ABC):
    @abstractmethod
    def listar_por_reservacion(self, reservacion_id: int) -> list[Consumo]: ...

    @abstractmethod
    def guardar(self, consumo: Consumo) -> Consumo: ...


class UsuarioRepository(ABC):
    @abstractmethod
    def obtener_por_email(self, email: str) -> Usuario | None: ...

    @abstractmethod
    def guardar(self, usuario: Usuario) -> Usuario: ...


class ProductoRepository(ABC):
    """Catálogo de productos en venta (alimentos y paquetes)."""

    @abstractmethod
    def listar(self, solo_activos: bool = True) -> list[Producto]: ...

    @abstractmethod
    def obtener(self, producto_id: int) -> Producto | None: ...

    @abstractmethod
    def guardar(self, producto: Producto) -> Producto: ...


class VentaRepository(ABC):
    """Ventas de caja. Al guardar, asigna el folio si viene vacío."""

    @abstractmethod
    def listar(self) -> list[Venta]: ...

    @abstractmethod
    def obtener(self, venta_id: int) -> Venta | None: ...

    @abstractmethod
    def guardar(self, venta: Venta) -> Venta: ...


class CanalExternoPort(ABC):
    """Puerto de salida hacia plataformas externas (Booking, Airbnb...)."""

    @abstractmethod
    def sincronizar_disponibilidad(
        self, canal: str, habitacion_id: int, fecha_inicio: date, fecha_fin: date, disponible: bool
    ) -> dict:
        """Empuja la disponibilidad de una habitación a un canal. Devuelve el resultado."""


class NotificacionPort(ABC):
    """Puerto de salida para avisar al personal (Telegram, correo, etc.).

    Un aviso nunca debe tumbar la operación principal: los adaptadores
    devuelven False si no pudieron enviar, sin lanzar excepciones.
    """

    @abstractmethod
    def enviar(self, mensaje: str) -> bool:
        """Envía ``mensaje`` al destinatario configurado. True si llegó."""
