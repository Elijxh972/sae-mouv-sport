import psycopg2
import os

# Préférez toujours DATABASE_URL en variable d’environnement (Vercel / .env local).
# Ne commitez jamais de mot de passe réel dans le dépôt ; en cas de fuite, régénérez le mot de passe Supabase.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.uhrrdadeptehksxjneqf:x3jxhhDeqbhZVr3g@aws-1-us-east-1.pooler.supabase.com:6543/postgres",
)

# Fuseau pour CURRENT_DATE / heures SQL (évite d’avoir « demain » en UTC alors qu’on est encore le jour J localement).
# Ex. variable d’environnement : PG_TZ=Europe/Paris
PG_TZ = os.environ.get("PG_TZ", "America/Martinique")


def get_db_connection():
    """
    Établit une connexion sécurisée à la base de données Supabase.
    Ajout de connect_timeout pour éviter les blocages infinis sur Vercel.
    """
    try:
        # On ajoute un timeout de 5 secondes pour ne pas laisser Vercel dans le vide
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE %s", (PG_TZ,))
        return conn
    except Exception as e:
        # Important : Vercel affichera cela dans l'onglet "Logs"
        print(f"Erreur de connexion à Supabase : {e}")
        return None