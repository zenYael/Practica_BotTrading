from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

Base = declarative_base()

class PriceTick(Base):
    __tablename__ = 'price_ticks'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    symbol = Column(String(20), index=True)
    price = Column(Float)

class TradingSignal(Base):
    __tablename__ = 'trading_signals'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    symbol = Column(String(20), index=True)
    strategy = Column(String(50))
    signal_type = Column(String(20))  # BUY, SELL, HOLD
    price = Column(Float)
    exit_price = Column(Float)
    metadata_json = Column(Text)  # Store additional info like indicators

class Wallet(Base):
    __tablename__ = 'wallet'
    id = Column(Integer, primary_key=True)
    balance = Column(Float, default=1000.0)
    asset_balances = Column(Text, default="{}") # JSON string like {"BTC": 0.0, "ETH": 0.0}

class PaperTrade(Base):
    __tablename__ = 'paper_trades'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    symbol = Column(String(20))
    trade_type = Column(String(10)) # BUY, SELL
    amount = Column(Float)
    price = Column(Float)
    total_value = Column(Float)
    strategy = Column(String(50), nullable=True)

class DatabaseManager:
    def __init__(self, db_url="sqlite+aiosqlite:///trading_system.db"):
        self.engine = create_async_engine(db_url, echo=False)
        self.async_session = sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def init_db(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Migración automática para la columna strategy en SQLite
        try:
            from sqlalchemy import text
            async with self.engine.begin() as conn:
                await conn.execute(text("ALTER TABLE paper_trades ADD COLUMN strategy VARCHAR(50);"))
        except Exception:
            pass

    async def save_tick(self, symbol, price):
        async with self.async_session() as session:
            async with session.begin():
                tick = PriceTick(symbol=symbol, price=price)
                session.add(tick)

    async def save_signal(self, symbol, strategy, signal_type, price, exit_price, metadata=""):
        async with self.async_session() as session:
            async with session.begin():
                signal = TradingSignal(
                    symbol=symbol, 
                    strategy=strategy, 
                    signal_type=signal_type, 
                    price=price, 
                    exit_price=exit_price,
                    metadata_json=metadata
                )
                session.add(signal)
    
    async def init_wallet(self):
        """Inicializa la billetera con $1000 si no existe."""
        from sqlalchemy import select
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(select(Wallet))
                wallet = result.scalar_one_or_none()
                if not wallet:
                    session.add(Wallet(balance=1000.0, asset_balances="{}"))

    async def get_wallet(self):
        from sqlalchemy import select
        async with self.async_session() as session:
            result = await session.execute(select(Wallet))
            return result.scalar_one()

    async def update_wallet(self, balance, asset_balances):
        from sqlalchemy import select
        async with self.async_session() as session:
            async with session.begin():
                result = await session.execute(select(Wallet))
                wallet = result.scalar_one()
                wallet.balance = balance
                wallet.asset_balances = asset_balances

    async def record_paper_trade(self, symbol, trade_type, amount, price, strategy=None):
        async with self.async_session() as session:
            async with session.begin():
                trade = PaperTrade(
                    symbol=symbol, 
                    trade_type=trade_type, 
                    amount=amount, 
                    price=price, 
                    total_value=amount*price,
                    strategy=strategy
                )
                session.add(trade)

