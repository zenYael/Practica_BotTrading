import json
import asyncio
import pandas as pd
import numpy as np
import pandas_ta as ta
from datetime import datetime
from sqlalchemy import select, update
from database import PaperTrade, TradingSignal, PriceTick, Wallet

TRAILING_STOP = 0.002

def strategy_scalping_rsi(df):
    return [{"sig": "BUY", "exit": cl * (1 - TRAILING_STOP)} if r < 55 and cl > e else {"sig": "SELL", "exit": cl * (1 + TRAILING_STOP)} if r > 45 and cl < e else {"sig": "HOLD", "exit": 0} for r, cl, e in zip(df["rsi"].to_numpy(), df["Close"].to_numpy(), df["ema_fast"].to_numpy())]

def strategy_ema_cross(df):
    signals = [{"sig": "HOLD", "exit": 0}]
    fast = df["ema_fast"].to_numpy()
    slow = df["ema_slow"].to_numpy()
    close = df["Close"].to_numpy()
    for i in range(1, len(df)):
        if fast[i-1] <= slow[i-1] and fast[i] > slow[i]:
            signals.append({"sig": "BUY", "exit": close[i] * (1 - TRAILING_STOP)})
        elif fast[i-1] >= slow[i-1] and fast[i] < slow[i]:
            signals.append({"sig": "SELL", "exit": close[i] * (1 + TRAILING_STOP)})
        else:
            signals.append({"sig": "HOLD", "exit": 0})
    return signals

def strategy_volatility(df):
    return [{"sig": "BUY", "exit": cl * (1 - TRAILING_STOP)} if cl <= bl * 1.001 else {"sig": "SELL", "exit": cl * (1 + TRAILING_STOP)} if cl >= bu * 0.999 else {"sig": "HOLD", "exit": 0} for cl, bl, bu in zip(df["Close"].to_numpy(), df["bb_lower"].to_numpy(), df["bb_upper"].to_numpy())]

def backtest_strategy(df, signals):
    """Simula el PnL porcentual de una estrategia incluyendo comisiones y penalización por Drawdown."""
    if not signals or len(signals) < len(df): return -999
    
    pnl = 0.0
    commission = 0.0002 # 0.02% total por entrada/salida
    in_position = False
    entry_price = 0.0
    
    # Métricas de riesgo
    max_equity = 1.0
    current_equity = 1.0
    max_drawdown = 0.0
    
    df_eval = df.tail(60)
    signals_eval = signals[-60:]
    
    for i in range(len(df_eval)):
        price = df_eval['Close'].iloc[i]
        sig = signals_eval[i]['sig']
        
        if sig == "BUY" and not in_position:
            in_position = True
            entry_price = price
        elif (sig == "SELL" or (in_position and price < signals_eval[i]['exit'])) and in_position:
            # PnL neto tras comisiones
            trade_return = (price - entry_price) / entry_price
            pnl += (trade_return - commission)
            current_equity *= (1 + (trade_return - commission))
            in_position = False
        
        # Tracking de Drawdown (basado en equity acumulada)
        if current_equity > max_equity:
            max_equity = current_equity
        dd = (max_equity - current_equity) / max_equity
        if dd > max_drawdown:
            max_drawdown = dd
            
    # Penalización por riesgo: Si el drawdown supera el 5%, reducimos drásticamente el score
    score = pnl * 100
    if max_drawdown > 0.05:
        score -= 10 # Penalización fija fuerte por riesgo excesivo
        
    return score


async def retrofit_missing_strategies(db):
    """
    Asocia de forma retroactiva las operaciones en 'paper_trades' que no tengan
    una estrategia registrada, buscando la señal de trading más cercana.
    """
    async with db.async_session() as session:
        async with session.begin():
            # Obtener operaciones sin estrategia
            res = await session.execute(select(PaperTrade).where(PaperTrade.strategy == None))
            null_trades = res.scalars().all()
            if not null_trades:
                return
            
            # Obtener señales de trading
            res_sig = await session.execute(select(TradingSignal))
            signals = res_sig.scalars().all()
            
            if not signals:
                return
                
            for trade in null_trades:
                # Filtrar señales por símbolo y tipo
                matching_sigs = [
                    s for s in signals 
                    if s.symbol == trade.symbol 
                    and s.signal_type == trade.trade_type
                ]
                if matching_sigs:
                    # Encontrar la señal más cercana en tiempo
                    closest_sig = min(
                        matching_sigs, 
                        key=lambda s: abs((s.timestamp - trade.timestamp).total_seconds())
                    )
                    trade.strategy = closest_sig.strategy
                    session.add(trade)

