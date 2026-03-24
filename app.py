import os
import logging
import secrets
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from database import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI()

# Configuration Sécurité & Sessions
app.add_middleware(SessionMiddleware, secret_key=os.environ.get('SECRET_KEY', 'fallback_dev_key'))

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
async def login_get(request: Request, erreur: str = None, succes: str = None):
    return templates.TemplateResponse(request, 'login.html', {'erreur': erreur, 'succes': succes})

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
    return RedirectResponse(url='/login?erreur=Identifiants incorrects', status_code=302)

@app.get('/forgot-password', response_class=HTMLResponse)
async def forgot_password_get(request: Request):
    return templates.TemplateResponse(request, 'forgot_password.html', {})

@app.post('/forgot-password', response_class=HTMLResponse)
async def forgot_password_post(request: Request, login: str = Form(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_user, email FROM UTILISATEUR WHERE login = %s", (login,))
    user = cursor.fetchone()

    if not user or not user[1]:
        conn.close()
        return templates.TemplateResponse(request, 'forgot_password.html', {'erreur': 'Identifiant introuvable ou aucun email associé.'})

    token = secrets.token_urlsafe(32)
    cursor.execute("DELETE FROM RESET_TOKEN WHERE id_user = %s", (user[0],))
    cursor.execute("INSERT INTO RESET_TOKEN (token, id_user) VALUES (%s, %s)", (token, user[0]))
    conn.commit()
    conn.close()

    base_url = os.environ.get('BASE_URL', 'https://sae401-app.vercel.app')
    lien = f"{base_url}/reset-password/{token}"
    message = Mail(
        from_email=os.environ.get('SENDGRID_FROM_EMAIL'),
        to_emails=user[1],
        subject="Réinitialisation de votre mot de passe Mouv'Sport",
        html_content=f"""
            <p>Bonjour,</p>
            <p>Cliquez sur le lien ci-dessous pour réinitialiser votre mot de passe :</p>
            <p><a href="{lien}">{lien}</a></p>
            <p>Ce lien expire dans 1 heure.</p>
        """
    )
    try:
        sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
        sg.send(message)
    except Exception as e:
        logging.error('Erreur envoi email: %s', e)

    return templates.TemplateResponse(request, 'forgot_password.html', {'succes': 'Un email de réinitialisation a été envoyé.'})

@app.get('/reset-password/{token}', response_class=HTMLResponse)
async def reset_password_get(request: Request, token: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_user FROM RESET_TOKEN WHERE token = %s AND expire_at > NOW()", (token,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return RedirectResponse(url='/login?erreur=Lien invalide ou expiré')
    return templates.TemplateResponse(request, 'reset_password.html', {'token': token})

@app.post('/reset-password/{token}')
async def reset_password_post(request: Request, token: str, password: str = Form(...), confirm: str = Form(...)):
    if password != confirm:
        return templates.TemplateResponse(request, 'reset_password.html', {'token': token, 'erreur': 'Les mots de passe ne correspondent pas.'})
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_user FROM RESET_TOKEN WHERE token = %s AND expire_at > NOW()", (token,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return RedirectResponse(url='/login?erreur=Lien invalide ou expiré')
    cursor.execute("UPDATE UTILISATEUR SET password = %s WHERE id_user = %s", (generate_password_hash(password), row[0]))
    cursor.execute("DELETE FROM RESET_TOKEN WHERE token = %s", (token,))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/login?succes=Mot de passe réinitialisé avec succès', status_code=302)

@app.get('/register', response_class=HTMLResponse)
async def register_get(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_club, nom_club FROM CLUB ORDER BY nom_club ASC")
    clubs = cursor.fetchall()
    conn.close()
    return templates.TemplateResponse(request, 'register.html', {'clubs': clubs})

@app.post('/register')
async def register_post(request: Request, login: str = Form(...), password: str = Form(...), id_club: int = Form(...)):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        mdp_hash = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO UTILISATEUR (login, password, role, id_club, est_verifie) VALUES (%s, %s, %s, %s, %s)",
            (login, mdp_hash, 'RESP_CLUB', id_club, True)
        )
        conn.commit()
        conn.close()
        return RedirectResponse(url='/login?succes=Compte créé avec succès', status_code=302)
    except Exception as e:
        return HTMLResponse(f"Erreur inscription : {str(e)}")

# --- MERCATO ---

@app.get('/mercato', response_class=HTMLResponse)
async def mercato(request: Request, search: str = None, poste: str = 'Tous', age_min: int = 0, age_max: int = 99, succes: str = None):
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
        'age_max': age_max,
        'succes': succes
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
    cursor.execute(
        "INSERT INTO OFFRE (id_joueur, id_club_acheteur, montant, type_mutation) VALUES (%s, %s, %s, %s)",
        (id_joueur, request.session['id_club'], montant, type_mutation)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url='/mercato?succes=Offre envoyée avec succès', status_code=302)

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
    except Exception as e:
        logging.error('Erreur chargement offres: %s', e)

    conn.close()
    return templates.TemplateResponse(request, 'club_dashboard.html', {
        'club': club,
        'joueurs': joueurs,
        'offres_recues': offres_recues,
        'offres_envoyees': offres_envoyees
    })

@app.get('/club/badge')
async def club_badge(request: Request):
    if 'user_id' not in request.session:
        return JSONResponse({'total': 0})
    conn = get_db_connection()
    cursor = conn.cursor()
    mon_club_id = request.session['id_club']
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM OFFRE o
            JOIN JOUEUR j ON o.id_joueur = j.id_joueur
            WHERE j.id_club_actuel = %s AND o.statut = 'en_attente'
        """, (mon_club_id,))
        recues = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM OFFRE WHERE id_club_acheteur = %s", (mon_club_id,))
        envoyees = cursor.fetchone()[0]
    except Exception as e:
        logging.error('Erreur badge: %s', e)
        recues, envoyees = 0, 0
    conn.close()
    return JSONResponse({'total': recues + envoyees})

@app.get('/offre/{id_offre}/accepter')
async def accepter_offre(request: Request, id_offre: int):
    if 'user_id' not in request.session:
        return RedirectResponse(url='/login')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE OFFRE SET statut = 'acceptee'
        WHERE id_offre = %s AND id_joueur IN (
            SELECT id_joueur FROM JOUEUR WHERE id_club_actuel = %s
        )
    """, (id_offre, request.session['id_club']))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/club', status_code=302)

@app.get('/offre/{id_offre}/refuser')
async def refuser_offre(request: Request, id_offre: int):
    if 'user_id' not in request.session:
        return RedirectResponse(url='/login')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE OFFRE SET statut = 'refusee'
        WHERE id_offre = %s AND id_joueur IN (
            SELECT id_joueur FROM JOUEUR WHERE id_club_actuel = %s
        )
    """, (id_offre, request.session['id_club']))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/club', status_code=302)

@app.get('/offre/{id_offre}/messages')
async def get_messages(request: Request, id_offre: int):
    if 'user_id' not in request.session:
        return HTMLResponse('', status_code=401)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.contenu, c.nom_club, m.date_message,
               CASE WHEN m.id_club_envoyeur = %s THEN true ELSE false END as is_me
        FROM MESSAGE m
        JOIN CLUB c ON m.id_club_envoyeur = c.id_club
        WHERE m.id_offre = %s
        ORDER BY m.date_message ASC
    """, (request.session['id_club'], id_offre))
    messages = cursor.fetchall()
    conn.close()
    return JSONResponse([{'contenu': m[0], 'club': m[1], 'date': str(m[2]), 'is_me': m[3]} for m in messages])

@app.post('/offre/{id_offre}/messages')
async def envoyer_message(request: Request, id_offre: int, contenu: str = Form(...)):
    if 'user_id' not in request.session:
        return HTMLResponse('', status_code=401)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO MESSAGE (id_offre, id_club_envoyeur, contenu) VALUES (%s, %s, %s)",
        (id_offre, request.session['id_club'], contenu)
    )
    conn.commit()
    conn.close()
    return JSONResponse({'ok': True})



@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login')