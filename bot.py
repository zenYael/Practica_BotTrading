import asyncio
import os
import json
import webbrowser
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from google import genai
from dotenv import load_dotenv
from binance import AsyncClient, BinanceSocketManager
import pandas_ta as ta
from sqlalchemy import select
from database import DatabaseManager, PaperTrade, PriceTick
from metrics import (
    retrofit_missing_strategies,
    calculate_closed_trades,
    reconstruct_equity_curves,
    calculate_portfolio_metrics,
    run_historical_backtest,
    strategy_scalping_rsi,
    strategy_ema_cross,
    strategy_volatility,
    backtest_strategy
)
from aiohttp import web

# ===============================================
# CONFIGURACIÓN E INICIALIZACIÓN
# ===============================================
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("ERROR: No se encontró la GEMINI_API_KEY en el archivo .env o variables de entorno.")
    exit()

SYMBOLS = ["BTCUSDT", "ETHUSDT"]  # Binance usa USDT en lugar de -USD
INTERVAL = "1s"
TRAILING_STOP = 0.002
EMA_FAST = 5
EMA_SLOW = 15

console = Console()
db = DatabaseManager()

# Variables para servidor web y WebSockets
active_connections = set()
last_state_payload = None
PORT = 8080

# ===============================================
# ANALISIS EXTERNO Y REPORTES
# ===============================================
async def get_market_sentiment():
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: requests.get("https://api.alternative.me/fng/", timeout=10))
        response.raise_for_status()
        data = response.json()
        val = data['data'][0]['value']
        text = data['data'][0]['value_classification']
        return f"{val} ({text})"
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]Error de red en sentimiento: {e}[/yellow]")
        return "N/A (Net Err)"
    except (KeyError, IndexError, ValueError) as e:
        console.print(f"[red]Error en formato de datos de sentimiento: {e}[/red]")
        return "N/A (Data Err)"
    except Exception as e:
        console.print(f"[red]Error inesperado en sentimiento: {e}[/red]")
        return "N/A"

def generate_markdown_report(summary, sentiment):
    report = f"# ALPHA-QUANT TRADING REPORT\n"
    report += f"**Ultima Sincronización:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report += f"**Sentimiento del Mercado (Fear & Greed):** {sentiment}\n\n"
    report += "## Resumen de Activos\n"
    report += "| Asset | Price | Strategy | Decision |\n"
    report += "|-------|-------|----------|----------|\n"
    for s in summary:
        report += f"| {s['Sym']} | {s['Price']} | {s['Model']} | {s['Signal']} |\n"
    
    report += f"\n---\n*Reporte generado automáticamente por Alpha-Quant Trading System con Persistencia DB*"
    with open("trading_report.md", "w", encoding="utf-8") as f:
        f.write(report)

# ===============================================
# Las estrategias e indicadores ahora se definen en metrics.py e importan al inicio

# ===============================================
# PROCESAMIENTO TÉCNICO
# ===============================================
def process_indicators(df):
    try:
        # Asegurarnos de que el índice sea Datetime para Plotly
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, unit='ms')
            
        # Agrupar los datos en tiempo real usando Pandas para generar velas rápidas de 5 segundos
        df = df.resample('5s').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()
        
        df["rsi"] = ta.rsi(df["Close"], length=5)
        df["ema_fast"] = ta.ema(df["Close"], length=5)
        df["ema_slow"] = ta.ema(df["Close"], length=15)
        sma20 = df["Close"].rolling(window=10).mean()
        std20 = df["Close"].rolling(window=10).std()
        df["bb_upper"] = sma20 + (std20 * 1.2)
        df["bb_lower"] = sma20 - (std20 * 1.2)
        
        # Patrones de velas
        signals_cdl = np.zeros(len(df))
        close_arr = df['Close'].to_numpy()
        open_arr = df['Open'].to_numpy()
        high_arr = df['High'].to_numpy()
        low_arr = df['Low'].to_numpy()
        for i in range(2, len(df)):
            body = abs(close_arr[i] - open_arr[i])
            if body > 0 and (min(open_arr[i], close_arr[i]) - low_arr[i]) > body * 2: signals_cdl[i] = 1 
            if close_arr[i-1] < open_arr[i-1] and close_arr[i] > open_arr[i] and close_arr[i] > open_arr[i-1]: signals_cdl[i] = 1 
        df["cdl_signal"] = signals_cdl
        return df.dropna()
    except Exception as e:
        console.print(f"[yellow]Error en indicadores: {e}[/yellow]")
        return df

