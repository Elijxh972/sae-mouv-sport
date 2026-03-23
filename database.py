import psycopg2
import os

# On récupère l'URL depuis les variables d'environnement Vercel (recommandé)
# Sinon, on utilise ton URL Supabase par défaut.
DATABASE_URL = os.environ.get('DATABASE_URL', "postgresql://postgres.uhrrdadeptehksxjneqf:x3jxhhDeqbhZVr3g@aws-1-us-east-1.pooler.supabase.com:6543/postgres")

def get_db_connection():
    """
    Établit une connexion sécurisée à la base de données Supabase.
    Ajout de connect_timeout pour éviter les blocages infinis sur Vercel.
    """
    try:
        # On ajoute un timeout de 5 secondes pour ne pas laisser Vercel dans le vide
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        return conn
    except Exception as e:
        # Important : Vercel affichera cela dans l'onglet "Logs"
        print(f"Erreur de connexion à Supabase : {e}")
        return None