def calculate_closed_trades(trades):
    """
    Algoritmo FIFO para emparejar BUY y SELL por activo y calcular
    las operaciones cerradas individuales, PnL y retornos.
    """
    closed_trades = []
    # Agrupar operaciones por activo
    trades_by_symbol = {}
    for t in trades:
        trades_by_symbol.setdefault(t.symbol, []).append(t)
        
    for symbol, sym_trades in trades_by_symbol.items():
        buys_queue = []
        # Ordenar por timestamp
        sorted_trades = sorted(sym_trades, key=lambda x: x.timestamp)
        
        for t in sorted_trades:
            if t.trade_type == "BUY":
                # Guardar timestamp, cantidad, precio, y estrategia
                buys_queue.append({
                    'timestamp': t.timestamp,
                    'amount': t.amount,
                    'price': t.price,
                    'strategy': t.strategy or "AutoML-Unknown"
                })
            elif t.trade_type == "SELL":
                rem_sell_qty = t.amount
                while rem_sell_qty > 0.00000001 and buys_queue:
                    oldest_buy = buys_queue[0]
                    buy_qty = oldest_buy['amount']
                    
                    if buy_qty <= rem_sell_qty:
                        qty_to_close = buy_qty
                        buys_queue.pop(0)
                    else:
                        qty_to_close = rem_sell_qty
                        oldest_buy['amount'] -= qty_to_close
                        
                    rem_sell_qty -= qty_to_close
                    
                    pnl_usd = qty_to_close * (t.price - oldest_buy['price'])
                    return_pct = ((t.price - oldest_buy['price']) / oldest_buy['price']) * 100.0
                    
                    closed_trades.append({
                        'symbol': symbol,
                        'buy_time': oldest_buy['timestamp'],
                        'sell_time': t.timestamp,
                        'buy_price': oldest_buy['price'],
                        'sell_price': t.price,
                        'amount': qty_to_close,
                        'pnl_usd': pnl_usd,
                        'return_pct': return_pct,
                        'strategy': oldest_buy['strategy']
                    })
                    
    # Ordenar por tiempo de salida (sell_time)
    closed_trades.sort(key=lambda x: x['sell_time'])
    return closed_trades

def reconstruct_equity_curves(ticks, trades, initial_balance=1000.0):
    """
    Reconstruye la curva de equidad histórica del capital simulado en base a operaciones reales
    y ticks de precio. También calcula las curvas de benchmark de Buy & Hold.
    """
    if not ticks:
        return [], [], []
        
    # Ordenar ticks y operaciones por fecha
    sorted_ticks = sorted(ticks, key=lambda x: x.timestamp)
    sorted_trades = sorted(trades, key=lambda x: x.timestamp)
    
    # Precios de inicio para Buy & Hold
    initial_prices = {}
    for t in sorted_ticks:
        if t.symbol not in initial_prices:
            initial_prices[t.symbol] = t.price
            
    btc_price_start = initial_prices.get('BTCUSDT', 75000.0) # default si no hay
    eth_price_start = initial_prices.get('ETHUSDT', 2000.0)
    
    btc_bh_qty = initial_balance / btc_price_start
    eth_bh_qty = initial_balance / eth_price_start
    
    equity_curve = []
    btc_bh_curve = []
    eth_bh_curve = []
    
    cash = initial_balance
    holdings = {'BTC': 0.0, 'ETH': 0.0}
    latest_prices = {'BTCUSDT': btc_price_start, 'ETHUSDT': eth_price_start}
    
    trade_idx = 0
    num_trades = len(sorted_trades)
    commission = 0.0001
    
    for tick in sorted_ticks:
        latest_prices[tick.symbol] = tick.price
        
        # Aplicar operaciones que ocurrieron antes o en este tick
        while trade_idx < num_trades and sorted_trades[trade_idx].timestamp <= tick.timestamp:
            t = sorted_trades[trade_idx]
            asset = t.symbol.replace("USDT", "")
            if t.trade_type == "BUY":
                # En la simulación se gastó balance*0.95, con comisiones descontadas de la cantidad comprada
                spent = (t.amount * t.price) / (1.0 - commission)
                cash -= spent
                holdings[asset] = holdings.get(asset, 0.0) + t.amount
            elif t.trade_type == "SELL":
                # Al vender se recibe cantidad*precio*(1-comision)
                received = (t.amount * t.price) * (1.0 - commission)
                cash += received
                holdings[asset] = max(0.0, holdings.get(asset, 0.0) - t.amount)
            trade_idx += 1
            
        # Calcular equidad en este punto
        portfolio_val = cash
        for asset, qty in holdings.items():
            sym = f"{asset}USDT"
            portfolio_val += qty * latest_prices.get(sym, 0.0)
            
        timestamp_str = tick.timestamp.strftime('%Y-%m-%dT%H:%M:%S')
        
        equity_curve.append({
            'x': timestamp_str,
            'y': portfolio_val
        })
        
        btc_bh_curve.append({
            'x': timestamp_str,
            'y': btc_bh_qty * latest_prices.get('BTCUSDT', btc_price_start)
        })
        
        eth_bh_curve.append({
            'x': timestamp_str,
            'y': eth_bh_qty * latest_prices.get('ETHUSDT', eth_price_start)
        })
        
    return equity_curve, btc_bh_curve, eth_bh_curve

