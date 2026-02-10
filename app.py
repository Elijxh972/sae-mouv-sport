from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from database import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime

app = FastAPI()

# 1. SÉCURITÉ & SESSION
app.add_middleware(SessionMiddleware, secret_key='MA_CLE_SECRETE_SAE')

# 2. FICHIERS STATIQUES
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. TEMPLATES HTML
templates = Jinja2Templates(directory="templates")

# Constante (0.3%)
POURCENTAGE_SOLIDARITE = 0.003  

# ==============================================================================
# ROUTE : ACCUEIL (Publique - 30 Derniers Jours)
# ==============================================================================
@app.get("/", response_class=HTMLResponse)
async def accueil(request: Request):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        
        # --- A. SPORTS (Sans doublons) ---
        cursor.execute('SELECT * FROM SPORT')
        tous_les_sports = cursor.fetchall()
        
        sports_uniques = []
        noms_vus = set()
        for sport in tous_les_sports:
            nom = sport[1]
            # On exclut explicitement le sport "Tennis" de la liste affichée
            if nom and nom.lower() == "tennis":
                continue
            if nom not in noms_vus:
                sports_uniques.append(sport)
                noms_vus.add(nom)
        
        cursor.execute('''
            SELECT j.nom, j.prenom, c1.nom_club, c2.nom_club
            FROM MUTATION m
            JOIN JOUEUR j ON m.id_joueur = j.id_joueur
            JOIN CLUB c1 ON m.id_club_depart = c1.id_club
            JOIN CLUB c2 ON m.id_club_arrivee = c2.id_club
            WHERE m.statut = 'Validé'
            AND m.date_demande >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY m.date_demande DESC
        ''')
        mutations = cursor.fetchall()
        conn.close()
        
        return templates.TemplateResponse('accueil.html', {
            'request': request, 
            'sports': sports_uniques, 
            'mutations': mutations
        })
        
    return HTMLResponse("Erreur de connexion à la Base de Données")


