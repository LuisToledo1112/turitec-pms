"""Casos de uso (capa de aplicación).

Dependen solo de los puertos del dominio, nunca de Flask ni SQLAlchemy,
por eso se pueden probar sin base de datos ni servidor web.
"""
from __future__ import annotations

import calendar as cal
from datetime import date
from decimal import Decimal

from ..domain.entities import Consumo, Factura, Reservacion
from ..domain.ports import (
    CanalExternoPort,
    ConsumoRepository,
    FacturaRepository,
    HabitacionRepository,
    HuespedRepository,
    ReservacionRepository,
)


class CrearReservacion:
    def __init__(self, reservaciones: ReservacionRepository, habitaciones: HabitacionRepository):
        self.reservaciones = reservaciones
        self.habitaciones = habitaciones

    def execute(
        self,
        habitacion_id: int,
        huesped_id: int,
        fecha_entrada: date,
        fecha_salida: date,
        origen: str = "directo",
    ) -> Reservacion:
        habitacion = self.habitaciones.obtener(habitacion_id)
        if habitacion is None:
            raise ValueError("La habitación no existe.")
        if habitacion.estado == "mantenimiento":
            raise ValueError(f"La habitación {habitacion.numero} está en mantenimiento.")
        reserva = Reservacion(
            habitacion_id=habitacion_id,
            huesped_id=huesped_id,
            fecha_entrada=fecha_entrada,
            fecha_salida=fecha_salida,
            estado="reservada",
            origen=origen,
        )
        reserva.validar_fechas()
        # Regla anti-sobreventa: no puede solaparse con otra activa.
        solapadas = self.reservaciones.buscar_solapadas(habitacion_id, fecha_entrada, fecha_salida)
        if solapadas:
            raise ValueError(
                f"La habitación {habitacion.numero} ya está reservada en esas fechas. "
                "Elige otras fechas u otra habitación para evitar la sobreventa."
            )
        reserva.total = reserva.calcular_total(habitacion.precio_noche)
        return self.reservaciones.guardar(reserva)


class HacerCheckIn:
    def __init__(self, reservaciones: ReservacionRepository, habitaciones: HabitacionRepository):
        self.reservaciones = reservaciones
        self.habitaciones = habitaciones

    def execute(self, reservacion_id: int) -> Reservacion:
        reserva = self.reservaciones.obtener(reservacion_id)
        if reserva is None:
            raise ValueError("La reservación no existe.")
        reserva.hacer_check_in()
        guardada = self.reservaciones.guardar(reserva)
        habitacion = self.habitaciones.obtener(reserva.habitacion_id)
        if habitacion:
            habitacion.estado = "ocupada"
            self.habitaciones.guardar(habitacion)
        return guardada


class HacerCheckOut:
    def __init__(self, reservaciones: ReservacionRepository, habitaciones: HabitacionRepository):
        self.reservaciones = reservaciones
        self.habitaciones = habitaciones

    def execute(self, reservacion_id: int) -> Reservacion:
        reserva = self.reservaciones.obtener(reservacion_id)
        if reserva is None:
            raise ValueError("La reservación no existe.")
        reserva.hacer_check_out()
        guardada = self.reservaciones.guardar(reserva)
        habitacion = self.habitaciones.obtener(reserva.habitacion_id)
        if habitacion:
            habitacion.estado = "libre"
            self.habitaciones.guardar(habitacion)
        return guardada


class CancelarReservacion:
    def __init__(self, reservaciones: ReservacionRepository):
        self.reservaciones = reservaciones

    def execute(self, reservacion_id: int) -> Reservacion:
        reserva = self.reservaciones.obtener(reservacion_id)
        if reserva is None:
            raise ValueError("La reservación no existe.")
        reserva.cancelar()
        return self.reservaciones.guardar(reserva)


