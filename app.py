import os
import logging
import secrets
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from database import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _is_production() -> bool:
    if os.environ.get("VERCEL"):
        return True
    return os.environ.get("ENV", "").lower() in ("production", "prod", "vercel")


_secret_key = os.environ.get("SECRET_KEY", "")
if _is_production() and (not _secret_key or _secret_key == "fallback_dev_key"):
    raise RuntimeError(
        "SECRET_KEY doit être défini en production (variable d'environnement, chaîne longue et aléatoire)."
    )
if not _secret_key:
    _secret_key = "fallback_dev_key"
    logging.warning("SECRET_KEY non défini : utilisation d'une clé de développement (à ne jamais utiliser en production).")

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key=_secret_key,
    session_cookie="session",
    max_age=7 * 24 * 60 * 60,
    same_site="lax",
    https_only=_is_production(),
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def _session_club_id(request: Request):
    cid = request.session.get("id_club")
    return cid if cid is not None else -1


def _require_resp_club(request: Request):
    """Retourne une RedirectResponse si l'utilisateur n'est pas un responsable club connecté."""
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=302)
    if request.session.get("role") != "RESP_CLUB":
        return RedirectResponse(url="/login", status_code=302)
    if request.session.get("id_club") is None:
        return RedirectResponse(url="/login", status_code=302)
    return None

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
            SELECT j.nom, j.prenom, c1.nom_club, c2.nom_club,
                   m.date_demande, m.montant_transfert
            FROM mutation m
            JOIN joueur j ON m.id_joueur = j.id_joueur
            JOIN club c1 ON m.id_club_depart = c1.id_club
            JOIN club c2 ON m.id_club_arrivee = c2.id_club
            WHERE m.date_demande >= (CURRENT_DATE - INTERVAL '30 days')::date
            ORDER BY m.date_demande DESC NULLS LAST
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
        if role == 'RESP_CLUB':
            return RedirectResponse(url='/club', status_code=302)
        if role == 'ADMIN':
            return RedirectResponse(url='/admin', status_code=302)
        return RedirectResponse(url='/', status_code=302)
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
        logging.exception("Inscription: %s", e)
        return HTMLResponse(
            "Impossible de créer le compte. Vérifiez vos informations ou contactez un administrateur.",
            status_code=400,
        )

# --- MERCATO ---

@app.get('/mercato', response_class=HTMLResponse)
async def mercato(
    request: Request,
    search: Optional[str] = None,
    poste: str = 'Tous',
    age_min: int = 0,
    age_max: int = 99,
    id_club: Optional[str] = Query(None),
    succes: Optional[str] = None,
):
    redir = _require_resp_club(request)
    if redir:
        return redir
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT poste FROM JOUEUR WHERE poste IS NOT NULL ORDER BY poste")
    postes_dispo = [r[0] for r in cursor.fetchall()]

    cursor.execute("SELECT id_club, nom_club FROM CLUB ORDER BY nom_club ASC")
    clubs_list = cursor.fetchall()

    id_club_filtre = None
    if id_club is not None and str(id_club).strip().isdigit():
        id_club_filtre = int(str(id_club).strip())

    query = """
        SELECT j.id_joueur, j.nom, j.prenom, j.poste, c.nom_club,
               EXTRACT(YEAR FROM AGE(j.date_naissance))::int as age
        FROM JOUEUR j
        JOIN CLUB c ON j.id_club_actuel = c.id_club
        WHERE EXTRACT(YEAR FROM AGE(j.date_naissance)) BETWEEN %s AND %s
        AND j.id_club_actuel != %s
    """
    params = [age_min, age_max, _session_club_id(request)]

    if search:
        query += " AND (j.nom ILIKE %s OR j.prenom ILIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    if poste and poste != 'Tous':
        query += " AND j.poste = %s"
        params.append(poste)
    if id_club_filtre is not None:
        query += " AND j.id_club_actuel = %s"
        params.append(id_club_filtre)

    query += " ORDER BY j.nom ASC"
    cursor.execute(query, params)
    joueurs = cursor.fetchall()
    conn.close()

    return templates.TemplateResponse(request, 'mercato.html', {
        'joueurs': joueurs,
        'postes_dispo': postes_dispo,
        'clubs_list': clubs_list,
        'search': search,
        'poste_active': poste,
        'age_min': age_min,
        'age_max': age_max,
        'id_club_filtre': id_club_filtre,
        'succes': succes
    })

