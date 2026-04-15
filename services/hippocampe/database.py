import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, Integer, String, Text, DateTime, text
from sqlalchemy.orm import declarative_base
from sqlalchemy.future import select
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# We setup the postgresql engine
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql+asyncpg://aletheia:aletheia_password@localhost:5432/hippocampe")

Base = declarative_base()

class Message(Base):
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(50), nullable=False) # 'user', 'assistant', 'system' (though system is dynamic so mostly user/assistant)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

# Pool massif géré par l'Event Loop
engine = create_async_engine(POSTGRES_URL, pool_size=20, max_overflow=10, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Création rétroactive absolue de l'index B-Tree pour les données préexistantes 
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_timestamp ON messages (timestamp DESC);"))

async def add_message(role: str, content: str):
    async with AsyncSessionLocal() as db:
        try:
            new_msg = Message(role=role, content=content)
            db.add(new_msg)
            await db.commit() # Relaxe le GIL pendant l'attente I/O réseau
        except Exception as e:
            print(f"Error adding message to db: {e}")
            await db.rollback()

async def get_recent_history(n: int = 10):
    async with AsyncSessionLocal() as db:
        try:
            stmt = select(Message.role, Message.content).order_by(Message.timestamp.desc()).limit(n)
            result = await db.execute(stmt)
            messages_list = [{"role": row.role, "content": row.content} for row in result.all()]
            messages_list.reverse() # In-place reversal, 0 overhead GC
            return messages_list
        except Exception as e:
            print(f"Error getting history: {e}")
            return []
