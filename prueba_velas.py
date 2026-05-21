import pandas as pd
import numpy as np

# 1. Datos de prueba (Simulando lo que descarga yfinance)
data = {
    'Open': [100, 102, 101, 105, 100, 110],
    'High': [105, 106, 102, 110, 101, 115],
    'Low': [95, 100, 90, 104, 95, 108],
    'Close': [102, 101, 95, 108, 99, 112]
}
df = pd.DataFrame(data)

# 2. Inicializar array de señales
signals = np.zeros(len(df))

# 3. Lógica de detección (Idéntica a la del bot principal)
print("Ejecutando detección de patrones...")
for i in range(2, len(df)):
    # Extraer valores individuales
    o, h, l, c = df['Open'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i], df['Close'].iloc[i]
    o_prev, c_prev = df['Open'].iloc[i-1], df['Close'].iloc[i-1]
    
    body = abs(c - o)
    
    # Detección de Martillo (Hammer)
    if body > 0 and (min(o, c) - l) > body * 2: 
        print(f"  [!] Martillo detectado en índice {i}")
        signals[i] = 1
        
    # Detección de Engolfing Alcista
    if c_prev < o_prev and c > o and c > o_prev: 
        print(f"  [!] Engolfing detectado en índice {i}")
        signals[i] = 1

# 4. Resultado final
print("\nArray de señales final:")
print(signals)
