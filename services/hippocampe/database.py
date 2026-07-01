import os
import asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.orm import declarative_base
from sqlalchemy.future import select
from datetime import datetime
import json
from dotenv import load_dotenv
from alembic.config import Config
from alembic import command

load_dotenv()

# We setup the postgresql engine
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql+asyncpg://aletheia:aletheia_password@localhost:5432/hippocampe")

Base = declarative_base()

class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(50), nullable=False) # 'user', 'assistant', 'system' (though system is dynamic so mostly user/assistant)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Seule définition du schéma (table + index) : source de vérité partagée par
    # l'application et par les migrations Alembic (migrations/versions/).
    __table_args__ = (
        Index('ix_messages_timestamp', timestamp.desc()),
    )

# Pool massif géré par l'Event Loop
engine = create_async_engine(POSTGRES_URL, pool_size=20, max_overflow=10, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

_ALEMBIC_INI = Path(__file__).parent / "alembic.ini"

def _upgrade_to_head():
    command.upgrade(Config(str(_ALEMBIC_INI)), "head")

async def init_db():
    # Alembic pilote son propre asyncio.run() (voir migrations/env.py) : on l'exécute
    # dans un thread séparé pour ne pas entrer en conflit avec la boucle déjà active ici.
    await asyncio.to_thread(_upgrade_to_head)

async def add_message(role: str, content):
    if not isinstance(content, str):
        content_str = json.dumps(content)
    else:
        content_str = content

    async with AsyncSessionLocal() as db:
        try:
            new_msg = Message(role=role, content=content_str)
            db.add(new_msg)
            await db.commit() # Relaxe le GIL pendant l'attente I/O réseau
        except Exception as e:
            print(f"Error adding message to db: {e}")
            await db.rollback()

async def get_recent_history(n: int = 10):
    async with AsyncSessionLocal() as db:
        try:
            stmt = select(Message.role, Message.content, Message.timestamp).order_by(Message.timestamp.desc()).limit(n)
            result = await db.execute(stmt)
            
            def parse_content(role, c, timestamp):
                # Seul l'utilisateur a l'horodatage pour éviter que l'IA n'apprenne à l'écrire
                prefix = f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] " if role == "user" else ""
                try:
                    if isinstance(c, str) and (c.startswith('[') or c.startswith('{')):
                        j = json.loads(c)
                        if isinstance(j, dict) and "text" in j:
                            j["text"] = prefix + j["text"] if prefix else j["text"]
                            return j
                        elif isinstance(j, list):
                            if prefix and len(j) > 0 and isinstance(j[0], dict) and j[0].get("type") == "text":
                                j[0]["text"] = prefix + j[0]["text"]
                            return j
                        else:
                            return j

                except json.JSONDecodeError:
                    pass

                return prefix + c if prefix else c

            messages_list = [{"role": row.role, "content": parse_content(row.role, row.content, row.timestamp)} for row in result.all()]
            messages_list.reverse() # In-place reversal, 0 overhead GC
            return messages_list
        except Exception as e:
            print(f"Error getting history: {e}")
            return []
