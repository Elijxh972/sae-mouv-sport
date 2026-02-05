from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from database import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash
import os

app = FastAPI()

# Configuration de la session (Indispensable pour le login)
app.add_middleware(SessionMiddleware, secret_key='MA_CLE_SECRETE_SAE')

# Configuration des fichiers statiques (CSS, Images)
# On vérifie si le dossier existe pour éviter les erreurs
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration des templates HTML
templates = Jinja2Templates(directory="templates")

POURCENTAGE_SOLIDARITE = 0.003  

# ==============================================================================
# --- ROUTE ACCUEIL (CORRIGÉE : SUPPRESSION DES DOUBLONS DE SPORTS) ---
# ==============================================================================
@app.get("/", response_class=HTMLResponse)
async def accueil(request: Request):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        
        # 1. On récupère TOUS les sports
        cursor.execute('SELECT * FROM SPORT')
        tous_les_sports = cursor.fetchall()
        
        # --- FILTRAGE PYTHON : ON RETIRE LES DOUBLONS ---
        # On crée une liste propre sans doublons de noms
        sports_uniques = []
        noms_vus = set()
        
        for sport in tous_les_sports:
            # On suppose que la colonne 1 est le nom du sport (id, nom)
            nom = sport[1] 
            if nom not in noms_vus:
                sports_uniques.append(sport)
                noms_vus.add(nom)
        # ------------------------------------------------

        # 2. On récupère les dernières mutations validées pour l'affichage public
        cursor.execute('''
            SELECT m.id_mutation, j.nom, c1.nom_club, c2.nom_club, m.montant_commission 
            FROM MUTATION m
            JOIN JOUEUR j ON m.id_joueur = j.id_joueur
            JOIN CLUB c1 ON m.id_club_depart = c1.id_club
            JOIN CLUB c2 ON m.id_club_arrivee = c2.id_club
            WHERE m.statut = 'Validé'
            LIMIT 5
        ''')
        mutations = cursor.fetchall()
        conn.close()
        
        # IMPORTANT : On envoie 'sports_uniques' au template
        return templates.TemplateResponse('accueil.html', {
            'request': request, 
            'sports': sports_uniques, 
            'mutations': mutations
        })
        
    return HTMLResponse("Erreur de connexion à la Base de Données")


# ==============================================================================
# --- AUTRES ROUTES (INCHANGÉES) ---
# ==============================================================================

# --- ROUTE UTILITAIRE (Création des comptes) ---
@app.get('/init_users')
async def init_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # On vide la table pour éviter les doublons
    cursor.execute("DELETE FROM UTILISATEUR")
    
    # Création Admin
    mdp_admin = generate_password_hash("admin123")
    cursor.execute("INSERT INTO UTILISATEUR (login, password, role) VALUES ('admin', %s, 'ADMIN')", (mdp_admin,))
    
    # Création Club Golden Star (ID 1)
    mdp_club = generate_password_hash("club123")
    cursor.execute("INSERT INTO UTILISATEUR (login, password, role, id_club) VALUES ('golden', %s, 'RESP_CLUB', 1)", (mdp_club,))
    
    conn.commit()
    conn.close()
    return "Utilisateurs créés ! Login: admin/admin123 et golden/club123"

