import psycopg2
import os

# --- CONFIGURATION SUPABASE ---
# Colle ton lien URI ici entre les guillemets.
# Format : postgresql://user:password@host:port/database
# Exemple : "postgresql://postgres.xzy:MonSuperMotDePasse@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

DATABASE_URL = os.environ.get('DATABASE_URL', "postgresql://postgres.uhrrdadeptehksxjneqf:x3jxhhDeqbhZVr3g@aws-1-us-east-1.pooler.supabase.com:6543/postgres")

def get_db_connection():
    """
    Établit une connexion sécurisée à la base de données Supabase (PostgreSQL).
    """
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Erreur de connexion à Supabase : {e}")
        return None