# ===============================================
# MOTOR DE DASHBOARD HTML
# ===============================================
# ===============================================
# SERVIDOR WEB Y CONTROLADORES DE WEBSOCKET
# ===============================================
async def handle_index(request):
    try:
        if os.path.exists("dashboard.html"):
            with open("dashboard.html", "r", encoding="utf-8") as f:
                html = f.read()
            return web.Response(text=html, content_type='text/html')
        else:
            return web.Response(text="dashboard.html no encontrado.", status=404)
    except Exception as e:
        return web.Response(text=f"Error al cargar el dashboard: {e}", status=500)

async def handle_plotly(request):
    try:
        if os.path.exists("plotly.min.js"):
            return web.FileResponse("plotly.min.js")
        else:
            return web.Response(text="plotly.min.js no encontrado.", status=404)
    except Exception as e:
        return web.Response(text=f"Error: {e}", status=500)

async def handle_websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    active_connections.add(ws)
    console.print(f"[bold cyan][WS] Cliente conectado. Activos: {len(active_connections)}[/bold cyan]")
    
    if last_state_payload is not None:
        try:
            await ws.send_json(last_state_payload)
        except Exception as e:
            console.print(f"[yellow][WS] Error de estado inicial: {e}[/yellow]")
            
    try:
        async for msg in ws:
            pass
    except Exception as e:
        pass
    finally:
        active_connections.discard(ws)
        console.print(f"[bold yellow][WS] Cliente desconectado. Activos: {len(active_connections)}[/bold yellow]")
        
    return ws

# ===============================================
# GEMINI: ANÁLISIS NARRATIVO
# ===============================================
client_gemini = genai.Client(api_key=GEMINI_API_KEY)

