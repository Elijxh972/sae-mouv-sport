import psycopg2
import os
import logging

# Préférez toujours DATABASE_URL en variable d’environnement (Vercel / .env local).
# Ne commitez jamais de mot de passe réel dans le dépôt ; en cas de fuite, régénérez le mot de passe Supabase.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.uhrrdadeptehksxjneqf:x3jxhhDeqbhZVr3g@aws-1-us-east-1.pooler.supabase.com:6543/postgres",
)

# Fuseau Martinique par défaut (dates affichées / enregistrées comme sur l’île).
# Surcharge possible : PG_TZ=Europe/Paris
# Les requêtes sensibles dans app.py utilisent (now() AT TIME ZONE PG_TZ) pour rester correctes
# même avec le pooler Supabase (mode transaction) qui peut ignorer SET TIME ZONE.
PG_TZ = os.environ.get("PG_TZ", "America/Martinique")


def get_db_connection():
    """
    Établit une connexion sécurisée à la base de données Supabase.
    Ajout de connect_timeout pour éviter les blocages infinis sur Vercel.
    """
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE %s", (PG_TZ,))
        return conn
    except Exception as e:
        logging.error("Erreur de connexion à Supabase : %s", e)
        if conn:
            conn.close()
        return None