import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from auth import create_token, decode_token, generate_otp, hash_password, verify_password
from database import SessionLocal, engine
from email_service import send_otp_email
from cloudinary_service import upload_photo, delete_photo
from kkiapay_service import is_transaction_successful
from dependencies import get_current_user

app = FastAPI(title="Maison d'Or API", version="2.0.0")

class ApartmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    city: str
    price: float
    address: Optional[str] = None  
    cover_url: Optional[str] = None

class EventCreateBaseModel(BaseModel):
    title: str
    date: datetime
    woman_id: int     
    apartment_id: int  

class EventAction(BaseModel):
    action: str

models.Base.metadata.create_all(bind=engine)

# CORS
# main.py → ALLOWED_ORIGINS
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000", 
    "https://maison-or-api.onrender.com",  # 🔥 Render
    "*"  # DEV
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



OTP_MAX_ATTEMPTS = 5


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


from sqlalchemy.orm import joinedload  # 👈 AJOUT EN HAUT DU FICHIER

def _event_public_dict(event: models.Event) -> dict:
    apartment = event.apartment

    return {
        "id": event.id,
        "title": event.title,
        "date": event.date.isoformat(),

        # ✅ IMAGE (depuis apartment photos)
        "cover_url": (
            apartment.photos[0].url
            if apartment and hasattr(apartment, "photos") and apartment.photos
            else None
        ),

        # ✅ PRIX
        "price": apartment.price if apartment else 0,

        # ✅ VILLE
        "city": apartment.city if apartment else None,

        # ✅ AUTRES INFOS UTILES POUR TON FRONT
        "duration_hours": 2,  # temporaire si tu n'as pas encore ce champ

        "status": event.status,
        "woman_status": event.woman_status,
        "owner_status": event.owner_status,
                "status": event.status,  # 👈 IMPORTANT !
        "status_display": "confirmé" if event.status == "confirmed" else "en attente",

        "owner": {
            "id": event.woman.id,
            "first_name": event.woman.first_name,
        } if event.woman else None,
    }


def _event_full_dict(event: models.Event):
    return _event_public_dict(event)


def create_notification(user_id: int, notif_type: str, message: str, db: Session, event_id: Optional[int] = None):
    notif = models.Notification(  # models.Notification
        user_id=user_id,
        type=notif_type,
        message=message,
        event_id=event_id,
        is_read=False
    )
    db.add(notif)
    db.commit()


def get_current_user(authorization: str = Header(...), db: Session = Depends(get_db)) -> models.User:
    try:
        token = authorization.replace("Bearer ", "").strip()
        payload = decode_token(token)
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Token invalide")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable")
    return user


def require_role(allowed: list):
    def check(user: models.User = Depends(get_current_user)):
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail=f"Réservé aux rôles : {', '.join(allowed)}")
        return user
    return check


def _create_otp(db: Session, email: str, purpose: str) -> str:
    db.query(models.OTPCode).filter(
        models.OTPCode.email == email, models.OTPCode.purpose == purpose
    ).delete()
    db.commit()
    otp = generate_otp()
    db.add(models.OTPCode(
        email=email, code=otp, purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    ))
    db.commit()
    return otp


def _verify_otp_entry(db: Session, email: str, code: str, purpose: str) -> models.OTPCode:
    entry = db.query(models.OTPCode).filter(
        models.OTPCode.email == email,
        models.OTPCode.purpose == purpose,
        models.OTPCode.used.is_(False),
    ).first()
    if not entry:
        raise HTTPException(status_code=400, detail="Code OTP invalide")
    if entry.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Trop de tentatives, demandez un nouveau code")
    exp = entry.expires_at if entry.expires_at.tzinfo else entry.expires_at.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Code OTP expiré")
    if entry.code != code:
        entry.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail=f"Code incorrect. {OTP_MAX_ATTEMPTS - entry.attempts} tentative(s) restante(s)")
    return entry


