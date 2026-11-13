"""Casos de uso (capa de aplicación).

Dependen solo de los puertos del dominio, nunca de Flask ni SQLAlchemy,
por eso se pueden probar sin base de datos ni servidor web.
"""
from __future__ import annotations

import calendar as cal
from datetime import date, datetime
from decimal import Decimal

from ..domain.entities import Consumo, DetalleVenta, Factura, Producto, Reservacion, Venta
from ..domain.ports import (
    CanalExternoPort,
    ConsumoRepository,
    FacturaRepository,
    HabitacionRepository,
    HuespedRepository,
    ProductoRepository,
    ReservacionRepository,
    VentaRepository,
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

    def execute(self, reservacion_id: int, cliente: str | None = None, rfc: str | None = None, correo: str | None = None) -> Factura:
        """Genera la factura de una reservación.

        Los datos de facturación (nombre, RFC y correo) se piden al
        generar la factura; si no se dan, se usan los del huésped.
        """
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
            rfc=(rfc.strip().upper() if rfc else (huesped.rfc if huesped else "")),
            folio=folio,
            cliente=(cliente.strip() if cliente else (huesped.nombre if huesped else "")),
            correo=(correo.strip() if correo else (huesped.email if huesped else "")),
        )
        return self.facturas.guardar(factura)


class GenerarFacturaVenta:
    """Factura una venta de caja: el cliente elige recibo o factura.

    La factura lleva el folio TUR-V00000 (la V la distingue de las
    facturas de reservación, TUR-00000) y el mismo subtotal, IVA y total
    de la venta. Una venta solo se factura una vez: si ya tiene factura,
    se devuelve la existente.
    """

    def __init__(self, ventas, huespedes, facturas):
        self.ventas = ventas
        self.huespedes = huespedes
        self.facturas = facturas

    def execute(self, venta_id: int, cliente: str | None = None, rfc: str | None = None, correo: str | None = None) -> Factura:
        """Factura una venta de caja.

        Los datos de facturación (nombre, RFC y correo) se piden al
        generar la factura; si no se dan, se usan los de la venta
        y los del huésped, si la venta tiene uno.
        """
        existente = self.facturas.obtener_por_venta(venta_id)
        if existente:
            return existente
        venta = self.ventas.obtener(venta_id)
        if venta is None:
            raise ValueError("La venta no existe.")
        huesped = self.huespedes.obtener(venta.huesped_id) if venta.huesped_id else None
        factura = Factura.desde_venta(
            venta,
            folio=f"TUR-V{venta_id:05d}",
            rfc=(rfc.strip().upper() if rfc else (huesped.rfc if huesped else "")),
            correo=(correo.strip() if correo else (huesped.email if huesped else "")),
        )
        if cliente and cliente.strip():
            factura.cliente = cliente.strip()
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


class RegistrarVenta:
    """Cobra una venta de caja y genera su recibo.

    Los precios se resuelven en el servidor desde el catálogo y las
    habitaciones, nunca se confían los precios que manda el navegador.

    Items (cada uno un dict):
      - producto: {"tipo": "producto", "producto_id": 1, "cantidad": 2}
      - habitación: {"tipo": "habitacion", "habitacion_id": 5, "noches": 2}
        (la cantidad es 1 línea; el precio es precio_noche x noches)
    """

    def __init__(
        self,
        productos: ProductoRepository,
        habitaciones: HabitacionRepository,
        ventas: VentaRepository,
    ):
        self.productos = productos
        self.habitaciones = habitaciones
        self.ventas = ventas

    def execute(
        self,
        cliente: str,
        metodo_pago: str,
        items: list[dict],
        monto_recibido: Decimal | None = None,
        huesped_id: int | None = None,
    ) -> Venta:
        if not items:
            raise ValueError("La venta no tiene productos. Elige al menos uno.")
        detalles: list[DetalleVenta] = []
        # Productos con inventario: se descuenta lo vendido. Si un producto
        # tiene stock (número), no se puede vender más de lo que hay; si
        # no tiene stock (vacío), se vende sin control de inventario.
        por_descontar: list[tuple] = []
        for item in items:
            cantidad = int(item.get("cantidad", 1))
            if cantidad < 1:
                raise ValueError("La cantidad debe ser al menos 1.")
            if item.get("tipo") == "habitacion":
                habitacion = self.habitaciones.obtener(int(item["habitacion_id"]))
                if habitacion is None:
                    raise ValueError("La habitación no existe.")
                noches = int(item.get("noches", 1))
                if noches < 1:
                    raise ValueError("Las noches deben ser al menos 1.")
                detalles.append(DetalleVenta(
                    concepto=f"Habitación {habitacion.numero} ({habitacion.tipo}) — {noches} noche(s)",
                    categoria="habitaciones",
                    precio_unitario=Decimal(habitacion.precio_noche) * noches,
                    cantidad=cantidad,
                ))
            else:
                producto = self.productos.obtener(int(item["producto_id"]))
                if producto is None or not producto.activo:
                    raise ValueError("El producto no existe o no está en venta.")
                if producto.stock is not None and producto.stock < cantidad:
                    raise ValueError(
                        f"No hay suficiente stock de {producto.nombre} "
                        f"(quedan {producto.stock}). Actualiza el inventario "
                        "en Administrar productos."
                    )
                if producto.stock is not None:
                    por_descontar.append((producto, cantidad))
                detalles.append(DetalleVenta(
                    producto_id=producto.id,
                    concepto=producto.nombre,
                    categoria=producto.categoria,
                    precio_unitario=Decimal(producto.precio),
                    cantidad=cantidad,
                ))
        venta = Venta(
            fecha=datetime.now(),
            cliente=(cliente or "Cliente general").strip() or "Cliente general",
            huesped_id=huesped_id,
            items=detalles,
            metodo_pago=metodo_pago,
            monto_recibido=Decimal(monto_recibido) if monto_recibido is not None else Decimal("0"),
        )
        venta.calcular_totales()
        venta.validar_pago()
        for producto, cantidad in por_descontar:
            producto.stock -= cantidad
            self.productos.guardar(producto)
        return self.ventas.guardar(venta)