# --- OFFRES ---

@app.get('/faire_offre/{id_joueur}', response_class=HTMLResponse)
async def faire_offre_get(request: Request, id_joueur: int):
    redir = _require_resp_club(request)
    if redir:
        return redir
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT nom, prenom FROM JOUEUR WHERE id_joueur = %s", (id_joueur,))
    joueur = cursor.fetchone()
    conn.close()
    return templates.TemplateResponse(request, 'faire_offre.html', {'joueur': joueur, 'id_joueur': id_joueur})

@app.post('/faire_offre/{id_joueur}')
async def faire_offre_post(request: Request, id_joueur: int, montant: int = Form(...), type_mutation: str = Form(...)):
    redir = _require_resp_club(request)
    if redir:
        return redir
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
    redir = _require_resp_club(request)
    if redir:
        return redir

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
    offres_attente_ligue = []
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
            SELECT o.id_offre, j.nom, j.prenom, c.nom_club, o.montant, o.type_mutation
            FROM OFFRE o
            JOIN JOUEUR j ON o.id_joueur = j.id_joueur
            JOIN CLUB c ON o.id_club_acheteur = c.id_club
            WHERE j.id_club_actuel = %s AND o.statut = 'acceptee'
            ORDER BY o.date_offre DESC
        """, (mon_club_id,))
        offres_attente_ligue = cursor.fetchall()

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
        'offres_attente_ligue': offres_attente_ligue,
        'offres_envoyees': offres_envoyees
    })

@app.get('/club/badge')
async def club_badge(request: Request):
    if request.session.get("role") != "RESP_CLUB" or request.session.get("id_club") is None:
        return JSONResponse({'total': 0})
    conn = get_db_connection()
    cursor = conn.cursor()
    mon_club_id = request.session['id_club']
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM OFFRE o
            JOIN JOUEUR j ON o.id_joueur = j.id_joueur
            WHERE j.id_club_actuel = %s AND o.statut IN ('en_attente', 'acceptee')
        """, (mon_club_id,))
        recues = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM OFFRE WHERE id_club_acheteur = %s", (mon_club_id,))
        envoyees = cursor.fetchone()[0]
    except Exception as e:
        logging.error('Erreur badge: %s', e)
        recues, envoyees = 0, 0
    conn.close()
    return JSONResponse({'total': recues + envoyees})

@app.post('/offre/{id_offre}/accepter')
async def accepter_offre(request: Request, id_offre: int):
    redir = _require_resp_club(request)
    if redir:
        return redir
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE OFFRE SET statut = 'acceptee'
        WHERE id_offre = %s AND id_joueur IN (
            SELECT id_joueur FROM JOUEUR WHERE id_club_actuel = %s
        ) AND statut = 'en_attente'
    """, (id_offre, request.session['id_club']))
    if cursor.rowcount == 0:
        conn.rollback()
        conn.close()
        return RedirectResponse(url='/club', status_code=302)
    conn.commit()
    conn.close()
    return RedirectResponse(url=f'/transfert/attente-ligue/{id_offre}', status_code=302)


@app.get('/transfert/attente-ligue/{id_offre}', response_class=HTMLResponse)
async def transfert_attente_ligue(request: Request, id_offre: int):
    redir = _require_resp_club(request)
    if redir:
        return redir
    mon_club = request.session.get('id_club')
    conn = get_db_connection()
    if not conn:
        return RedirectResponse(url='/club')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT j.nom, j.prenom, c_ach.nom_club, o.montant, o.type_mutation, o.statut
        FROM OFFRE o
        JOIN JOUEUR j ON o.id_joueur = j.id_joueur
        JOIN CLUB c_ach ON o.id_club_acheteur = c_ach.id_club
        WHERE o.id_offre = %s AND j.id_club_actuel = %s
    """, (id_offre, mon_club))
    row = cursor.fetchone()
    conn.close()
    if not row or row[5] != 'acceptee':
        return RedirectResponse(url='/club', status_code=302)
    return templates.TemplateResponse(request, 'transfert_attente_ligue.html', {
        'joueur_nom': row[0],
        'joueur_prenom': row[1],
        'club_acheteur': row[2],
        'montant': row[3],
        'type_mutation': row[4],
        'id_offre': id_offre
    })

