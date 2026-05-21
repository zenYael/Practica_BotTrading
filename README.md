# 🚀 Guía de Configuración: Alpha-Quant Trading System

Esta guía te ayudará a instalar y ejecutar el sistema de trading en una computadora nueva desde cero.

---

## 1. Requisitos Previos

Antes de comenzar, asegúrate de tener instalado:
*   **Python 3.9 o superior:** Descárgalo de [python.org](https://www.python.org/downloads/). 
    > **IMPORTANTE:** Durante la instalación en Windows, asegúrate de marcar la casilla **"Add Python to PATH"**.

---

## 2. Preparación de Archivos

Copia los siguientes archivos de tu proyecto original a una nueva carpeta en la otra PC:
*   `bot.py` (Script principal)
*   `database.py` (Gestor de base de datos)
*   `.env` (Archivo de configuración con tu API Key)
*   `plotly.min.js` (Para que el dashboard funcione sin internet)

---

## 3. Configuración del Entorno Virtual (Recomendado)

Es mejor usar un entorno virtual para no ensuciar tu instalación global de Python.

1. Abre una terminal (PowerShell o CMD) en la carpeta del proyecto.
2. Crea el entorno virtual:
   ```powershell
   python -m venv venv
   ```
3. Actívalo:
   *   **En Windows:**
       ```powershell
       .\venv\Scripts\activate
       ```
   *   **En Linux/Mac:**
       ```bash
       source venv/bin/activate
       ```

---

## 4. Instalación de Dependencias

Una vez activado el entorno, instala todas las librerías necesarias con el siguiente comando:

```powershell
pip install pandas numpy requests plotly rich google-genai python-dotenv python-binance pandas-ta sqlalchemy aiosqlite
```

---

## 5. Configuración de la API Key

Asegúrate de que el archivo `.env` contenga tu clave de Google Gemini:

```env
GEMINI_API_KEY=TU_API_KEY_AQUI
```

> Si no tienes una, consíguela en [Google AI Studio](https://aistudio.google.com/).

---

## 6. Ejecución del Programa

Para iniciar el sistema, simplemente ejecuta:

```powershell
python Este_es_el_bueno_pecausa.py
```

### ¿Qué sucederá al iniciar?
1. El programa se conectará a Binance para obtener datos en tiempo real.
2. Se creará automáticamente la base de datos `trading_system.db`.
3. Se abrirá tu navegador web con el **Dashboard Interactivo** (`dashboard.html`).
4. Verás en la terminal el análisis de **Gemini** y el estado de tu billetera virtual.

---

## 7. Solución de Problemas Comunes

| Problema | Solución |
| :--- | :--- |
| **"python" no se reconoce** | Reinstala Python y marca "Add Python to PATH". |
| **Error de ModuleNotFoundError** | Asegúrate de haber activado el entorno (`venv`) y corrido el `pip install`. |
| **Error de API Key** | Verifica que el archivo `.env` esté en la misma carpeta que el script. |
| **No abre el Dashboard** | Revisa que tengas el archivo `plotly.min.js` en la carpeta. |

---

> [!TIP]
> **Mantén la terminal abierta:** El bot funciona en tiempo real; si cierras la terminal, el bot dejará de analizar y ejecutar operaciones.