async def get_gemini_analysis(summary_text, sentiment):
    """Genera un resumen ejecutivo usando Google Gemini."""
    try:
        prompt = f"""
        Actúa como un experto en trading cuantitativo e Inteligencia de Negocios.
        Analiza el siguiente estado del mercado y genera un 'Resumen Ejecutivo' de 2 párrafos en español.
        Explica por qué el bot está tomando estas decisiones basándote en el sentimiento y los precios.
        
        Estado del Mercado:
        {summary_text}
        
        Sentimiento (Fear & Greed):
        {sentiment}
        """
        response = await asyncio.to_thread(
            client_gemini.models.generate_content, 
            model='gemini-2.5-flash', 
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"Análisis no disponible temporalmente. ({e})"

# ===============================================
# SIMULADOR DE CARTERA (PAPER TRADING)
# ===============================================
async def execute_paper_trade(symbol, signal_type, price, strategy=None):
    """Simula la ejecución de una orden en la billetera virtual."""
    wallet = await db.get_wallet()
    balance = wallet.balance
    assets = json.loads(wallet.asset_balances)
    
    asset_name = symbol.replace("USDT", "")
    current_asset_qty = assets.get(asset_name, 0.0)
    
    commission = 0.0001 # 0.01% por operación
    
    if signal_type == "BUY" and balance > 10:
        # Usamos el 95% del saldo para comprar para dejar margen
        amount_to_spend = balance * 0.95
        qty_to_buy = (amount_to_spend / price) * (1 - commission)
        assets[asset_name] = current_asset_qty + qty_to_buy
        balance -= amount_to_spend
        await db.record_paper_trade(symbol, "BUY", qty_to_buy, price, strategy=strategy)
        console.print(f"[bold lime][PAPER TRADE] COMPRA ejecutada: {qty_to_buy:.4f} {asset_name}[/bold lime]")
        
    elif signal_type == "SELL" and current_asset_qty > 0:
        val_to_receive = (current_asset_qty * price) * (1 - commission)
        balance += val_to_receive
        await db.record_paper_trade(symbol, "SELL", current_asset_qty, price, strategy=strategy)
        console.print(f"[bold red][PAPER TRADE] VENTA ejecutada: {current_asset_qty:.4f} {asset_name}[/bold red]")
        assets[asset_name] = 0.0
        
    await db.update_wallet(balance, json.dumps(assets))
# La simulación / backtest de estrategias ahora se realiza a través de metrics.py

async def run_trading_system():
    await db.init_db()
    await db.init_wallet() # Inicializa $1000 si es necesario
    client = await AsyncClient.create()
    bm = BinanceSocketManager(client)
    all_market_data = {}
    first_run = True
    executive_summary = "Generando análisis inicial..."
    ui_cycle_count = 0
    
    async def sync_historical_data(symbol):
        """Descarga klines recientes para rellenar huecos tras una desconexión."""
        try:
            klines = await client.get_historical_klines(symbol, "1s", "1 hour ago UTC")
            new_df = pd.DataFrame(klines, columns=['time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'qav', 'num_trades', 'taker_base', 'taker_quote', 'ignore'])
            new_df['time'] = pd.to_datetime(new_df['time'], unit='ms')
            new_df.set_index('time', inplace=True)
            new_df = new_df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
            all_market_data[symbol] = new_df.tail(500)
            console.print(f"[green][RESYNC] Datos de {symbol} sincronizados correctamente.[/green]")
        except Exception as e:
            console.print(f"[red]Error sincronizando datos de {symbol}: {e}[/red]")

    async def handle_socket(symbol):
        retry_delay = 5
        while True:
            try:
                # Sincronizamos historial al conectar/reconectar
                await sync_historical_data(symbol)
                async with bm.kline_socket(symbol=symbol, interval="1s") as s:
                    console.print(f"[bold green][WS] Conectado a {symbol}[/bold green]")
                    retry_delay = 5
                    while True:
                        res = await s.recv()
                        k = res['k']
                        timestamp = pd.to_datetime(k['t'], unit='ms')
                        df = all_market_data[symbol]
                        df.loc[timestamp] = pd.Series({'Open': float(k['o']), 'High': float(k['h']), 'Low': float(k['l']), 'Close': float(k['c']), 'Volume': float(k['v'])}, name=timestamp)
                        all_market_data[symbol] = df.tail(500)
                        await db.save_tick(symbol, float(k['c']))
                        
                        if k['x']:
                            df_clean = process_indicators(all_market_data[symbol].copy())
                            strats = {"Scalping": strategy_scalping_rsi, "Cross": strategy_ema_cross, "Volat": strategy_volatility}
                            # AutoML: Seleccionamos la mejor por PnL histórico
                            best_name, best_sig, max_pnl = "", None, -999
                            for name, func in strats.items():
                                sigs = func(df_clean)
                                pnl = backtest_strategy(df_clean, sigs)
                                if pnl > max_pnl: max_pnl, best_name, best_sig = pnl, name, sigs[-1]
                            
                            if best_sig:
                                # Guardar señal y ejecutar Paper Trade
                                await db.save_signal(symbol, f"AutoML-{best_name}", best_sig['sig'], float(k['c']), best_sig['exit'], metadata=json.dumps({"pnl_60": max_pnl}))
                                await execute_paper_trade(symbol, best_sig['sig'], float(k['c']), strategy=f"AutoML-{best_name}")

            except Exception as e:
                console.print(f"[bold red][ERR] Error en socket {symbol}: {e}. Reconectando en {retry_delay}s...[/bold red]")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    async def ui_update_loop():
        nonlocal first_run, executive_summary, ui_cycle_count
        global last_state_payload
        strategy_comparison = None
        while True:
            try:
                os.system('cls' if os.name == 'nt' else 'clear')
                sentiment = await get_market_sentiment()
                wallet = await db.get_wallet()
                
                # Migración / Retrofit de estrategias para operaciones pasadas
                await retrofit_missing_strategies(db)
                
                # Consultar todos los ticks y operaciones de la DB
                async with db.async_session() as session:
                    res_ticks = await session.execute(select(PriceTick))
                    ticks = res_ticks.scalars().all()
                    
                    res_trades = await session.execute(select(PaperTrade))
                    trades = res_trades.scalars().all()
                
                # Calcular operaciones cerradas, curvas de equidad y métricas
                closed_trades = calculate_closed_trades(trades)
                equity_curve, btc_bh, eth_bh = reconstruct_equity_curves(ticks, trades, initial_balance=1000.0)
                port_metrics = calculate_portfolio_metrics(equity_curve, closed_trades)
                
                # Caching: Calcular simulación comparativa de estrategias cada 15 ciclos (aprox 45s)
                if first_run or ui_cycle_count % 15 == 0 or not strategy_comparison:
                    strategy_comparison = run_historical_backtest(ticks)
                
                # Formatear operaciones cerradas para enviarlas como JSON al JS del dashboard
                closed_trades_serializable = []
                for t in closed_trades:
                    closed_trades_serializable.append({
                        'symbol': t['symbol'],
                        'buy_time': t['buy_time'].strftime('%Y-%m-%d %H:%M:%S'),
                        'sell_time': t['sell_time'].strftime('%Y-%m-%d %H:%M:%S'),
                        'buy_price': t['buy_price'],
                        'sell_price': t['sell_price'],
                        'amount': t['amount'],
                        'pnl_usd': t['pnl_usd'],
                        'return_pct': t['return_pct'],
                        'strategy': t['strategy']
                    })
                
                # Panel Superior: Título y Billetera en la consola Rich
                wallet_text = f"[bold white]Balance USD: [green]${wallet.balance:,.2f}[/green] | Assets: [cyan]{wallet.asset_balances}[/cyan][/bold white]"
                console.print(Panel(f"[bold green]TRADING SYSTEM v5.4 (AI NARRATIVE + SIMULATION)[/bold green]\n{wallet_text}", subtitle=f"Sentimiento: {sentiment}"))
                
                summary, dashboard_info = [], {}
                active_symbols = [s for s in SYMBOLS if s in all_market_data and not all_market_data[s].empty]
                
                for sym in active_symbols:
                    df_clean = process_indicators(all_market_data[sym].copy())
                    strats = {"Scalping": strategy_scalping_rsi, "Cross": strategy_ema_cross, "Volat": strategy_volatility}
                    best_name, best_signals, max_pnl = "", [], -999
                    
                    for name, func in strats.items():
                        sigs = func(df_clean)
                        pnl = backtest_strategy(df_clean, sigs)
                        if pnl > max_pnl: max_pnl, best_name, best_signals = pnl, name, sigs
                    
                    if best_signals:
                        last_sig = best_signals[-1]
                        summary.append({"Sym": sym, "Price": f"${df_clean['Close'].iloc[-1]:,.2f}", "Model": f"{best_name} ({max_pnl:+.2f}%)", "Signal": last_sig['sig']})
                        dashboard_info[sym] = {'df': df_clean.tail(60), 'signals': best_signals[-60:]}
                
                if summary:
                    table = Table(title=f"Sync (UI): {datetime.now().strftime('%H:%M:%S')}")
                    table.add_column("Asset"); table.add_column("Price"); table.add_column("Strategy (PnL 60v)"); table.add_column("Decision", style="bold yellow")
                    for s in summary: table.add_row(s["Sym"], s["Price"], s["Model"], s["Signal"])
                    console.print(table)
                    
                    # Gemini Analysis (cada 20 ciclos = 60s aprox)
                    if ui_cycle_count % 20 == 0:
                        summary_text = "\n".join([f"{s['Sym']}: {s['Price']} -> {s['Signal']}" for s in summary])
                        executive_summary = await get_gemini_analysis(summary_text, sentiment)
                    
                    console.print(Panel(executive_summary, title="[bold purple]Gemini Executive Summary[/bold purple]", border_style="purple"))
                    
                    # Convertir datos de mercado a serializable para Plotly JS
                    market_data_serializable = {}
                    for sym in active_symbols:
                        if sym in dashboard_info:
                            df = dashboard_info[sym]['df']
                            sigs = dashboard_info[sym]['signals']
                            market_data_serializable[sym] = {
                                'times': [ts.strftime('%Y-%m-%dT%H:%M:%S') for ts in df.index],
                                'open': df['Open'].tolist(),
                                'high': df['High'].tolist(),
                                'low': df['Low'].tolist(),
                                'close': df['Close'].tolist(),
                                'ema_slow': df['ema_slow'].tolist() if 'ema_slow' in df else [],
                                'bb_upper': df['bb_upper'].tolist() if 'bb_upper' in df else [],
                                'bb_lower': df['bb_lower'].tolist() if 'bb_lower' in df else [],
                                'rsi': df['rsi'].tolist() if 'rsi' in df else [],
                                'signals': [{'sig': s['sig'], 'exit': s['exit']} for s in sigs]
                            }
                    
                    # Calcular equidad y balance actual del portafolio
                    active_asset_val = 0.0
                    try:
                        assets = json.loads(wallet.asset_balances)
                        for k, v in assets.items():
                            for s in summary:
                                if s['Sym'].startswith(k):
                                    price = float(s['Price'].replace('$', '').replace(',', ''))
                                    active_asset_val += v * price
                                    break
                    except Exception:
                        pass
                        
                    current_portfolio_val = wallet.balance + active_asset_val
                    total_return_pct = ((current_portfolio_val - 1000.0) / 1000.0) * 100.0

                    # Sanitizar profit factor por si es infinito
                    sanitized_metrics = port_metrics.copy()
                    if sanitized_metrics.get('profit_factor') == float('inf'):
                        sanitized_metrics['profit_factor'] = 'Infinity'

                    # Construir payload JSON
                    payload = {
                        'sentiment': sentiment,
                        'sync_time': datetime.now().strftime('%H:%M:%S'),
                        'current_portfolio_val': current_portfolio_val,
                        'wallet_balance': wallet.balance,
                        'wallet_assets': json.loads(wallet.asset_balances),
                        'total_return_pct': total_return_pct,
                        'executive_summary': executive_summary,
                        'summary': summary,
                        'market_data': market_data_serializable,
                        'closed_trades': closed_trades_serializable,
                        'equity_curve': equity_curve,
                        'btc_bh': btc_bh,
                        'eth_bh': eth_bh,
                        'strategy_comparison': strategy_comparison,
                        'metrics': sanitized_metrics
                    }
                    
                    last_state_payload = payload
                    
                    # Transmitir a todos los clientes WebSocket activos
                    for ws in list(active_connections):
                        try:
                            await ws.send_json(payload)
                        except Exception as e:
                            console.print(f"[yellow][WS] Error al enviar a cliente: {e}[/yellow]")

                    generate_markdown_report(summary, sentiment)
                else:
                    console.print("[yellow]Esperando datos de mercado...[/yellow]")
                
                if first_run and summary:
                    webbrowser.open(f"http://localhost:{PORT}")
                    first_run = False
                
                ui_cycle_count += 1
            except Exception as e:
                console.print(f"[red]Error en UI Loop: {e}[/red]")
            await asyncio.sleep(3)

    # Precarga de datos históricos ANTES de iniciar la UI y Sockets
    console.print("[cyan]Pre-cargando datos históricos de Binance...[/cyan]")
    await asyncio.gather(*(sync_historical_data(sym) for sym in SYMBOLS))
    
    # Configurar e iniciar servidor web aiohttp
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/plotly.min.js', handle_plotly)
    app.router.add_get('/ws', handle_websocket)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', PORT)
    await site.start()
    console.print(f"[bold green]Servidor web iniciado en http://localhost:{PORT}[/bold green]")
    
    try:
        # Lanzar tareas
        tasks = [handle_socket(sym) for sym in SYMBOLS]
        tasks.append(ui_update_loop())
        await asyncio.gather(*tasks)
    finally:
        console.print("[cyan]Deteniendo servidor web y limpiando recursos...[/cyan]")
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(run_trading_system())
    except KeyboardInterrupt:
        pass