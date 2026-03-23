import os
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from database import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI()

# Configuration Sécurité & Sessions
app.add_middleware(SessionMiddleware, secret_key='MA_CLE_SECRETE_SAE')

# Fichiers Statiques & Templates
static_dir = os.path.join(BASE_DIR, "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# --- ROUTES AUTHENTIFICATION ---

@app.get('/', response_class=HTMLResponse)
async def accueil(request: Request):
    conn = get_db_connection()
    mutations = []
    if conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT j.nom, j.prenom, c1.nom_club, c2.nom_club
            FROM MUTATION m
            JOIN JOUEUR j ON m.id_joueur = j.id_joueur
            JOIN CLUB c1 ON m.id_club_depart = c1.id_club
            JOIN CLUB c2 ON m.id_club_arrivee = c2.id_club
            ORDER BY m.type_mutation
        """)
        mutations = cursor.fetchall()
        conn.close()
    return templates.TemplateResponse(request, 'accueil.html', {'mutations': mutations})

@app.get('/login', response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(request, 'login.html')

@app.post('/login')
async def login_post(request: Request, login: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_user, password, role, id_club, est_verifie FROM UTILISATEUR WHERE login = %s", (login,))
    user = cursor.fetchone()
    conn.close()
    
    if user and check_password_hash(user[1], password):
        user_id, _, role, id_club, est_verifie = user
        if role == 'JOUEUR' and not est_verifie:
            return HTMLResponse("Compte en attente de validation par le club.")
        
        request.session['user_id'] = user_id
        request.session['role'] = role
        request.session['id_club'] = id_club
        return RedirectResponse(url='/club' if role == 'RESP_CLUB' else '/', status_code=302)
    return HTMLResponse("Identifiants incorrects. <a href='/login'>Réessayer</a>")

@app.get('/register', response_class=HTMLResponse)
async def register_get(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_club, nom_club FROM CLUB ORDER BY nom_club ASC")
    clubs = cursor.fetchall()
    conn.close()
    return templates.TemplateResponse(request, 'register.html', {'clubs': clubs})

@app.post('/register')
async def register_post(request: Request, login: str = Form(...), password: str = Form(...), 
                        role: str = Form(...), id_club: int = Form(...), nom: str = Form(None), 
                        prenom: str = Form(None), date_naissance: str = Form(None)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        mdp_hash = generate_password_hash(password)
        
        # 1. Insertion Utilisateur
        cursor.execute(
            "INSERT INTO UTILISATEUR (login, password, role, id_club, est_verifie) VALUES (%s, %s, %s, %s, %s)",
            (login, mdp_hash, role.upper(), id_club, role.upper() != 'JOUEUR')
        )
        
        # 2. Si Joueur, insertion dans la table JOUEUR avec date_naissance
        if role.upper() == 'JOUEUR':
            cursor.execute(
                "INSERT INTO JOUEUR (nom, prenom, date_naissance, id_club_actuel) VALUES (%s, %s, %s, %s)",
                (nom, prenom, date_naissance, id_club)
            )
        
        conn.commit()
        conn.close()
        return RedirectResponse(url='/login', status_code=302)
    except Exception as e:
        return HTMLResponse(f"Erreur inscription : {str(e)}")

# --- ESPACE CLUB (CORRIGÉ) ---

@app.get('/club', response_class=HTMLResponse)
async def club_dashboard(request: Request):
    if 'user_id' not in request.session: return RedirectResponse(url='/login')
    
    mon_club_id = request.session['id_club']
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id_club, nom_club FROM CLUB WHERE id_club = %s", (mon_club_id,))
    club = cursor.fetchone()
    
    # Ordre Strict : 0:id, 1:nom, 2:prenom, 3:poste, 4:date, 5:age
    cursor.execute("""
        SELECT id_joueur, nom, prenom, poste, date_naissance,
               EXTRACT(YEAR FROM AGE(date_naissance))::int as age
        FROM JOUEUR WHERE id_club_actuel = %s ORDER BY nom ASC
    """, (mon_club_id,))
    joueurs = cursor.fetchall()

    conn.close()
    return templates.TemplateResponse(request, 'club_dashboard.html', {'club': club, 'joueurs': joueurs})

@app.get('/update_db')
async def update_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("ALTER TABLE JOUEUR ADD COLUMN IF NOT EXISTS date_naissance DATE")
    cursor.execute("ALTER TABLE JOUEUR ADD COLUMN IF NOT EXISTS poste TEXT")
    cursor.execute("ALTER TABLE UTILISATEUR ADD COLUMN IF NOT EXISTS est_verifie BOOLEAN DEFAULT TRUE")
    conn.commit()
    conn.close()
    return "Base de données à jour."

@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login')