class GenerarFactura:
    def __init__(
        self,
        reservaciones: ReservacionRepository,
        habitaciones: HabitacionRepository,
        huespedes: HuespedRepository,
        facturas: FacturaRepository,
        consumos: ConsumoRepository,
    ):
        self.reservaciones = reservaciones
        self.habitaciones = habitaciones
        self.huespedes = huespedes
        self.facturas = facturas
        self.consumos = consumos

    def execute(self, reservacion_id: int) -> Factura:
        existente = self.facturas.obtener_por_reservacion(reservacion_id)
        if existente:
            return existente
        reserva = self.reservaciones.obtener(reservacion_id)
        if reserva is None:
            raise ValueError("La reservación no existe.")
        if reserva.estado not in ("check-in", "check-out"):
            raise ValueError("Solo se puede facturar una reservación con check-in o check-out.")
        habitacion = self.habitaciones.obtener(reserva.habitacion_id)
        huesped = self.huespedes.obtener(reserva.huesped_id)
        lista_consumos = self.consumos.listar_por_reservacion(reservacion_id)
        folio = f"TUR-{reservacion_id:05d}"
        factura = Factura.desde_reservacion(
            reserva,
            precio_noche=habitacion.precio_noche if habitacion else Decimal("0"),
            consumos=lista_consumos,
            rfc=huesped.rfc if huesped else "",
            folio=folio,
        )
        return self.facturas.guardar(factura)


class ReporteOcupacionMensual:
    """Cuartos ocupados, tasa de ocupación e ingresos de un mes."""

    def __init__(
        self,
        reservaciones: ReservacionRepository,
        habitaciones: HabitacionRepository,
        facturas: FacturaRepository,
    ):
        self.reservaciones = reservaciones
        self.habitaciones = habitaciones
        self.facturas = facturas

    def execute(self, anio: int, mes: int) -> dict:
        habitaciones = self.habitaciones.listar()
        total_habitaciones = len(habitaciones)
        dias_mes = cal.monthrange(anio, mes)[1]
        inicio = date(anio, mes, 1)
        fin = date(anio, mes, dias_mes)
        # Noches ocupadas: días de reservaciones activas dentro del mes
        noches_ocupadas = 0
        cuartos_ocupados = set()
        for r in self.reservaciones.listar():
            if r.estado == "cancelada":
                continue
            if r.fecha_salida > inicio and r.fecha_entrada <= fin:
                # Noches dentro del mes: intersección [entrada, salida) con [inicio, fin+1 día)
                from datetime import timedelta

                fin_mes_excl = fin + timedelta(days=1)
                a = max(r.fecha_entrada, inicio)
                b = min(r.fecha_salida, fin_mes_excl)
                noches = max(0, (b - a).days)
                noches_ocupadas += noches
                if noches:
                    cuartos_ocupados.add(r.habitacion_id)
        capacidad = total_habitaciones * dias_mes
        tasa = (noches_ocupadas / capacidad * 100) if capacidad else 0.0
        ingresos = sum(
            (Decimal(f.total) for f in self.facturas.listar()
             if f.fecha_emision and f.fecha_emision.year == anio and f.fecha_emision.month == mes),
            Decimal("0"),
        )
        # Ingresos por día (para el gráfico de barras)
        ingresos_por_dia: dict[int, float] = {d: 0.0 for d in range(1, dias_mes + 1)}
        for f in self.facturas.listar():
            if f.fecha_emision and f.fecha_emision.year == anio and f.fecha_emision.month == mes:
                ingresos_por_dia[f.fecha_emision.day] += float(f.total)
        return {
            "anio": anio,
            "mes": mes,
            "dias_mes": dias_mes,
            "total_habitaciones": total_habitaciones,
            "noches_ocupadas": noches_ocupadas,
            "cuartos_ocupados": len(cuartos_ocupados),
            "capacidad_noches": capacidad,
            "tasa_ocupacion": round(tasa, 1),
            "ingresos": float(ingresos),
            "ingresos_por_dia": ingresos_por_dia,
        }


class SincronizarDisponibilidad:
    """Empuja la disponibilidad a los canales externos (simulado)."""

    def __init__(
        self,
        canal: CanalExternoPort,
        habitaciones: HabitacionRepository,
        reservaciones: ReservacionRepository,
    ):
        self.canal = canal
        self.habitaciones = habitaciones
        self.reservaciones = reservaciones

    def execute(self, fecha_inicio: date, fecha_fin: date, canales: list[str] | None = None) -> list[dict]:
        canales = canales or ["booking", "airbnb"]
        resultados = []
        for habitacion in self.habitaciones.listar():
            solapadas = self.reservaciones.buscar_solapadas(
                habitacion.id, fecha_inicio, fecha_fin
            )
            disponible = not solapadas and habitacion.estado != "mantenimiento"
            for nombre_canal in canales:
                resultados.append(
                    self.canal.sincronizar_disponibilidad(
                        nombre_canal, habitacion.id, fecha_inicio, fecha_fin, disponible
                    )
                )
        return resultados