@app.post('/offre/{id_offre}/refuser')
async def refuser_offre(request: Request, id_offre: int):
    redir = _require_resp_club(request)
    if redir:
        return redir
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

def _club_peut_voir_offre(cursor, id_offre: int, id_club: int) -> bool:
    """Acheteur, club actuel du joueur, ou ancien club vendeur (après validation ligue)."""
    cursor.execute("""
        SELECT 1 FROM OFFRE o
        JOIN JOUEUR j ON o.id_joueur = j.id_joueur
        WHERE o.id_offre = %s AND (
            o.id_club_acheteur = %s
            OR j.id_club_actuel = %s
            OR EXISTS (
                SELECT 1 FROM MUTATION m
                WHERE m.id_joueur = o.id_joueur
                  AND m.id_club_depart = %s
                  AND m.id_club_arrivee = o.id_club_acheteur
            )
        )
    """, (id_offre, id_club, id_club, id_club))
    return cursor.fetchone() is not None


@app.get('/offre/{id_offre}/messages')
async def get_messages(request: Request, id_offre: int):
    if request.session.get("role") != "RESP_CLUB" or request.session.get("id_club") is None:
        return JSONResponse({'error': 'Non autorisé'}, status_code=401)
    conn = get_db_connection()
    cursor = conn.cursor()
    if not _club_peut_voir_offre(cursor, id_offre, request.session['id_club']):
        conn.close()
        return HTMLResponse('', status_code=403)
    cursor.execute("""
        SELECT m.contenu, c.nom_club, m.date_message,
               CASE WHEN m.id_club_emetteur = %s THEN true ELSE false END as is_me
        FROM MESSAGE m
        JOIN CLUB c ON m.id_club_emetteur = c.id_club
        WHERE m.id_offre = %s
        ORDER BY m.date_message ASC
    """, (request.session['id_club'], id_offre))
    messages = cursor.fetchall()
    conn.close()
    return JSONResponse([{'contenu': m[0], 'club': m[1], 'date': str(m[2]), 'is_me': m[3]} for m in messages])

@app.post('/offre/{id_offre}/messages')
async def envoyer_message(request: Request, id_offre: int, contenu: str = Form(...)):
    if request.session.get("role") != "RESP_CLUB" or request.session.get("id_club") is None:
        return JSONResponse({'error': 'Non autorisé'}, status_code=401)
    conn = get_db_connection()
    cursor = conn.cursor()
    if not _club_peut_voir_offre(cursor, id_offre, request.session['id_club']):
        conn.close()
        return HTMLResponse('', status_code=403)
    cursor.execute(
        "INSERT INTO MESSAGE (id_offre, id_club_emetteur, contenu) VALUES (%s, %s, %s)",
        (id_offre, request.session['id_club'], contenu)
    )
    conn.commit()
    conn.close()
    return JSONResponse({'ok': True})


# --- ADMIN (LIGUE) : validation définitive des transferts ---