# --- ROUTE LOGIN ---
@app.get('/login', response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse('login.html', {'request': request})

@app.post('/login')
async def login_post(request: Request, login: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_user, password, role, id_club FROM UTILISATEUR WHERE login = %s", (login,))
    user = cursor.fetchone()
    conn.close()
    
    if user and check_password_hash(user[1], password):
        request.session['user_id'] = user[0]
        request.session['role'] = user[2]
        request.session['id_club'] = user[3]
        
        if user[2] == 'ADMIN':
            return RedirectResponse(url='/admin', status_code=302)
        elif user[2] == 'RESP_CLUB':
            return RedirectResponse(url='/club', status_code=302)
        else:
            return RedirectResponse(url='/', status_code=302)
    else:
        return HTMLResponse("Identifiants incorrects")

# --- ROUTE REGISTER ---
@app.get('/register', response_class=HTMLResponse)
async def register_get(request: Request):
    return templates.TemplateResponse('register.html', {'request': request})

@app.post('/register')
async def register_post(request: Request, login: str = Form(...), password: str = Form(...), role: str = Form(...), id_club: int = Form(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if login exists
    cursor.execute("SELECT id_user FROM UTILISATEUR WHERE login = %s", (login,))
    if cursor.fetchone():
        conn.close()
        return HTMLResponse("Login déjà utilisé")
    
    mdp_hash = generate_password_hash(password)
    cursor.execute("INSERT INTO UTILISATEUR (login, password, role, id_club) VALUES (%s, %s, %s, %s)", (login, mdp_hash, role, id_club))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/login', status_code=302)

# --- ROUTE LOGOUT ---
@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login', status_code=302)

# --- TABLEAU DE BORD CLUB ---
@app.get('/club', response_class=HTMLResponse)
async def club_dashboard(request: Request):
    if 'user_id' not in request.session or request.session['role'] != 'RESP_CLUB':
        return RedirectResponse(url='/login', status_code=302)
    
    mon_club_id = request.session['id_club']
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Infos du club
    cursor.execute("SELECT * FROM CLUB WHERE id_club = %s", (mon_club_id,))
    infos_club = cursor.fetchone()
    
    # Mes joueurs actuels
    cursor.execute("SELECT * FROM JOUEUR WHERE id_club_actuel = %s", (mon_club_id,))
    mes_joueurs = cursor.fetchall()
    
    conn.close()
    return templates.TemplateResponse('club_dashboard.html', {'request': request, 'club': infos_club, 'joueurs': mes_joueurs})

# --- ROUTE ADMIN (Vue) ---
@app.get('/admin', response_class=HTMLResponse)
async def admin_panel(request: Request):
    if 'user_id' not in request.session or request.session['role'] != 'ADMIN':
        return RedirectResponse(url='/login', status_code=302)
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    sql = '''
        SELECT m.id_mutation, j.nom, j.prenom, c1.nom_club, c2.nom_club, m.montant_transfert, m.montant_commission, m.type_mutation
        FROM MUTATION m
        JOIN JOUEUR j ON m.id_joueur = j.id_joueur
        JOIN CLUB c1 ON m.id_club_depart = c1.id_club
        JOIN CLUB c2 ON m.id_club_arrivee = c2.id_club
        WHERE m.statut = 'En attente'
    '''
    cursor.execute(sql)
    demandes = cursor.fetchall()
    conn.close()
    return templates.TemplateResponse('admin.html', {'request': request, 'demandes': demandes})

# --- ROUTE ADMIN AJOUTER UTILISATEUR ---
@app.post('/admin/add_user')
async def add_user(request: Request, login: str = Form(...), password: str = Form(...), role: str = Form(...), id_club: int = Form(None)):
    if 'user_id' not in request.session or request.session['role'] != 'ADMIN':
        return RedirectResponse(url='/login', status_code=302)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    mdp_hash = generate_password_hash(password)
    if role == 'RESP_CLUB' and id_club:
        cursor.execute("INSERT INTO UTILISATEUR (login, password, role, id_club) VALUES (%s, %s, %s, %s)", (login, mdp_hash, role, id_club))
    else:
        cursor.execute("INSERT INTO UTILISATEUR (login, password, role) VALUES (%s, %s, %s)", (login, mdp_hash, role))
    
    conn.commit()
    conn.close()
    return RedirectResponse(url='/admin', status_code=302)

# --- ROUTE ADMIN ACTION (Validation/Refus) ---
@app.get('/admin/action/{id_mutation}/{action}')
async def traiter_mutation(id_mutation: int, action: str, request: Request):
    print(f"--- 🔍 DÉBUT DEBUG : Mutation {id_mutation} | Action : {action} ---")

    if 'user_id' not in request.session or request.session['role'] != 'ADMIN':
        print("⛔ Erreur : Utilisateur non connecté ou pas Admin")
        return RedirectResponse(url='/login', status_code=302)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Récupération infos
        print("⏳ Récupération des infos...")
        cursor.execute("SELECT id_club_arrivee, id_joueur FROM MUTATION WHERE id_mutation = %s", (id_mutation,))
        infos = cursor.fetchone()
        
        if not infos:
            print("❌ ERREUR : Aucune mutation trouvée avec cet ID !")
            return HTMLResponse("Erreur : Mutation introuvable")

        id_club_acheteur = infos[0]
        id_joueur = infos[1]
        print(f"✅ Infos trouvées : Club Acheteur ID={id_club_acheteur}, Joueur ID={id_joueur}")

        if action == 'valider':
            print("🚀 Action VALIDER détectée")
            # 1. Validation statut
            cursor.execute("UPDATE MUTATION SET statut = 'Validé' WHERE id_mutation = %s", (id_mutation,))
            # 2. Déplacement joueur
            cursor.execute("UPDATE JOUEUR SET id_club_actuel = %s WHERE id_joueur = %s", (id_club_acheteur, id_joueur))
            # 3. Bonus Confiance
            cursor.execute("UPDATE CLUB SET score_confiance = score_confiance + 5 WHERE id_club = %s", (id_club_acheteur,))
            
        elif action == 'refuser':
            print("🗑️ Action REFUSER détectée")
            cursor.execute("UPDATE MUTATION SET statut = 'Refusé' WHERE id_mutation = %s", (id_mutation,))
            cursor.execute("UPDATE CLUB SET score_confiance = score_confiance - 10 WHERE id_club = %s", (id_club_acheteur,))
        
        conn.commit()
        print("💾 COMMIT effectué avec succès !")

    except Exception as e:
        print(f"❌ GROSSE ERREUR SQL : {e}")
        conn.rollback()
    
    finally:
        conn.close()
        print("--- FIN DEBUG ---")
    
    return RedirectResponse(url='/admin', status_code=302)

# --- ROUTE MERCATO ---
@app.get('/mercato', response_class=HTMLResponse)
async def mercato(request: Request):
    if 'user_id' not in request.session or request.session['role'] != 'RESP_CLUB':
        return RedirectResponse(url='/login', status_code=302)
    
    mon_club_id = request.session['id_club']
    conn = get_db_connection()
    cursor = conn.cursor()
    
    sql = """
        SELECT j.id_joueur, j.nom, j.prenom, j.poste, c.nom_club 
        FROM JOUEUR j
        JOIN CLUB c ON j.id_club_actuel = c.id_club
        WHERE j.id_club_actuel != %s
        ORDER BY j.nom
    """
    cursor.execute(sql, (mon_club_id,))
    joueurs_dispo = cursor.fetchall()
    conn.close()
    return templates.TemplateResponse('mercato.html', {'request': request, 'joueurs': joueurs_dispo})

# --- ROUTE FAIRE OFFRE ---
@app.get('/faire_offre/{id_joueur}', response_class=HTMLResponse)
async def faire_offre_get(id_joueur: int, request: Request):
    if 'user_id' not in request.session or request.session['role'] != 'RESP_CLUB':
        return RedirectResponse(url='/login', status_code=302)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT nom, prenom FROM JOUEUR WHERE id_joueur = %s", (id_joueur,))
    joueur = cursor.fetchone()
    conn.close()
    return templates.TemplateResponse('faire_offre.html', {'request': request, 'joueur': joueur})

@app.post('/faire_offre/{id_joueur}')
async def faire_offre_post(id_joueur: int, request: Request, montant: float = Form(...), type_mutation: str = Form(...)):
    if 'user_id' not in request.session or request.session['role'] != 'RESP_CLUB':
        return RedirectResponse(url='/login', status_code=302)

    conn = get_db_connection()
    cursor = conn.cursor()

    commission = montant * POURCENTAGE_SOLIDARITE
    
    cursor.execute("SELECT id_club_actuel FROM JOUEUR WHERE id_joueur = %s", (id_joueur,))
    id_club_vendeur = cursor.fetchone()[0]
    id_club_acheteur = request.session['id_club']

    sql = """INSERT INTO MUTATION 
             (date_demande, type_mutation, montant_transfert, montant_commission, id_joueur, id_club_depart, id_club_arrivee)
             VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s)"""
    
    cursor.execute(sql, (type_mutation, montant, commission, id_joueur, id_club_vendeur, id_club_acheteur))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/club', status_code=302)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=True)