# ==============================================================================
# ROUTES : AUTHENTIFICATION
# ==============================================================================
@app.get('/login', response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse('login.html', {'request': request})

@app.post('/login')
async def login_post(request: Request, login: str = Form(...), password: str = Form(...)):
    """
    Connexion d'un utilisateur (admin, resp club, joueur).
    En cas d'erreur technique, on renvoie le message dans la réponse HTML
    pour éviter une erreur 500 opaque.
    """
    try:
        conn = get_db_connection()
        if not conn:
            return HTMLResponse("Erreur de connexion à la base de données. Merci de réessayer plus tard.")

        cursor = conn.cursor()
        # Compatibilité avec d'anciennes versions de la base où la colonne est_verifie
        # n'existe peut-être pas encore.
        try:
            cursor.execute(
                "SELECT id_user, password, role, id_club, est_verifie "
                "FROM UTILISATEUR WHERE login = %s",
                (login,)
            )
            user = cursor.fetchone()
            est_verifie_col_present = True
        except Exception:
            # La première requête a échoué (ex : colonne est_verifie inexistante) :
            # on annule la transaction en cours avant de relancer une requête.
            conn.rollback()
            # Fallback : on récupère sans est_verifie et on considère les comptes comme vérifiés
            cursor.execute(
                "SELECT id_user, password, role, id_club "
                "FROM UTILISATEUR WHERE login = %s",
                (login,)
            )
            row = cursor.fetchone()
            user = None
            est_verifie_col_present = False
            if row:
                # On reconstruit un tuple compatible avec le reste du code :
                # (id_user, password, role, id_club, est_verifie)
                user = (row[0], row[1], row[2], row[3], True)

        conn.close()
        
        if not user or not check_password_hash(user[1], password):
            return HTMLResponse("Identifiants incorrects <a href='/login'>Réessayer</a>")

        user_id, _, role, id_club, est_verifie = user

        # Si c'est un joueur non encore validé par son club
        # (seulement si la colonne existe réellement en base)
        if role == 'JOUEUR' and est_verifie_col_present and not est_verifie:
            return HTMLResponse("Ton compte joueur est en attente de validation par ton club. Merci de réessayer plus tard.")

        # Connexion OK : on enregistre en session
        request.session['user_id'] = user_id
        request.session['role'] = role
        request.session['id_club'] = id_club
        
        if role == 'ADMIN':
            return RedirectResponse(url='/admin', status_code=302)
        elif role == 'RESP_CLUB':
            return RedirectResponse(url='/club', status_code=302)
        else:
            # Joueur validé : on l'envoie vers son espace dédié
            return RedirectResponse(url='/joueur', status_code=302)

    except Exception as e:
        # Aide au debug : on renvoie l'erreur dans la page au lieu d'un 500 générique
        return HTMLResponse(f"Erreur technique lors de la connexion : {e}")

@app.get('/register', response_class=HTMLResponse)
async def register_get(request: Request):
    """
    Affiche le formulaire d'inscription.
    On charge la liste des clubs pour que le joueur puisse choisir le sien.
    """
    conn = get_db_connection()
    clubs = []
    if conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id_club, nom_club FROM CLUB ORDER BY nom_club")
            clubs = cursor.fetchall()
        except Exception as e:
            print(f"Erreur chargement clubs pour register : {e}")
        finally:
            conn.close()

    return templates.TemplateResponse('register.html', {'request': request, 'clubs': clubs})

@app.post('/register')
async def register_post(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    id_club: int = Form(...),
    sport: str = Form(None)
):
    """
    Création d'un compte :
    - RESP_CLUB : compte immédiatement actif et associé à un club.
    - JOUEUR : doit choisir son sport + son club, mais le compte reste en attente
      tant que le club ne l'a pas validé.
    """
    conn = get_db_connection()
    if not conn:
        return HTMLResponse("Erreur de connexion à la base. Merci de réessayer plus tard.")

    cursor = conn.cursor()

    # Vérifier l'unicité du login
    cursor.execute("SELECT id_user FROM UTILISATEUR WHERE login = %s", (login,))
    if cursor.fetchone():
        conn.close()
        return HTMLResponse("Login déjà utilisé. <a href='/register'>Retour</a>")

    # Nettoyage du rôle
    role = role.upper()
    if role not in ('RESP_CLUB', 'JOUEUR'):
        conn.close()
        return HTMLResponse("Rôle invalide. <a href='/register'>Retour</a>")

    # Application 100% football :
    # - pour un joueur, on force le sport à "Football"
    # - pour un responsable de club, on ignore le champ sport
    est_verifie = True
    if role == 'JOUEUR':
        sport = 'Football'
        est_verifie = False
    else:
        sport = None

    mdp_hash = generate_password_hash(password)

    # Insertion avec colonnes étendues
    cursor.execute(
        "INSERT INTO UTILISATEUR (login, password, role, id_club, sport, est_verifie) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (login, mdp_hash, role, id_club, sport, est_verifie)
    )
    conn.commit()
    conn.close()

    if role == 'JOUEUR':
        msg = "Compte créé ! Il doit maintenant être validé par votre club avant de pouvoir vous connecter."
        return HTMLResponse(msg + " <a href='/'>Retour à l'accueil</a>")
    else:
        return RedirectResponse(url='/login', status_code=302)

@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login', status_code=302)


# ==============================================================================
# ROUTE : TABLEAU DE BORD CLUB (Avec Filtres)
# ==============================================================================
@app.get('/club', response_class=HTMLResponse)
async def club_dashboard(request: Request, sport: str = None, niveau: str = None):
    if 'user_id' not in request.session or request.session['role'] != 'RESP_CLUB':
        return RedirectResponse(url='/login', status_code=302)
    
    mon_club_id = request.session['id_club']
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Infos Club
    cursor.execute("SELECT * FROM CLUB WHERE id_club = %s", (mon_club_id,))
    infos_club = cursor.fetchone()
    # Sécurisation : si aucun club trouvé pour cet id, on évite une erreur serveur
    if not infos_club:
        # On fabrique un tuple minimal compatible avec le template
        # indices utilisés : [1] = nom, [3] = score_confiance, [4] = statut_financier
        infos_club = (
            mon_club_id,          # id_club
            "Club inconnu",       # nom_club
            None,                 # champ intermédiaire (ex: ligue)
            0,                    # score_confiance
            "Non défini"          # statut_financier
        )

    # 2. Sports Disponibles (Gestion d'erreur si colonne manquante)
    try:
        cursor.execute("SELECT DISTINCT sport FROM JOUEUR WHERE id_club_actuel = %s", (mon_club_id,))
        sports_dispo = [row[0] for row in cursor.fetchall() if row[0]]
    except:
        sports_dispo = [] 

    # 3. Requête Filtrée pour l'effectif du club
    sql_joueurs = "SELECT * FROM JOUEUR WHERE id_club_actuel = %s"
    params = [mon_club_id]

    if sport and sport != "Tous":
        sql_joueurs += " AND sport = %s"
        params.append(sport)
        
    if niveau and niveau != "Tous":
        sql_joueurs += " AND niveau = %s"
        params.append(niveau)

    try:
        cursor.execute(sql_joueurs, tuple(params))
        mes_joueurs = cursor.fetchall()
    except:
        # Si ça plante (ex: colonne niveau manque), on renvoie tout sans filtre
        cursor.execute("SELECT * FROM JOUEUR WHERE id_club_actuel = %s", (mon_club_id,))
        mes_joueurs = cursor.fetchall()

    # 4. Joueurs en attente de validation pour ce club
    try:
        cursor.execute(
            "SELECT id_user, login, sport FROM UTILISATEUR "
            "WHERE role = 'JOUEUR' AND id_club = %s AND (est_verifie = FALSE OR est_verifie IS NULL)",
            (mon_club_id,)
        )
        joueurs_en_attente = cursor.fetchall()
    except Exception as e:
        print(f"Erreur chargement joueurs en attente : {e}")
        joueurs_en_attente = []
    
    conn.close()
    
    return templates.TemplateResponse('club_dashboard.html', {
        'request': request, 
        'club': infos_club, 
        'joueurs': mes_joueurs,
        'sports_dispo': sports_dispo,
        'filtre_sport': sport,
        'filtre_niveau': niveau,
        'joueurs_en_attente': joueurs_en_attente
    })


@app.get('/club/validate_player/{id_user}/{action}')
async def validate_player(id_user: int, action: str, request: Request):
    """
    Permet à un responsable de club de confirmer ou refuser
    qu'un compte JOUEUR lui soit rattaché.
    """
    if 'user_id' not in request.session or request.session['role'] != 'RESP_CLUB':
        return RedirectResponse(url='/login', status_code=302)

    conn = get_db_connection()
    if not conn:
        return HTMLResponse("Erreur de connexion à la base.")

    cursor = conn.cursor()
    try:
        if action == 'valider':
            cursor.execute(
                "UPDATE UTILISATEUR SET est_verifie = TRUE "
                "WHERE id_user = %s AND role = 'JOUEUR'",
                (id_user,)
            )
        elif action == 'refuser':
            # On supprime simplement le compte joueur non validé
            cursor.execute(
                "DELETE FROM UTILISATEUR WHERE id_user = %s AND role = 'JOUEUR'",
                (id_user,)
            )
        conn.commit()
    except Exception as e:
        print(f"Erreur validation joueur : {e}")
        conn.rollback()
    finally:
        conn.close()

    return RedirectResponse(url='/club', status_code=302)


# ==============================================================================
# ROUTE : MERCATO
# ==============================================================================
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


# ==============================================================================
# ROUTES : FAIRE UNE OFFRE
# ==============================================================================
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
    res = cursor.fetchone()
    if not res:
        return HTMLResponse("Erreur : Joueur introuvable")
        
    id_club_vendeur = res[0]
    id_club_acheteur = request.session['id_club']

    sql = """INSERT INTO MUTATION 
             (date_demande, type_mutation, montant_transfert, montant_commission, id_joueur, id_club_depart, id_club_arrivee, statut)
             VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, 'En attente')"""
    
    cursor.execute(sql, (type_mutation, montant, commission, id_joueur, id_club_vendeur, id_club_acheteur))
    conn.commit()
    conn.close()
    return RedirectResponse(url='/club', status_code=302)


# ==============================================================================
# ROUTES : ADMINISTRATION
# ==============================================================================
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

@app.get('/admin/action/{id_mutation}/{action}')
async def traiter_mutation(id_mutation: int, action: str, request: Request):
    if 'user_id' not in request.session or request.session['role'] != 'ADMIN':
        return RedirectResponse(url='/login', status_code=302)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id_club_arrivee, id_joueur FROM MUTATION WHERE id_mutation = %s", (id_mutation,))
        infos = cursor.fetchone()
        
        if infos:
            id_club_acheteur = infos[0]
            id_joueur = infos[1]

            if action == 'valider':
                cursor.execute("UPDATE MUTATION SET statut = 'Validé' WHERE id_mutation = %s", (id_mutation,))
                cursor.execute("UPDATE JOUEUR SET id_club_actuel = %s WHERE id_joueur = %s", (id_club_acheteur, id_joueur))
                cursor.execute("UPDATE CLUB SET score_confiance = score_confiance + 5 WHERE id_club = %s", (id_club_acheteur,))
                
            elif action == 'refuser':
                cursor.execute("UPDATE MUTATION SET statut = 'Refusé' WHERE id_mutation = %s", (id_mutation,))
                cursor.execute("UPDATE CLUB SET score_confiance = score_confiance - 10 WHERE id_club = %s", (id_club_acheteur,))
            
            conn.commit()
    except Exception as e:
        print(f"Erreur SQL : {e}")
        conn.rollback()
    finally:
        conn.close()
    
    return RedirectResponse(url='/admin', status_code=302)


# ==============================================================================
# ROUTE DE SECOURS : MISE A JOUR BASE DE DONNEES
# ==============================================================================
@app.get('/update_db')
async def update_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Ajout / mise à niveau des colonnes JOUEUR
        try: cursor.execute("ALTER TABLE JOUEUR ADD COLUMN sport TEXT DEFAULT 'Football'")
        except: pass
        try: cursor.execute("ALTER TABLE JOUEUR ADD COLUMN niveau TEXT DEFAULT 'R1'")
        except: pass 
        try: cursor.execute("ALTER TABLE JOUEUR ADD COLUMN fairplay INTEGER DEFAULT 5")
        except: pass

        # Ajout / mise à niveau des colonnes UTILISATEUR
        try: cursor.execute("ALTER TABLE UTILISATEUR ADD COLUMN sport TEXT")
        except: pass
        try: cursor.execute("ALTER TABLE UTILISATEUR ADD COLUMN est_verifie BOOLEAN DEFAULT TRUE")
        except: pass

        conn.commit()
        return "Base de données mise à jour avec succès ! Tu peux retourner sur /club"
    except Exception as e:
        return f"Erreur lors de la mise à jour : {e}"
    finally:
        conn.close()

# ROUTE UTILITAIRE (Reset Users)
@app.get('/init_users')
async def init_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM UTILISATEUR")
    mdp_admin = generate_password_hash("admin123")
    cursor.execute("INSERT INTO UTILISATEUR (login, password, role) VALUES ('admin', %s, 'ADMIN')", (mdp_admin,))
    mdp_club = generate_password_hash("club123")
    cursor.execute("INSERT INTO UTILISATEUR (login, password, role, id_club) VALUES ('golden', %s, 'RESP_CLUB', 1)", (mdp_club,))
    conn.commit()
    conn.close()
    return "Utilisateurs réinitialisés"


# ROUTE UTILITAIRE (Insertion des clubs de Régional 2)
@app.get('/init_clubs_r2')
async def init_clubs_r2():
    """
    Insère en base tous les clubs de Régional 2 (sans notion de poule).
    On se contente pour l'instant de renseigner le nom du club ; les autres
    colonnes de CLUB utilisent leurs valeurs par défaut éventuelles.
    """
    clubs_r2 = [
        # Ex-Poule A
        "RC Rivière-Pilote",
        "CS Vauclinois",
        "New Club (Petit-Bourg)",
        "US Diamantinoise",
        "SC Lamentinois",
        "Éveil (Trois-Ilets)",
        "US Marinoise",
        "Stade Spiritain",
        "Éclair (Rivière-Salée)",
        "Olympique (Marin)",
        "JS Eucalyptus (François)",
        "FAM (Lamentin)",
        # Ex-Poule B
        "US Riveraine (Grand-Rivière)",
        "Effort (Morne-Vert)",
        "Rapid Club (Lorient)",
        "Essor Préchotin",
        "Réveil Sportif (Gros-Morne)",
        "CS Case-Pilote",
        "Réal Tartane",
        "Étendard (Bellefontaine)",
        "UJ Monnerot",
        "Assaut (Saint-Pierre)",
        "Océanic (Lorrain)",
        "AC Vert-Pré",
    ]

    conn = get_db_connection()
    if not conn:
        return "Erreur de connexion à la base."

    cursor = conn.cursor()
    inserted = 0

    for nom in clubs_r2:
        try:
            # On insère uniquement le nom ; les autres colonnes prennent leurs valeurs par défaut
            cursor.execute(
                "INSERT INTO CLUB (nom_club) VALUES (%s)",
                (nom,)
            )
            inserted += 1
        except Exception as e:
            # En cas de doublon ou autre erreur sur un club, on ignore et on continue
            print(f"Erreur insertion club '{nom}': {e}")
            conn.rollback()
        else:
            conn.commit()

    conn.close()
    return f"Clubs de Régional 2 insérés (ou déjà existants). Nouveaux enregistrements : {inserted}"


# ROUTE UTILITAIRE (Insertion de tous les clubs R1, R2, R3)
@app.get('/init_clubs_all')
async def init_clubs_all():
    """
    Insère en base tous les clubs listés :
    - Régional 1
    - Régional 2 (tous ensemble, sans poules)
    - Régional 3 (liste fournie)
    """
    clubs_r1 = [
        "RC Saint-Joseph",
        "Club Franciscain",
        "Club Colonial",
        "Golden Lion",
        "AS Samaritaine",
        "Club Péléen",
        "AS Morne-des-Esses",
        "Inter de Sainte-Anne",
        "CO Trénelle",
        "Espoir (Sainte-Luce)",
        "Golden Star",
        "US Robert",
        "Émulation",
        "Aiglon",
    ]

    clubs_r2 = [
        # Ex-Poule A
        "RC Rivière-Pilote",
        "CS Vauclinois",
        "New Club (Petit-Bourg)",
        "US Diamantinoise",
        "SC Lamentinois",
        "Éveil (Trois-Ilets)",
        "US Marinoise",
        "Stade Spiritain",
        "Éclair (Rivière-Salée)",
        "Olympique (Marin)",
        "JS Eucalyptus (François)",
        "FAM (Lamentin)",
        # Ex-Poule B
        "US Riveraine (Grand-Rivière)",
        "Effort (Morne-Vert)",
        "Rapid Club (Lorient)",
        "Essor Préchotin",
        "Réveil Sportif (Gros-Morne)",
        "CS Case-Pilote",
        "Réal Tartane",
        "Étendard (Bellefontaine)",
        "UJ Monnerot",
        "Assaut (Saint-Pierre)",
        "Océanic (Lorrain)",
        "AC Vert-Pré",
    ]

    clubs_r3 = [
        "La Gauloise (Trinité)",
        "RC Bokannal",
        "ASCEF",
        "Good Luck",
        "Étendard",
        "JS Marigot",
        "Silver Star",
        "UJ Redoute",
        "Solidarité",
        "Gri-Gri Pilotin",
        "Anses-d’Arlet FC",
        "CS Belimois",
        "FEP Monésie",
        "ASPTT",
    ]

    tous_les_clubs = clubs_r1 + clubs_r2 + clubs_r3

    conn = get_db_connection()
    if not conn:
        return "Erreur de connexion à la base."

    cursor = conn.cursor()
    inserted = 0

    for nom in tous_les_clubs:
        try:
            cursor.execute(
                "INSERT INTO CLUB (nom_club) VALUES (%s)",
                (nom,)
            )
            inserted += 1
            conn.commit()
        except Exception as e:
            # Probable doublon : on annule et on continue
            print(f"Erreur insertion club '{nom}': {e}")
            conn.rollback()

    conn.close()
    return f"Clubs R1/R2/R3 insérés (ou déjà existants). Nouveaux enregistrements : {inserted}"


# ==============================================================================
# ESPACE JOUEUR
# ==============================================================================
@app.get('/joueur', response_class=HTMLResponse)
async def joueur_dashboard(request: Request):
    """
    Tableau de bord simple pour un joueur :
    - vérifie qu'il est connecté et que son rôle est JOUEUR
    - affiche ses infos de base et le club auquel il est rattaché
    """
    if 'user_id' not in request.session or request.session.get('role') != 'JOUEUR':
        return RedirectResponse(url='/login', status_code=302)

    user_id = request.session['user_id']

    conn = get_db_connection()
    if not conn:
        return HTMLResponse("Erreur de connexion à la base de données.")

    cursor = conn.cursor()
    # On récupère les infos de l'utilisateur et de son club (si renseigné)
    cursor.execute(
        """
        SELECT u.login, u.sport, c.nom_club
        FROM UTILISATEUR u
        LEFT JOIN CLUB c ON u.id_club = c.id_club
        WHERE u.id_user = %s
        """,
        (user_id,)
    )
    infos = cursor.fetchone()
    conn.close()

    if not infos:
        return HTMLResponse("Compte introuvable. Merci de vous reconnecter. <a href='/login'>Connexion</a>")

    login, sport, nom_club = infos

    return templates.TemplateResponse(
        'joueur_dashboard.html',
        {
            'request': request,
            'login': login,
            'sport': sport or 'Football',
            'nom_club': nom_club or "Aucun club rattaché pour le moment"
        }
    )

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=True)