import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./maison_or.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Dans models.py - AJOUTE cette fonction UNE FOI
def fix_apartments_table():
    """FIX UNE FOI la table apartments"""
    from sqlalchemy import text
    with SessionLocal() as db:
        db.execute(text("ALTER TABLE apartments ADD COLUMN address VARCHAR(255)"))
        db.commit()
        print("✅ address ajouté !")

# Lance UNE FOI :
# fix_apartments_table()