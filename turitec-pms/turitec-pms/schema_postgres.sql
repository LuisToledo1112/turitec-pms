-- Turitec PMS — Esquema PostgreSQL
-- Crear la base antes o usar docker-compose (ya crea la BD "turitec").
-- Las tablas se crean también automáticamente con SQLAlchemy (db.create_all()).

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL,
    email VARCHAR(120) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    rol VARCHAR(30) NOT NULL DEFAULT 'recepcion', -- administrador | recepcion
    activo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS habitaciones (
    id SERIAL PRIMARY KEY,
    numero VARCHAR(10) UNIQUE NOT NULL,
    tipo VARCHAR(30) NOT NULL, -- Sencilla | Doble | Suite
    precio_noche NUMERIC(10,2) NOT NULL,
    capacidad INTEGER NOT NULL DEFAULT 2,
    estado VARCHAR(30) NOT NULL DEFAULT 'libre', -- libre | ocupada | limpieza | mantenimiento
    descripcion TEXT
);

CREATE TABLE IF NOT EXISTS huespedes (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(150) NOT NULL,
    email VARCHAR(120),
    telefono VARCHAR(30),
    rfc VARCHAR(13),
    documento VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS reservaciones (
    id SERIAL PRIMARY KEY,
    habitacion_id INTEGER NOT NULL REFERENCES habitaciones(id),
    huesped_id INTEGER NOT NULL REFERENCES huespedes(id),
    fecha_entrada DATE NOT NULL,
    fecha_salida DATE NOT NULL,
    estado VARCHAR(30) NOT NULL DEFAULT 'reservada', -- reservada | check-in | check-out | cancelada
    total NUMERIC(10,2) NOT NULL DEFAULT 0,
    origen VARCHAR(30) NOT NULL DEFAULT 'directo', -- directo | booking | airbnb
    creada_en TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (fecha_salida > fecha_entrada)
);

-- Índice para la validación anti-sobreventa (búsqueda de solapamientos)
CREATE INDEX IF NOT EXISTS idx_reservaciones_hab_fechas
    ON reservaciones (habitacion_id, fecha_entrada, fecha_salida);

CREATE TABLE IF NOT EXISTS consumos (
    id SERIAL PRIMARY KEY,
    reservacion_id INTEGER NOT NULL REFERENCES reservaciones(id),
    concepto VARCHAR(150) NOT NULL,
    monto NUMERIC(10,2) NOT NULL,
    fecha DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS facturas (
    id SERIAL PRIMARY KEY,
    folio VARCHAR(20) UNIQUE NOT NULL,
    reservacion_id INTEGER NOT NULL REFERENCES reservaciones(id),
    huesped_id INTEGER NOT NULL REFERENCES huespedes(id),
    fecha_emision DATE NOT NULL DEFAULT CURRENT_DATE,
    subtotal NUMERIC(10,2) NOT NULL,
    iva NUMERIC(10,2) NOT NULL,
    total NUMERIC(10,2) NOT NULL,
    rfc VARCHAR(13),
    estado VARCHAR(30) NOT NULL DEFAULT 'emitida'
);

CREATE TABLE IF NOT EXISTS canales (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(50) UNIQUE NOT NULL, -- booking | airbnb | directo
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_sincronizacion TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sincronizaciones (
    id SERIAL PRIMARY KEY,
    canal VARCHAR(50) NOT NULL,
    habitacion_id INTEGER REFERENCES habitaciones(id),
    fecha_inicio DATE,
    fecha_fin DATE,
    disponible BOOLEAN NOT NULL,
    resultado VARCHAR(255),
    creada_en TIMESTAMP NOT NULL DEFAULT NOW()
);
