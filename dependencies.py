from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from database import get_db
from models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    # Ici tu devrais décoder ton token et récupérer l'utilisateur depuis la DB
    user = db.query(User).filter(User.id == 1).first()  # juste pour exemple
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur non authentifié")
    return user