class EditarReservacion:
    """Cambia habitación, huésped o fechas de una reservación activa.

    Reaplica la regla anti-sobreventa (sin contar la propia reservación)
    y recalcula el total con el precio de la habitación. Solo se puede
    editar una reservación en estado reservada o check-in.
    """

    def __init__(self, reservaciones: ReservacionRepository, habitaciones: HabitacionRepository):
        self.reservaciones = reservaciones
        self.habitaciones = habitaciones

    def execute(
        self,
        reservacion_id: int,
        habitacion_id: int,
        huesped_id: int,
        fecha_entrada: date,
        fecha_salida: date,
    ) -> Reservacion:
        reserva = self.reservaciones.obtener(reservacion_id)
        if reserva is None:
            raise ValueError("La reservación no existe.")
        if reserva.estado not in ("reservada", "check-in"):
            raise ValueError(f"No se puede editar una reservación en estado '{reserva.estado}'.")
        habitacion = self.habitaciones.obtener(habitacion_id)
        if habitacion is None:
            raise ValueError("La habitación no existe.")
        reserva.habitacion_id = habitacion_id
        reserva.huesped_id = huesped_id
        reserva.fecha_entrada = fecha_entrada
        reserva.fecha_salida = fecha_salida
        reserva.validar_fechas()
        solapadas = self.reservaciones.buscar_solapadas(habitacion_id, fecha_entrada, fecha_salida, excluir_id=reserva.id)
        if solapadas:
            raise ValueError(
                f"La habitación {habitacion.numero} ya está reservada en esas fechas. "
                "Elige otras fechas u otra habitación para evitar la sobreventa."
            )
        reserva.total = reserva.calcular_total(habitacion.precio_noche)
        return self.reservaciones.guardar(reserva)