def _upsert_user(db: Session, payload: dict, role: str) -> models.User:
    existing = db.query(models.User).filter(models.User.email == payload["email"]).first()
    if existing and existing.is_verified:
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")
    try:
        if existing and not existing.is_verified:
            for k, v in payload.items():
                setattr(existing, k, v)
            existing.role = role
            db.commit()
            return existing
        user = models.User(role=role, **payload)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ce pseudo est déjà utilisé")


class RegisterBase(BaseModel):
    first_name: str
    username: str
    phone: str = Field(..., pattern=r"^\+?[\d\s\-]{7,20}$")
    city: str
    email: EmailStr
    password: str = Field(..., min_length=8)

class RegisterPro(RegisterBase):
    business_name: str
    business_type: str
    siret: Optional[str] = None

class LoginSchema(BaseModel):
    email: EmailStr
    password: str

class OTPVerifySchema(BaseModel):
    email: EmailStr
    code: str
    purpose: str

class ResendOTPSchema(BaseModel):
    email: EmailStr
    purpose: str

class ForgotPasswordSchema(BaseModel):
    email: EmailStr

class ResetPasswordSchema(BaseModel):
    email: EmailStr
    code: str
    new_password: str = Field(..., min_length=8)

class EventCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=120)
    date: datetime
    womanid: int  
    apartmentid: int  
    event_type: str
    price: float = Field(..., gt=0)
    max_guests: int = Field(..., ge=1, le=20)
    city: str
    location_exact: str
    message: Optional[str] = None  

class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    location_hint: Optional[str] = None
    location_exact: Optional[str] = None
    date: Optional[datetime] = None
    is_active: Optional[bool] = None
    cover_url: Optional[str] = None

class VerifyPaymentEvent(BaseModel):
    event_id: int
    transaction_id: str

class VerifyPaymentPhoto(BaseModel):
    photo_id: int
    transaction_id: str


@app.get("/")
def health():
    return {"status": "ok", "app": "Maison d'Or API v2"}


@app.post("/register/homme", status_code=201)
def register_homme(data: RegisterBase, db: Session = Depends(get_db)):
    payload = {k: v for k, v in data.model_dump().items() if k != "password"}
    payload["password"] = hash_password(data.password)
    _upsert_user(db, payload, "homme")
    otp = _create_otp(db, data.email, "register")
    send_otp_email(data.email, otp, "register", "homme")
    return {"message": "Code OTP envoyé."}


@app.post("/register/femme", status_code=201)
def register_femme(data: RegisterBase, db: Session = Depends(get_db)):
    payload = {k: v for k, v in data.model_dump().items() if k != "password"}
    payload["password"] = hash_password(data.password)
    _upsert_user(db, payload, "femme")
    otp = _create_otp(db, data.email, "register")
    send_otp_email(data.email, otp, "register", "femme")
    return {"message": "Code OTP envoyé."}


@app.post("/register/professionnel", status_code=201)
def register_pro(data: RegisterPro, db: Session = Depends(get_db)):
    payload = {k: v for k, v in data.model_dump().items() if k != "password"}
    payload["password"] = hash_password(data.password)
    _upsert_user(db, payload, "professionnel")
    otp = _create_otp(db, data.email, "register")
    send_otp_email(data.email, otp, "register", "professionnel")
    return {"message": "Code OTP envoyé."}


@app.post("/verify-otp")
def verify_otp(data: OTPVerifySchema, db: Session = Depends(get_db)):
    entry = _verify_otp_entry(db, data.email, data.code, data.purpose)
    entry.used = True
    db.commit()
    if data.purpose == "register":
        user = db.query(models.User).filter(models.User.email == data.email).first()
        if not user:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable")
        user.is_verified = True
        db.commit()
        token = create_token({"sub": user.email, "role": user.role})
        return {"message": "Compte vérifié", "access_token": token, "token_type": "bearer", "role": user.role}
    return {"message": "Code vérifié"}


