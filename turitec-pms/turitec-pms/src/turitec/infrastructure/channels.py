"""Adaptador simulado de canales externos (Booking / Airbnb / directo).

NO hace llamadas reales a ninguna plataforma: registra la sincronización
en la base de datos y en memoria, para demostrar el flujo sin credenciales.
El servicio Node.js de services/channel_sync hace lo mismo por HTTP.
"""
from __future__ import annotations

from datetime import date

from ..domain.ports import CanalExternoPort
from .db import SincronizacionModel, db


class CanalExternoSimulado(CanalExternoPort):
    """Adaptador en memoria + BD que simula empujar disponibilidad."""

    def __init__(self):
        self.registro: list[dict] = []

    def sincronizar_disponibilidad(self, canal, habitacion_id, fecha_inicio, fecha_fin, disponible):
        resultado = {
            "canal": canal,
            "habitacion_id": habitacion_id,
            "fecha_inicio": str(fecha_inicio),
            "fecha_fin": str(fecha_fin),
            "disponible": disponible,
            "estado": "sincronizado (simulado)",
            "mensaje": f"Disponibilidad {'libre' if disponible else 'no disponible'} enviada a {canal} (simulación, sin llamada real).",
        }
        self.registro.append(resultado)
        try:
            db.session.add(SincronizacionModel(
                canal=canal, habitacion_id=habitacion_id,
                fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
                disponible=disponible, resultado=resultado["estado"],
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
        return resultado


# Instancia compartida para la app
canal_simulado = CanalExternoSimulado()
