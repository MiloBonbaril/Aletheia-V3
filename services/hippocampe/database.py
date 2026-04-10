import os
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# We setup the postgresql engine
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://aletheia:aletheia_password@localhost:5432/hippocampe")

Base = declarative_base()

class Message(Base):
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(50), nullable=False) # 'user', 'assistant', 'system' (though system is dynamic so mostly user/assistant)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

engine = create_engine(POSTGRES_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def add_message(role: str, content: str):
    db = SessionLocal()
    try:
        new_msg = Message(role=role, content=content)
        db.add(new_msg)
        db.commit()
    except Exception as e:
        print(f"Error adding message to db: {e}")
        db.rollback()
    finally:
        db.close()

def get_recent_history(n: int = 10):
    db = SessionLocal()
    try:
        messages = db.query(Message).order_by(Message.timestamp.desc()).limit(n).all()
        # They come out newest first, so we reverse it to chronological
        return [{"role": msg.role, "content": msg.content} for msg in reversed(messages)]
    except Exception as e:
        print(f"Error getting history: {e}")
        return []
    finally:
        db.close()
