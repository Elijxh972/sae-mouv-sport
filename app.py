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

# --- MERCATO ---

@app.get('/mercato', response_class=HTMLResponse)
async def mercato(request: Request, search: str = None, poste: str = 'Tous', age_min: int = 0, age_max: int = 99):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT poste FROM JOUEUR WHERE poste IS NOT NULL ORDER BY poste")
    postes_dispo = [r[0] for r in cursor.fetchall()]

    query = """
        SELECT j.id_joueur, j.nom, j.prenom, j.poste, c.nom_club,
               EXTRACT(YEAR FROM AGE(j.date_naissance))::int as age
        FROM JOUEUR j
        JOIN CLUB c ON j.id_club_actuel = c.id_club
        WHERE EXTRACT(YEAR FROM AGE(j.date_naissance)) BETWEEN %s AND %s
        AND j.id_club_actuel != %s
    """
    params = [age_min, age_max, request.session.get('id_club', -1)]

    if search:
        query += " AND (j.nom ILIKE %s OR j.prenom ILIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    if poste and poste != 'Tous':
        query += " AND j.poste = %s"
        params.append(poste)

    query += " ORDER BY j.nom ASC"
    cursor.execute(query, params)
    joueurs = cursor.fetchall()
    conn.close()

    return templates.TemplateResponse(request, 'mercato.html', {
        'joueurs': joueurs,
        'postes_dispo': postes_dispo,
        'search': search,
        'poste_active': poste,
        'age_min': age_min,
        'age_max': age_max
    })

# --- OFFRES ---

@app.get('/faire_offre/{id_joueur}', response_class=HTMLResponse)
async def faire_offre_get(request: Request, id_joueur: int):
    if 'user_id' not in request.session:
        return RedirectResponse(url='/login')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT nom, prenom FROM JOUEUR WHERE id_joueur = %s", (id_joueur,))
    joueur = cursor.fetchone()
    conn.close()
    return templates.TemplateResponse(request, 'faire_offre.html', {'joueur': joueur, 'id_joueur': id_joueur})

@app.post('/faire_offre/{id_joueur}')
async def faire_offre_post(request: Request, id_joueur: int, montant: int = Form(...), type_mutation: str = Form(...)):
    if 'user_id' not in request.session:
        return RedirectResponse(url='/login')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS OFFRE (
            id_offre SERIAL PRIMARY KEY,
            id_joueur INT,
            id_club_acheteur INT,
            montant INT,
            type_mutation TEXT,
            statut TEXT DEFAULT 'en_attente',
            date_offre TIMESTAMP DEFAULT NOW()
        )
    """)
    cursor.execute("SELECT id_club_actuel FROM JOUEUR WHERE id_joueur = %s", (id_joueur,))
    id_club_vendeur = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO OFFRE (id_joueur, id_club_acheteur, montant, type_mutation) VALUES (%s, %s, %s, %s)",
        (id_joueur, request.session['id_club'], montant, type_mutation)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url='/mercato', status_code=302)

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

    cursor.execute("SELECT id_user, login FROM UTILISATEUR WHERE id_club = %s AND role = 'JOUEUR' AND est_verifie = FALSE", (mon_club_id,))
    joueurs_en_attente = cursor.fetchall()

    offres_recues = []
    offres_envoyees = []
    try:
        cursor.execute("""
            SELECT o.id_offre, j.nom, j.prenom, c.nom_club, o.montant, o.type_mutation
            FROM OFFRE o
            JOIN JOUEUR j ON o.id_joueur = j.id_joueur
            JOIN CLUB c ON o.id_club_acheteur = c.id_club
            WHERE j.id_club_actuel = %s AND o.statut = 'en_attente'
        """, (mon_club_id,))
        offres_recues = cursor.fetchall()

        cursor.execute("""
            SELECT o.id_offre, j.nom, j.prenom, c.nom_club, o.montant, o.type_mutation, o.statut
            FROM OFFRE o
            JOIN JOUEUR j ON o.id_joueur = j.id_joueur
            JOIN CLUB c ON j.id_club_actuel = c.id_club
            WHERE o.id_club_acheteur = %s
            ORDER BY o.date_offre DESC
        """, (mon_club_id,))
        offres_envoyees = cursor.fetchall()
    except:
        pass

    conn.close()
    return templates.TemplateResponse(request, 'club_dashboard.html', {
        'club': club,
        'joueurs': joueurs,
        'joueurs_en_attente': joueurs_en_attente,
        'offres_recues': offres_recues,
        'offres_envoyees': offres_envoyees
    })

@app.get('/offre/{id_offre}/accepter')
async def accepter_offre(request: Request, id_offre: int):
    if 'user_id' not in request.session:
        return RedirectResponse(url='/login')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE OFFRE SET statut = 'acceptee' WHERE id_offre = %s", (id_offre,))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/club', status_code=302)

@app.get('/offre/{id_offre}/refuser')
async def refuser_offre(request: Request, id_offre: int):
    if 'user_id' not in request.session:
        return RedirectResponse(url='/login')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE OFFRE SET statut = 'refusee' WHERE id_offre = %s", (id_offre,))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/club', status_code=302)

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