@app.get('/admin', response_class=HTMLResponse)
async def admin_dashboard(request: Request, succes: str = None, erreur: str = None):
    if request.session.get('role') != 'ADMIN':
        return RedirectResponse(url='/login', status_code=302)
    conn = get_db_connection()
    demandes = []
    if conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.id_offre, j.nom, j.prenom,
                   c_v.nom_club, c_a.nom_club,
                   o.montant,
                   ROUND(o.montant * 0.03)::int,
                   o.type_mutation
            FROM OFFRE o
            JOIN JOUEUR j ON o.id_joueur = j.id_joueur
            JOIN CLUB c_v ON j.id_club_actuel = c_v.id_club
            JOIN CLUB c_a ON o.id_club_acheteur = c_a.id_club
            WHERE o.statut = 'acceptee'
            ORDER BY o.date_offre ASC
        """)
        demandes = cursor.fetchall()
        conn.close()
    return templates.TemplateResponse(request, 'admin.html', {
        'demandes': demandes,
        'succes': succes,
        'erreur': erreur
    })


@app.post('/admin/transfert/{id_offre}/valider')
async def admin_valider_transfert(request: Request, id_offre: int):
    if request.session.get('role') != 'ADMIN':
        return RedirectResponse(url='/login', status_code=302)
    conn = get_db_connection()
    if not conn:
        return RedirectResponse(url='/admin?erreur=Connexion%20base%20indisponible', status_code=302)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT o.id_joueur, o.id_club_acheteur, j.id_club_actuel, o.type_mutation, o.montant
            FROM OFFRE o
            JOIN JOUEUR j ON o.id_joueur = j.id_joueur
            WHERE o.id_offre = %s AND o.statut = 'acceptee'
        """, (id_offre,))
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            conn.close()
            return RedirectResponse(url='/admin?erreur=Offre%20introuvable%20ou%20d%C3%A9j%C3%A0%20trait%C3%A9e', status_code=302)
        id_joueur, id_club_acheteur, id_club_depart, type_mutation, montant_offre = row
        if id_club_depart == id_club_acheteur:
            conn.rollback()
            conn.close()
            return RedirectResponse(url='/admin?erreur=Dossier%20invalide', status_code=302)

        cursor.execute(
            "UPDATE JOUEUR SET id_club_actuel = %s WHERE id_joueur = %s",
            (id_club_acheteur, id_joueur)
        )
        cursor.execute(
            "UPDATE OFFRE SET statut = 'validee' WHERE id_offre = %s",
            (id_offre,)
        )
        cursor.execute("""
            UPDATE OFFRE SET statut = 'refusee'
            WHERE id_joueur = %s AND id_offre != %s AND statut = 'en_attente'
        """, (id_joueur, id_offre))
        montant_comm = int(round((montant_offre or 0) * 0.03))
        cursor.execute("""
            INSERT INTO mutation (
                date_demande, statut, type_mutation, montant_transfert, montant_commission,
                etat_paiement, id_joueur, id_club_depart, id_club_arrivee
            ) VALUES (
                CURRENT_DATE, 'validee', %s, %s, %s, 'en_attente', %s, %s, %s
            )
        """, (type_mutation, montant_offre or 0, montant_comm, id_joueur, id_club_depart, id_club_acheteur))
        conn.commit()
    except Exception as e:
        logging.exception('Validation transfert: %s', e)
        conn.rollback()
        conn.close()
        return RedirectResponse(
            url='/admin?erreur=Erreur%20lors%20de%20la%20validation%20%28sch%C3%A9ma%20BDD%29',
            status_code=302
        )
    conn.close()
    return RedirectResponse(url='/admin?succes=Transfert%20valid%C3%A9%20et%20enregistr%C3%A9', status_code=302)


@app.post('/admin/transfert/{id_offre}/refuser')
async def admin_refuser_transfert(request: Request, id_offre: int):
    if request.session.get('role') != 'ADMIN':
        return RedirectResponse(url='/login', status_code=302)
    conn = get_db_connection()
    if not conn:
        return RedirectResponse(url='/admin?erreur=Connexion%20base%20indisponible', status_code=302)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE OFFRE SET statut = 'refusee_ligue'
        WHERE id_offre = %s AND statut = 'acceptee'
    """, (id_offre,))
    if cursor.rowcount == 0:
        conn.rollback()
        conn.close()
        return RedirectResponse(url='/admin?erreur=Dossier%20introuvable', status_code=302)
    conn.commit()
    conn.close()
    return RedirectResponse(url='/admin?succes=Dossier%20refus%C3%A9', status_code=302)


@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login')