def calculate_portfolio_metrics(equity_curve, closed_trades):
    """
    Calcula todas las métricas de rendimiento y riesgo a partir de las
    operaciones cerradas y de la curva de equidad.
    """
    metrics = {
        'total_trades': len(closed_trades),
        'winning_trades': 0,
        'losing_trades': 0,
        'win_rate': 0.0,
        'profit_factor': 0.0,
        'sharpe_ratio': 0.0,
        'max_drawdown': 0.0,
        'risk_per_trade_avg': 0.0,
        'risk_per_trade_std': 0.0,
        'pnl_by_asset': {},
        'pnl_by_strategy': {}
    }
    
    if not closed_trades:
        return metrics
        
    # Calcular ganadoras y perdedoras
    wins = [t['pnl_usd'] for t in closed_trades if t['pnl_usd'] > 0]
    losses = [abs(t['pnl_usd']) for t in closed_trades if t['pnl_usd'] <= 0]
    
    metrics['winning_trades'] = len(wins)
    metrics['losing_trades'] = len(losses)
    metrics['win_rate'] = (len(wins) / len(closed_trades)) * 100.0 if closed_trades else 0.0
    
    sum_wins = sum(wins)
    sum_losses = sum(losses)
    
    if sum_losses > 0:
        metrics['profit_factor'] = sum_wins / sum_losses
    else:
        metrics['profit_factor'] = float('inf') if sum_wins > 0 else 0.0
        
    # PnL por activo y estrategia
    for t in closed_trades:
        metrics['pnl_by_asset'][t['symbol']] = metrics['pnl_by_asset'].get(t['symbol'], 0.0) + t['pnl_usd']
        metrics['pnl_by_strategy'][t['strategy']] = metrics['pnl_by_strategy'].get(t['strategy'], 0.0) + t['pnl_usd']
        
    # Riesgo por operación
    returns = [t['return_pct'] for t in closed_trades]
    loss_pcts = [abs(t['return_pct']) for t in closed_trades if t['return_pct'] < 0]
    metrics['risk_per_trade_avg'] = np.mean(loss_pcts) if loss_pcts else 0.0
    metrics['risk_per_trade_std'] = np.std(returns) if len(returns) > 1 else 0.0
    
    # Sharpe Ratio y Max Drawdown desde la curva de equidad
    if equity_curve and len(equity_curve) > 2:
        df_eq = pd.DataFrame(equity_curve)
        df_eq['y'] = df_eq['y'].astype(float)
        
        # Max Drawdown
        df_eq['cummax'] = df_eq['y'].cummax()
        df_eq['dd'] = (df_eq['cummax'] - df_eq['y']) / df_eq['cummax']
        metrics['max_drawdown'] = float(df_eq['dd'].max() * 100.0)
        
        # Sharpe Ratio (Resampleamos a intervalos de 15 minutos para obtener retornos regulares)
        df_eq['timestamp'] = pd.to_datetime(df_eq['x'])
        df_eq.set_index('timestamp', inplace=True)
        df_res = df_eq['y'].resample('15Min').last().ffill()
        
        returns_res = df_res.pct_change().dropna()
        if len(returns_res) > 1 and returns_res.std() > 0:
            # 35,040 periodos de 15 min en un año
            mean_ret = returns_res.mean()
            std_ret = returns_res.std()
            metrics['sharpe_ratio'] = float((mean_ret / std_ret) * np.sqrt(35040))
            
    return metrics

def process_historical_candles(ticks):
    """
    Agrupa los ticks históricos de la DB en velas de 5s para el backtest de estrategias.
    """
    if not ticks:
        return {}
        
    df_ticks = pd.DataFrame([{
        'timestamp': t.timestamp,
        'symbol': t.symbol,
        'price': t.price
    } for t in ticks])
    
    symbols_data = {}
    for sym in df_ticks['symbol'].unique():
        df_sym = df_ticks[df_ticks['symbol'] == sym].copy()
        df_sym.set_index('timestamp', inplace=True)
        # Resample a 5 segundos
        df_res = df_sym['price'].resample('5s').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last'
        }).ffill()
        df_res['Volume'] = 0.0 # Placeholder
        
        # Calcular indicadores
        df_res["rsi"] = ta.rsi(df_res["Close"], length=5)
        df_res["ema_fast"] = ta.ema(df_res["Close"], length=5)
        df_res["ema_slow"] = ta.ema(df_res["Close"], length=15)
        
        sma20 = df_res["Close"].rolling(window=10).mean()
        std20 = df_res["Close"].rolling(window=10).std()
        df_res["bb_upper"] = sma20 + (std20 * 1.2)
        df_res["bb_lower"] = sma20 - (std20 * 1.2)
        
        symbols_data[sym] = df_res.dropna()
        
    return symbols_data

