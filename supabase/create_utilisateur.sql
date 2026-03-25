-- À exécuter dans Supabase → SQL Editor
-- Crée la table manquante pour l’app (connexion club + admin).

CREATE TABLE IF NOT EXISTS public.utilisateur (
    id_user SERIAL PRIMARY KEY,
    login VARCHAR(255) UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role VARCHAR(50) NOT NULL,
    id_club INTEGER REFERENCES public.club (id_club) ON DELETE SET NULL,
    est_verifie BOOLEAN NOT NULL DEFAULT true,
    email TEXT
);

-- Compte admin (mot de passe : admin123)
INSERT INTO public.utilisateur (login, password, role, id_club, est_verifie)
VALUES (
    'admin',
    'scrypt:32768:8:1$cCPtkwyGOdhbBzac$f83c3a58a5b13f78d98b334d230c7b5686af67a51869fb17387ae589f24dd817a4fe15a88cb678e32999eda778b9ac5c52778f2ca5cde61eeb1e97f069ec4e5a',
    'ADMIN',
    NULL,
    true
)
ON CONFLICT (login) DO UPDATE SET
    password = EXCLUDED.password,
    role = EXCLUDED.role,
    est_verifie = true;

-- Si tu as déjà une table reset_token avec id_user en UUID, il faudra l’aligner sur id_user integer
-- ou recréer reset_token avec : id_user INTEGER REFERENCES public.utilisateur(id_user) ON DELETE CASCADE