@app.post("/resend-otp")
def resend_otp(data: ResendOTPSchema, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if user:
        otp = _create_otp(db, data.email, data.purpose)
        send_otp_email(data.email, otp, data.purpose, user.role)
    return {"message": "Si cet email existe, un nouveau code a été envoyé."}


@app.post("/login")
def login(data: LoginSchema, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user or not verify_password(data.password, user.password):
        raise HTTPException(status_code=401, detail="Email ou mot de passe invalide")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Compte non vérifié.")
    token = create_token({"sub": user.email, "role": user.role})
    return {"access_token": token, "token_type": "bearer", "role": user.role}


@app.post("/forgot-password")
def forgot_password(data: ForgotPasswordSchema, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if user:
        otp = _create_otp(db, data.email, "reset")
        send_otp_email(data.email, otp, "reset", user.role)
    return {"message": "Si cet email est associé à un compte, vous recevrez un code."}


@app.post("/reset-password")
def reset_password(data: ResetPasswordSchema, db: Session = Depends(get_db)):
    entry = _verify_otp_entry(db, data.email, data.code, "reset")
    entry.used = True
    user = db.query(models.User).filter(models.User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    user.password = hash_password(data.new_password)
    db.commit()
    return {"message": "Mot de passe réinitialisé avec succès"}



@app.get("/users/femmes")
def get_femmes(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if current_user.role != "homme":
        raise HTTPException(status_code=403, detail="Réservé aux hommes")
    
    femmes = db.query(models.User).filter(
        models.User.role == "femme",
        models.User.is_verified == True
    ).order_by(models.User.created_at.desc()).all()
    
    return [
        {
            "id": f.id,
            "first_name": f.first_name,
            "username": f.username,
            "city": f.city,
            "profile_image": f.profile_image,
        }
        for f in femmes
    ]


def require_role(allowed_roles: list):
    def check_user(current_user: models.User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail=f" Réservé aux rôles {', '.join(allowed_roles)}")
        return current_user
    return check_user

@app.post("/events", status_code=201)
def create_event(
    data: EventCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(['homme']))
):
    # ✅ Vérifications
    woman = db.query(models.User).filter(models.User.id == data.womanid).first()
    apartment = db.query(models.Apartment).filter(models.Apartment.id == data.apartmentid).first()

    if not woman:
        raise HTTPException(status_code=404, detail="Femme introuvable")
    if not apartment:
        raise HTTPException(status_code=404, detail="Appartement introuvable")

    # ✅ Création événement
    event = models.Event(
        requester_id=current_user.id,
        woman_id=data.womanid,
        apartment_id=data.apartmentid,
        title=data.title,
        date=data.date,
        status="pending",  # ✅ Par défaut
        woman_status="pending",
        owner_status="pending"
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    # ✅ Notifications
    db.add_all([
        models.Notification(
            user_id=woman.id,
            event_id=event.id,
            message=f"Nouvelle demande: {data.title}"
        ),
        models.Notification(
            user_id=apartment.owner_id,
            event_id=event.id,
            message=f"Demande réservation: {data.title}"
        )
    ])
    db.commit()

    return {"message": "Événement créé", "event_id": event.id}




@app.get("/events")
def list_events(city: str = None, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    role = current_user.role
    user_id = current_user.id
    print(f"🔍 [{role}] User {user_id}")

    if role == "homme":
        # Ses demandes
        events = db.query(models.Event).filter(
            models.Event.requester_id == user_id
        ).all()
        print(f"👨 Homme: {len(events)}")

    elif role == "femme":
        # Ses hébergements (woman_id)
        events = db.query(models.Event).filter(
            models.Event.woman_id == user_id  # 👈 CORRECT !
        ).all()
        print(f"👩 Femme: {len(events)}")

    elif role == "professionnel":
        # Ses apparts (JOIN)
        events = db.query(models.Event).join(
            models.Apartment, models.Event.apartment_id == models.Apartment.id
        ).filter(
            models.Apartment.owner_id == user_id
        ).all()
        print(f"🏢 Pro: {len(events)}")

    else:
        events = []
        print("❌ Rôle inconnu")

    # City filter
    if city and city != 'Toutes':
        events = [e for e in events if e.city == city]

    # Status FR
    for e in events:
        e.status_display = "confirmé" if getattr(e, 'status', None) == "confirmed" else "en attente"

    print(f"📊 Retour: {len(events)} events")
    return [_event_public_dict(e) for e in events]

    

@app.get("/events/mine")
def my_events(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)  # ✅ TOUS les rôles !
):
    if current_user.role == "femme":
        events = db.query(models.Event).filter(
            models.Event.woman_id == current_user.id
        ).order_by(models.Event.date.desc()).all()
    elif current_user.role == "professionnel":
        events = db.query(models.Event).join(models.Apartment).filter(
            models.Apartment.owner_id == current_user.id
        ).order_by(models.Event.date.desc()).all()
    elif current_user.role == "homme":  # ✅ AJOUTÉ !
        events = db.query(models.Event).filter(
            models.Event.requester_id == current_user.id  # Ses demandes
        ).order_by(models.Event.date.desc()).all()
    else:
        events = []
    
    return [_event_full_dict(e) for e in events]



@app.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(models.Event).options(
        joinedload(models.Event.requester),
        joinedload(models.Event.woman),
        joinedload(models.Event.apartment).joinedload(models.Apartment.owner),
        joinedload(models.Event.apartment).joinedload(models.Apartment.photos)
    ).filter(models.Event.id == event_id).first()
    
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")
    
    # 🔥 DEBUG PYTHON CORRECT
    requester_name = event.requester.first_name if event.requester else None
    woman_name = event.woman.first_name if event.woman else None
    owner_name = event.apartment.owner.first_name if event.apartment and event.apartment.owner else None
    
    print(f"🔍 EVENT {event_id}: requester={requester_name}, woman={woman_name}, owner={owner_name}")
    
    return {
        "id": event.id,
        "title": event.title,
        "date": event.date.isoformat(),
        "price": event.apartment.price if event.apartment else 0,
        
        # 🔥 NOMS RÉELS → PYTHON SAFE
        "requester": {
            "id": event.requester.id if event.requester else None,
            "first_name": event.requester.first_name if event.requester else "Inconnu",
            "username": event.requester.username if event.requester else None
        } if event.requester else None,
        
        "woman": {
            "id": event.woman.id if event.woman else None,
            "first_name": event.woman.first_name if event.woman else "Inconnu",
            "username": event.woman.username if event.woman else None
        } if event.woman else None,
        
        "apartment": {
            "id": event.apartment.id if event.apartment else None,
            "title": event.apartment.title if event.apartment else None,
            "city": event.apartment.city if event.apartment else None,
            "price": event.apartment.price if event.apartment else 0,
            "owner": {
                "id": event.apartment.owner.id if event.apartment and event.apartment.owner else None,
                "first_name": event.apartment.owner.first_name if event.apartment and event.apartment.owner else "Inconnu",
                "username": event.apartment.owner.username if event.apartment and event.apartment.owner else None
            } if event.apartment and event.apartment.owner else None,
            "cover_url": (event.apartment.photos[0].url if event.apartment 
                         and event.apartment.photos 
                         and len(event.apartment.photos) > 0 
                         else None)
        } if event.apartment else None,
        
        "status": event.status,
        "woman_status": event.woman_status,
        "owner_status": event.owner_status,
        "message": getattr(event, 'message', None)
    }

@app.patch("/events/{event_id}")
def update_event(
    event_id: int,
    data: EventUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(["professionnel"])),
):
    event = db.query(models.Event).filter(models.Event.id == event_id, models.Event.owner_id == current_user.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable ou accès refusé")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(event, k, v)
    db.commit()
    db.refresh(event)
    return _event_dict(event, current_user)


@app.delete("/events/{event_id}", status_code=204)
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(["professionnel"])),
):
    event = db.query(models.Event).filter(models.Event.id == event_id, models.Event.owner_id == current_user.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable ou accès refusé")
    db.delete(event)
    db.commit()


@app.post("/photos/upload", status_code=201)
async def upload_photo_endpoint(
    file: UploadFile = File(...),
    is_premium: bool = Form(False),
    price: Optional[float] = Form(None),
    media_type: str = Form("photo"),
    is_story: bool = Form(False),
    story_text: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(["femme"])),
):
    contents = await file.read()

    # Upload Cloudinary
    result = upload_photo(contents, folder=f"maison_or/users/{current_user.id}")

    # Si c’est une STORY
    if is_story:
        story = models.Story(
            owner_id=current_user.id,
            url=result["url"],
            public_id=result["public_id"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        db.add(story)
        db.commit()
        db.refresh(story)

        return {
            "id": story.id,
            "is_story": True,
            "file_path": story.url,
            "story_text": story_text,
            "created_at": story.created_at.isoformat(),
        }

    photo = models.Photo(
        owner_id=current_user.id,
        url=result["url"],
        public_id=result["public_id"],
        thumbnail_url=result.get("thumbnail_url"),
        is_premium=is_premium,
        price=price if is_premium else None,
        caption=story_text,
    )

    db.add(photo)
    db.commit()
    db.refresh(photo)

    return {
        "id": photo.id,
        "is_story": False,
        "media_type": media_type,
        "file_path": photo.url,
        "is_premium": photo.is_premium,
        "price": photo.price,
        "created_at": photo.created_at.isoformat(),
        "views": 0
    }


@app.get("/photos/mine")
def my_photos(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(["femme"])),
):
    photos = db.query(models.Photo).filter(models.Photo.owner_id == current_user.id).order_by(models.Photo.created_at.desc()).all()
    return [_photo_dict(p, unlocked=True) for p in photos]


@app.get("/photos/user/{user_id}")
def get_user_photos(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    photos = db.query(models.Photo).filter(models.Photo.owner_id == user_id).order_by(models.Photo.created_at.desc()).all()
    unlocked_ids = {
        u.photo_id for u in db.query(models.PhotoUnlock).filter(models.PhotoUnlock.user_id == current_user.id).all()
    }
    return [_photo_dict(p, unlocked=(not p.is_premium or p.id in unlocked_ids)) for p in photos]


@app.delete("/photos/{photo_id}", status_code=204)
def delete_photo_endpoint(
    photo_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(["femme"])),
):
    photo = db.query(models.Photo).filter(
        models.Photo.id == photo_id,
        models.Photo.owner_id == current_user.id
    ).first()

    if not photo:
        raise HTTPException(status_code=404, detail="Photo introuvable")

    delete_photo(photo.public_id)
    db.delete(photo)
    db.commit()


@app.get("/photos")
def get_all_photos(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    photos = db.query(models.Photo).join(models.User).filter(
        models.User.role == "femme"
    ).order_by(models.Photo.created_at.desc()).all()

    unlocked_ids = {
        u.photo_id for u in db.query(models.PhotoUnlock).filter(
            models.PhotoUnlock.user_id == current_user.id
        ).all()
    }

    return [
        {
            "id": p.id,
            "url": p.url if (not p.is_premium or p.id in unlocked_ids) else None,
            "thumbnail_url": p.thumbnail_url,
            "media_type": "photo",
            "is_premium": p.is_premium,
            "price": p.price,
            "unlocked": (not p.is_premium) or (p.id in unlocked_ids),
            "owner_id": p.owner_id,
            "owner_name": p.owner.first_name,
            "owner_profile_image": p.owner.profile_image,  # ✅ AJOUTÉ !

        }
        for p in photos
    ]


@app.get("/users/{user_id}")
def get_user_profile(user_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    base = {
        "id": user.id,
        "role": user.role,
        "first_name": user.first_name,
        "username": user.username,
        "city": user.city,
        "is_verified": user.is_verified,
    }

    if user.role == "professionnel":
        base.update({
            "business_name": user.business_name,
            "business_type": user.business_type,
        })

    return base




@app.post("/payments/verify-event")
def verify_event_payment(
    data: VerifyPaymentEvent,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    existing = db.query(models.Reservation).filter(
        models.Reservation.transaction_id == data.transaction_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Transaction déjà utilisée")

    event = db.query(models.Event).filter(models.Event.id == data.event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")
    if event.seats_left <= 0:
        raise HTTPException(status_code=400, detail="Plus de places disponibles")

    success, amount = is_transaction_successful(data.transaction_id)
    if not success:
        raise HTTPException(status_code=402, detail="Transaction Kkiapay non confirmée")
    if amount < event.price:
        raise HTTPException(status_code=402, detail=f"Montant insuffisant (attendu {event.price} FCFA)")

    reservation = models.Reservation(
        event_id=event.id,
        user_id=current_user.id,
        status="confirmed",
        transaction_id=data.transaction_id,
        amount=amount,
    )
    event.seats_left -= 1
    db.add(reservation)
    db.commit()
    db.refresh(reservation)

    return {
        "message": "Réservation confirmée",
        "reservation_id": reservation.id,
        "location_exact": event.location_exact,
        "date": event.date.isoformat(),
        "owner_phone": event.owner.phone,
    }


@app.post("/payments/verify-photo")
def verify_photo_payment(
    data: VerifyPaymentPhoto,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    existing = db.query(models.PhotoUnlock).filter(
        models.PhotoUnlock.transaction_id == data.transaction_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Transaction déjà utilisée")

    photo = db.query(models.Photo).filter(models.Photo.id == data.photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo introuvable")
    if not photo.is_premium:
        raise HTTPException(status_code=400, detail="Photo non premium")

    already = db.query(models.PhotoUnlock).filter(
        models.PhotoUnlock.photo_id == data.photo_id,
        models.PhotoUnlock.user_id == current_user.id,
    ).first()
    if already:
        return {"message": "Déjà débloquée", "url": photo.url}

    success, amount = is_transaction_successful(data.transaction_id)
    if not success:
        raise HTTPException(status_code=402, detail="Transaction Kkiapay non confirmée")
    if amount < photo.price:
        raise HTTPException(status_code=402, detail=f"Montant insuffisant (attendu {photo.price} FCFA)")

    unlock = models.PhotoUnlock(
        photo_id=photo.id,
        user_id=current_user.id,
        transaction_id=data.transaction_id,
        amount=amount,
    )
    db.add(unlock)
    db.commit()

    return {"message": "Photo débloquée", "url": photo.url}


@app.get("/reservations/mine")
def my_reservations(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    reservations = db.query(models.Reservation).filter(
        models.Reservation.user_id == current_user.id,
        models.Reservation.status == "confirmed",
    ).order_by(models.Reservation.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "event_id": r.event_id,
            "event_title": r.event.title,
            "event_date": r.event.date.isoformat(),
            "location_exact": r.event.location_exact,
            "amount": r.amount,
            "created_at": r.created_at.isoformat(),
        }
        for r in reservations
    ]


def _event_dict(event: models.Event, user: models.User) -> dict:
    return _event_public_dict(event)


# def _event_public_dict(event: models.Event) -> dict:
#     return {
#         "id": event.id, "title": event.title,
#         "description": event.description[:120] + "…" if event.description and len(event.description) > 120 else event.description,
#         "event_type": event.event_type, "price": event.price, "max_guests": event.max_guests,
#         "seats_left": event.seats_left, "city": event.city, "location_hint": event.location_hint,
#         "date": event.date.isoformat(), "duration_hours": event.duration_hours,
#         "cover_url": event.cover_url,
#         "owner": {"id": event.owner.id, "first_name": event.owner.first_name, "username": event.owner.username},
#     }


def _photo_dict(photo: models.Photo, unlocked: bool) -> dict:
    return {
        "id": photo.id, "is_premium": photo.is_premium, "price": photo.price,
        "caption": photo.caption, "created_at": photo.created_at.isoformat(),
        "url": photo.url if unlocked else None,
        "thumbnail_url": photo.thumbnail_url,
        "unlocked": unlocked,
        "owner_id": photo.owner_id,
    }

@app.post("/stories", status_code=201)
async def create_story(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(["femme"])),
):
    contents = await file.read()
    result = upload_photo(contents, folder=f"maison_or/stories/{current_user.id}")

    story = models.Story(
        owner_id=current_user.id,
        url=result["url"],
        public_id=result["public_id"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    db.add(story)
    db.commit()
    db.refresh(story)

    return {
        "message": "Story créée",
        "story": {
            "id": story.id,
            "url": story.url,
            "owner_id": current_user.id,
            "owner_name": current_user.first_name,
            "created_at": story.created_at.isoformat(),
            "expires_at": story.expires_at.isoformat(),
        }
    }

@app.get("/stories")
def get_stories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    stories = db.query(models.Story).join(models.User).filter(
        models.Story.expires_at > now,
        models.User.role == "femme"
    ).order_by(models.Story.created_at.desc()).all()

    return [
        {
            "id": s.id,
            "url": s.url,
            "owner_id": s.owner_id,
            "owner_name": s.owner.first_name,
            "owner_profile_image": s.owner.profile_image,  # ✅ AJOUTÉ !
            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
        }
        for s in stories
    ]

@app.delete("/stories/{story_id}", status_code=204)
def delete_story(
    story_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(["femme"])),
):
    story = db.query(models.Story).filter(
        models.Story.id == story_id,
        models.Story.owner_id == current_user.id
    ).first()

    if not story:
        raise HTTPException(status_code=404, detail="Story introuvable")

    delete_photo(story.public_id)

    db.delete(story)
    db.commit()



@app.post("/profile-picture/upload", status_code=201)
async def upload_profile_picture(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Validation
    if not file.content_type.startswith('image/'):
        raise HTTPException(400, "Fichier image requis")
    if file.size > 5 * 1024 * 1024:  # 5MB
        raise HTTPException(400, "Image trop grande (max 5MB)")
    
    contents = await file.read()
    
    # Supprime ancienne photo
    if current_user.profile_public_id:
        delete_photo(current_user.profile_public_id)
    
    # ✅ UPLOAD SANS transformation
    result = upload_photo(
        contents, 
        folder=f"maison_or/profiles/{current_user.id}"
    )
    
    # Met à jour user
    current_user.profile_image = result["url"]
    current_user.profile_public_id = result["public_id"]
    db.commit()
    
    return {
        "message": "Photo de profil mise à jour",
        "profile_image": result["url"]
    }

@app.delete("/profile-picture", status_code=204)
def delete_profile_picture(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.profile_public_id:
        delete_photo(current_user.profile_public_id)
    
    current_user.profile_image = None
    current_user.profile_public_id = None
    db.commit()
    return {"message": "Photo de profil supprimée"}


@app.get("/profile-picture/{user_id}")
def get_profile_picture(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    
    return {
        "profile_image": user.profile_image,
        "has_profile_picture": bool(user.profile_image)
    }


@app.get("/me")
def get_me(current_user: models.User = Depends(get_current_user)):
    base = {
        "id": current_user.id, 
        "email": current_user.email, 
        "role": current_user.role,
        "profile_image": current_user.profile_image,  # 👈 AJOUTE
        "is_verified": current_user.is_verified, 
        "first_name": current_user.first_name,
        "username": current_user.username, 
        "phone": current_user.phone, 
        "city": current_user.city,
    }
    if current_user.role == "professionnel":
        base.update({
            "business_name": current_user.business_name, 
            "business_type": current_user.business_type
        })
    return base


@app.get("/apartments/mine")
def get_my_apartments(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)  
):
    if current_user.role != "professionnel":
        raise HTTPException(status_code=403, detail="Réservé aux professionnels")
    
    apartments = db.query(models.Apartment).filter(
        models.Apartment.owner_id == current_user.id
    ).order_by(models.Apartment.created_at.desc()).all()
    
    return [
        {
            "id": a.id,
            "title": a.title,
            "description": a.description or "",
            "city": a.city,
            "price": float(a.price),
            "is_active": a.is_active,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "photos": []
        }
        for a in apartments
    ]


@app.post("/apartments", status_code=201)
def create_apartment(
    data: ApartmentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role != "professionnel":
        raise HTTPException(status_code=403, detail="Réservé aux professionnels")
    apartment = models.Apartment(
        owner_id=current_user.id,
        title=data.title,
        description=data.description,
        city=data.city,
        price=data.price,
        # address=data.address,
    )
    db.add(apartment)
    db.commit()
    db.refresh(apartment)
    return apartment



@app.get("/apartments")
def list_apartments(db: Session = Depends(get_db)):
    apartments = db.query(models.Apartment).filter(models.Apartment.is_active == True).all()
    return [
        {
            "id": a.id,
            "title": a.title,
            "description": a.description or None,
            "city": a.city,
            "price": float(a.price),
            "is_active": a.is_active,
            "owner_id": a.owner_id,
            "owner_name": a.owner.first_name,           # ✅
            "owner_profile_image": a.owner.profile_image, # ✅
            "cover_url": (a.photos[0].url if a.photos else None) if hasattr(a, 'photos') and a.photos else None,
        }
        for a in apartments
    ]



@app.post("/apartments/{apartment_id}/photos", status_code=201)
async def upload_apartment_photo(
    apartment_id: int,
    file: UploadFile = File(...),
    is_cover: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Vérif appart appartient au user
    apartment = db.query(models.Apartment).filter(
        models.Apartment.id == apartment_id,
        models.Apartment.owner_id == current_user.id
    ).first()
    if not apartment:
        raise HTTPException(status_code=404, detail="Appartement introuvable")
    
    # Validation fichier
    if not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="Image requise")
    contents = await file.read()
    
    # Upload Cloudinary
    result = upload_photo(contents, folder=f"maison_or/apartments/{apartment_id}")
    
    # Créé photo
    photo = models.ApartmentPhoto(
        apartment_id=apartment_id,
        url=result["url"],
        public_id=result["public_id"],
        is_cover=is_cover
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    
    return {
        "id": photo.id,
        "url": photo.url,
        "is_cover": photo.is_cover,
        "created_at": photo.created_at.isoformat()
    }



@app.delete("/apartments/photos/{photo_id}", status_code=204)
def delete_apartment_photo(
    photo_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    photo = db.query(models.ApartmentPhoto).filter(
        models.ApartmentPhoto.id == photo_id
    ).first()
    
    if not photo:
        raise HTTPException(status_code=404, detail="Photo introuvable")
    
    if photo.apartment.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Non autorisé")
    
    # Supprime Cloudinary
    delete_photo(photo.public_id)
    db.delete(photo)
    db.commit()
    
    return {"message": "Photo supprimée"}



@app.post("/events/{event_id}/action")
def event_action(
    event_id: int, 
    payload: EventAction, 
    db: Session = Depends(get_db),  # 👈 AJOUTÉ !
    current_user: models.User = Depends(get_current_user)
):
    event = db.query(models.Event).filter(models.Event.id == event_id).first()  # models.Event
    
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")

    action = payload.action

    # 👇 FEMME
    if current_user.role == "femme":
        if action == "accept":
            event.woman_status = "accepted"
        elif action == "refuse":
            event.woman_status = "refused"

    # 👇 PROFESSIONNEL
    if current_user.role == "professionnel":
        if action == "accept":
            event.owner_status = "accepted"
        elif action == "refuse":
            event.owner_status = "refused"

    # 💥 CHECK GLOBAL STATUS
    if event.woman_status == "accepted" and event.owner_status == "accepted":
        event.status = "confirmed"
        # 🔔 NOTIFICATION HOMME (corrige create_notification après)
        # create_notification(event.requester_id, "event_confirmed", "Votre demande a été acceptée 🎉")

    db.commit()
    db.refresh(event)
    return _event_public_dict(event)  # Retourne format correct

@app.get("/notifications")
def get_notifications(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    notifs = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id
    ).order_by(models.Notification.created_at.desc()).all()

    return [
        {
            "id": n.id,
            "message": n.message,
            "type": n.type,
            "event_id": n.event_id,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
        }
        for n in notifs
    ]


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    notif = db.query(models.Notification).filter(
        models.Notification.id == notification_id,
        models.Notification.user_id == current_user.id
    ).first()

    if not notif:
        raise HTTPException(status_code=404, detail="Notification introuvable")

    notif.is_read = True
    db.commit()
    return {"message": "Notification marquée comme lue"}



@app.post("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.is_read == False
    ).update({models.Notification.is_read: True})
    db.commit()
    return {"message": "Toutes les notifications marquées comme lues"}