def simulate_strategy_returns(df, strategy_func, trailing_stop=0.002):
    """
    Simula una estrategia sobre un DataFrame OHLC completo y devuelve la curva de equidad
    de la estrategia (como porcentaje de retorno acumulado).
    """
    signals = strategy_func(df)
    
    equity = 1.0
    equity_curve = []
    in_pos = False
    entry_price = 0.0
    commission = 0.0002
    
    close_arr = df['Close'].to_numpy()
    
    for i in range(len(df)):
        price = close_arr[i]
        sig = signals[i]['sig']
        exit_price = signals[i]['exit']
        
        if sig == "BUY" and not in_pos:
            in_pos = True
            entry_price = price
        elif (sig == "SELL" or (in_pos and price < exit_price)) and in_pos:
            trade_ret = (price - entry_price) / entry_price
            equity *= (1.0 + (trade_ret - commission))
            in_pos = False
            
        equity_curve.append(equity)
        
    return equity_curve

def run_historical_backtest(ticks):
    """
    Simula las 3 estrategias y la lógica AutoML sobre todo el historial de ticks.
    Devuelve los arrays de retornos acumulados (%) para graficarlos en el dashboard.
    """
    symbols_data = process_historical_candles(ticks)
    if not symbols_data:
        return []
        
    # Usaremos BTCUSDT como activo de referencia principal para la comparación de estrategias
    # ya que tiene la mayor cantidad de histórico.
    sym = 'BTCUSDT'
    if sym not in symbols_data:
        sym = list(symbols_data.keys())[0]
        
    df = symbols_data[sym].tail(1000)
    
    # Usar las estrategias locales ya definidas en este archivo
    
    # 1. Simulación de estrategias estáticas
    scalping_curve = simulate_strategy_returns(df, strategy_scalping_rsi)
    cross_curve = simulate_strategy_returns(df, strategy_ema_cross)
    volat_curve = simulate_strategy_returns(df, strategy_volatility)
    
    # 2. Simulación de AutoML (selección dinámica de estrategia)
    automl_curve = []
    automl_equity = 1.0
    in_pos = False
    entry_price = 0.0
    commission = 0.0002
    
    strats = {
        "Scalping": strategy_scalping_rsi, 
        "Cross": strategy_ema_cross, 
        "Volat": strategy_volatility
    }
    
    close_arr = df['Close'].to_numpy()
    
    # Guardamos los arrays de señales precalculadas para todo el dataset
    precalc_sigs = {name: func(df) for name, func in strats.items()}
    
    # Simulamos paso a paso con AutoML
    for i in range(len(df)):
        price = close_arr[i]
        
        # Cada paso evaluamos el rendimiento en las últimas 60 velas para seleccionar estrategia
        if i >= 60:
            df_eval = df.iloc[i-59:i+1]
            best_name = "Scalping"
            max_pnl = -999.0
            
            for name, func in strats.items():
                sigs_eval = precalc_sigs[name][i-59:i+1]
                score = backtest_strategy(df_eval, sigs_eval)
                if score > max_pnl:
                    max_pnl = score
                    best_name = name
                    
            chosen_sig = precalc_sigs[best_name][i]
        else:
            chosen_sig = {'sig': 'HOLD', 'exit': 0.0}
            
        sig = chosen_sig['sig']
        exit_price = chosen_sig['exit']
        
        if sig == "BUY" and not in_pos:
            in_pos = True
            entry_price = price
        elif (sig == "SELL" or (in_pos and price < exit_price)) and in_pos:
            trade_ret = (price - entry_price) / entry_price
            automl_equity *= (1.0 + (trade_ret - commission))
            in_pos = False
            
        automl_curve.append(automl_equity)
        
    timestamps = [ts.strftime('%Y-%m-%dT%H:%M:%S') for ts in df.index]
    
    # Formatear para Plotly (retorno acumulado como porcentaje, ej. 1.05 -> +5.0%)
    strategy_comparison = {
        'timestamps': timestamps,
        'Scalping': [(x - 1.0) * 100.0 for x in scalping_curve],
        'Cross': [(x - 1.0) * 100.0 for x in cross_curve],
        'Volatility': [(x - 1.0) * 100.0 for x in volat_curve],
        'AutoML': [(x - 1.0) * 100.0 for x in automl_curve]
    }
    
    return strategy_comparison
