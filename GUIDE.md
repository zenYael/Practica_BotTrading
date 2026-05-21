# Guía Técnica: Alpha-Quant Trading System v5.4

Esta guía detalla la arquitectura y el funcionamiento de cada característica del sistema de trading autónomo.

---

## 1. Arquitectura de Datos en Tiempo Real (Resiliencia)
El sistema utiliza WebSockets de Binance para recibir cada cambio de precio (tick) sin latencia.

- **Cómo funciona**: La función `handle_socket` gestiona una conexión persistente. Si hay una caída de red, un **Backoff Exponencial** espera antes de reconectar.
- **Sincronización (Gap Filling)**: Al reconectar, la función `sync_historical_data` descarga klines vía REST para asegurar que no falten velas en los indicadores técnicos.
- **Optimización**: El procesamiento de datos está desacoplado de la UI. Los ticks actualizan el DataFrame en memoria en milisegundos, pero la UI solo se refresca cada 10 segundos para ahorrar CPU.

## 2. Persistencia y Business Intelligence (DB)
Utilizamos **SQLAlchemy** con **SQLite** para un almacenamiento profesional.

- **Tablas**:
    - `price_ticks`: Guarda cada micro-movimiento para análisis de alta frecuencia.
    - `trading_signals`: Registra cada decisión del bot, permitiendo auditorías de por qué se compró o vendió.
    - `wallet` y `paper_trades`: Almacenan el estado de la simulación financiera.
- **Ventaja**: Estos datos son la base para crear dashboards en herramientas como PowerBI o Tableau en el futuro.

## 3. Motor AutoML (Selección por PnL)
A diferencia de sistemas estáticos, este bot elige su propia estrategia dinámicamente.

- **Evaluación**: Cada 10 segundos, la función `backtest_strategy` simula el rendimiento de 3 modelos (Scalping, Cruce EMA, Volatilidad) sobre las últimas 60 velas.
- **Selección**: El bot elige el modelo que maximiza el **Retorno Neto** tras descontar comisiones (0.2%).
- **Gestión de Riesgo**: Si una estrategia tiene un **Max Drawdown** superior al 5%, el sistema le aplica una penalización masiva para evitar elegir modelos excesivamente volátiles.

## 4. Simulador de Cartera (Paper Trading)
Permite probar la rentabilidad real sin poner en riesgo capital verdadero.

- **Billetera Virtual**: Inicia con **$1,000 USD**. Al recibir una señal de "BUY", el bot simula la compra del activo y actualiza los saldos en la DB.
- **Realismo**: Incluye comisiones del 0.1% por operación, simulando el entorno real de Binance (Spot).

## 5. Inteligencia Artificial Narrativa (Gemini)
Integra el modelo **Gemini 1.5/2.0 Flash** para dar contexto humano a los datos.

- **Funcionamiento**: Envía el resumen de precios y el sentimiento del mercado a la IA.
- **Resultado**: Genera un **Executive Summary** que explica narrativamente por qué el mercado está en "Miedo" o "Codicia" y cómo eso influye en la estrategia actual.

## 6. Dashboard BI (Plotly)
Visualización avanzada para supervisión técnica.

- **Estructura**: Paneles divididos de Precio y RSI. Incluye Bandas de Bollinger y EMA 200.
- **Escalabilidad**: Usa `plotly.min.js` de forma externa. El archivo `dashboard.html` es ultra-ligero (<10KB), permitiendo refrescos instantáneos en el navegador.

---

## 7. Próximas Mejoras Sugeridas (Roadmap)

### A. Análisis Multi-Temporal (Multi-Timeframe)
- **Mejora**: Validar señales de 5m con la tendencia de 1h.
- **Impacto**: Reducción drástica de "falsas señales" en mercados laterales.

### B. Notificaciones Externas
- **Mejora**: Integrar un bot de **Telegram** o **Discord**.
- **Impacto**: Recibir alertas de compras/ventas y el resumen de Gemini directamente en el móvil.

### C. Optimización de Gestión de Capital (Kelly Criterion)
- **Mejora**: Ajustar el tamaño de la posición basado en la tasa de acierto histórica del modelo.
- **Impacto**: Maximización del crecimiento de la cuenta a largo plazo.

### D. Ejecución Real (Trade API)
- **Mejora**: Cambiar de `Paper Trading` a órdenes reales usando las API Keys con permisos de "Spot Trading".
- **Impacto**: El bot pasa de simulador a herramienta de generación de ingresos real.

### E. Despliegue en la Nube (Docker)
- **Mejora**: Contenerizar la aplicación para correr en un VPS (AWS/Google Cloud).
- **Impacto**: Disponibilidad del 99.9% sin depender de que tu ordenador personal esté encendido.

---
*Documentación generada por el equipo de Alpha-Quant BI*