class ReporteVentas:
    """Reporte de ventas de un mes: demanda por producto y ganancia.

    Agrega las líneas de las ventas del mes por producto (cantidad,
    ingresos, costo y ganancia), por categoría, por método de pago y
    por día. La ganancia de cada producto es ingresos menos costo
    (costo capturado en el catálogo; si es 0, la ganancia es el ingreso).
    También trae las facturas del mes para verlas junto a las ventas.
    """

    def __init__(self, ventas: VentaRepository, facturas: FacturaRepository, productos: ProductoRepository):
        self.ventas = ventas
        self.facturas = facturas
        self.productos = productos

    def execute(self, anio: int, mes: int) -> dict:
        costos = {p.id: Decimal(p.costo or 0) for p in self.productos.listar(solo_activos=False)}
        ventas_mes = [
            v for v in self.ventas.listar()
            if v.fecha and v.fecha.year == anio and v.fecha.month == mes
        ]
        por_producto: dict[str, dict] = {}
        por_categoria: dict[str, dict] = {}
        por_metodo: dict[str, Decimal] = {}
        por_dia: dict[int, Decimal] = {}
        ingresos = Decimal("0")
        costos_total = Decimal("0")
        for v in ventas_mes:
            ingresos += Decimal(v.total)
            por_metodo[v.metodo_pago] = por_metodo.get(v.metodo_pago, Decimal("0")) + Decimal(v.total)
            por_dia[v.fecha.day] = por_dia.get(v.fecha.day, Decimal("0")) + Decimal(v.total)
            for item in v.items:
                linea_ingreso = Decimal(item.precio_unitario) * item.cantidad
                linea_costo = costos.get(item.producto_id, Decimal("0")) * item.cantidad if item.producto_id else Decimal("0")
                costos_total += linea_costo
                agg = por_producto.setdefault(item.concepto, {"concepto": item.concepto, "categoria": item.categoria, "cantidad": 0, "ingresos": Decimal("0"), "costos": Decimal("0")})
                agg["cantidad"] += item.cantidad
                agg["ingresos"] += linea_ingreso
                agg["costos"] += linea_costo
                cat = por_categoria.setdefault(item.categoria, {"categoria": item.categoria, "cantidad": 0, "ingresos": Decimal("0")})
                cat["cantidad"] += item.cantidad
                cat["ingresos"] += linea_ingreso
        productos_ordenados = sorted(por_producto.values(), key=lambda a: (-a["cantidad"], -a["ingresos"]))
        for agg in productos_ordenados:
            agg["ganancia"] = agg["ingresos"] - agg["costos"]
            agg["ingresos"] = float(agg["ingresos"])
            agg["costos"] = float(agg["costos"])
            agg["ganancia"] = float(agg["ganancia"])
        categorias = sorted(por_categoria.values(), key=lambda c: -c["ingresos"])
        for cat in categorias:
            cat["ingresos"] = float(cat["ingresos"])
        facturas_mes = [
            f for f in self.facturas.listar()
            if f.fecha_emision and f.fecha_emision.year == anio and f.fecha_emision.month == mes
        ]
        total_facturas = sum((Decimal(f.total) for f in facturas_mes), Decimal("0"))
        ganancia = ingresos - costos_total
        return {
            "anio": anio,
            "mes": mes,
            "num_ventas": len(ventas_mes),
            "ingresos": float(ingresos),
            "costos": float(costos_total),
            "ganancia": float(ganancia),
            "ticket_promedio": float(ingresos / len(ventas_mes)) if ventas_mes else 0.0,
            "por_producto": productos_ordenados,
            "por_categoria": categorias,
            "por_metodo": {k: float(v) for k, v in sorted(por_metodo.items())},
            "por_dia": {d: float(por_dia.get(d, Decimal("0"))) for d in range(1, cal.monthrange(anio, mes)[1] + 1)},
            "facturas": facturas_mes,
            "total_facturas": float(total_facturas),
            "num_facturas": len(facturas_mes),
        }


class CorteDia:
    """Corte de caja de un día: ventas, cobro por método y efectivo neto.

    El efectivo neto en caja es lo recibido en efectivo menos el cambio
    entregado. También cuenta las facturas emitidas ese día.
    """

    def __init__(self, ventas: VentaRepository, facturas: FacturaRepository):
        self.ventas = ventas
        self.facturas = facturas

    def execute(self, dia: date) -> dict:
        ventas_dia = [v for v in self.ventas.listar() if v.fecha and v.fecha.date() == dia]
        total = sum((Decimal(v.total) for v in ventas_dia), Decimal("0"))
        por_metodo: dict[str, Decimal] = {}
        recibido_efectivo = Decimal("0")
        cambio_total = Decimal("0")
        for v in ventas_dia:
            por_metodo[v.metodo_pago] = por_metodo.get(v.metodo_pago, Decimal("0")) + Decimal(v.total)
            if v.metodo_pago == "efectivo":
                recibido_efectivo += Decimal(v.monto_recibido)
                cambio_total += Decimal(v.cambio)
        facturas_dia = [f for f in self.facturas.listar() if f.fecha_emision == dia]
        total_facturas = sum((Decimal(f.total) for f in facturas_dia), Decimal("0"))
        return {
            "dia": dia,
            "num_ventas": len(ventas_dia),
            "total_ventas": float(total),
            "efectivo": float(por_metodo.get("efectivo", Decimal("0"))),
            "tarjeta": float(por_metodo.get("tarjeta", Decimal("0"))),
            "transferencia": float(por_metodo.get("transferencia", Decimal("0"))),
            "recibido_efectivo": float(recibido_efectivo),
            "cambio_total": float(cambio_total),
            "efectivo_neto": float(recibido_efectivo - cambio_total),
            "num_facturas": len(facturas_dia),
            "total_facturas": float(total_facturas),
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
