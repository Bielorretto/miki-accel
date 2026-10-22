-- Schéma MIKI. Données 100 % synthétiques.
-- PostgreSQL garde ce qui doit durer ; l'état en direct (sessions, files, dispo) vit dans Valkey.

-- ---------------------------------------------------------------------------
-- Référentiel : configuration des 115
-- ---------------------------------------------------------------------------

CREATE TABLE departements (
    code        text PRIMARY KEY,                -- '93', '2A', '974'
    nom         text NOT NULL,
    siao        text NOT NULL,                   -- nom du SIAO qui gère le 115
    couvert     boolean NOT NULL DEFAULT true,   -- false : MIKI le dit et oriente
    orientation text                             -- ce que MIKI dit si non couvert
);

-- Correspondance commune → code postal → département.
-- Une commune peut avoir plusieurs codes postaux, et deux communes le même nom.
CREATE TABLE communes (
    code_insee  text NOT NULL,
    code_postal text NOT NULL,
    nom         text NOT NULL,
    departement text NOT NULL REFERENCES departements (code),
    PRIMARY KEY (code_insee, code_postal)
);
CREATE INDEX communes_nom ON communes (lower(nom));

CREATE TABLE profils (
    code    text PRIMARY KEY,
    libelle text NOT NULL
);

-- Liste fermée des causes de mise à la rue (statistiques).
CREATE TABLE causes (
    code    text PRIMARY KEY,
    libelle text NOT NULL
);

-- Règles de priorité propres à chaque 115, versionnées : une décision cite la version appliquée.
CREATE TABLE regles_departement (
    departement       text NOT NULL REFERENCES departements (code),
    version           int  NOT NULL,
    ordre_profils     text[] NOT NULL,   -- du plus prioritaire au moins prioritaire
    criteres_securite text[] NOT NULL,   -- critères qui passent devant tout (configurable)
    active            boolean NOT NULL DEFAULT true,
    creee_le          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (departement, version)
);
CREATE UNIQUE INDEX une_regle_active ON regles_departement (departement) WHERE active;

CREATE TABLE places (
    departement text NOT NULL REFERENCES departements (code),
    profil      text NOT NULL REFERENCES profils (code),
    disponibles int  NOT NULL CHECK (disponibles >= 0),
    total       int  NOT NULL CHECK (total >= disponibles),
    maj         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (departement, profil)
);

-- Les écoutants humains du 115 (à ne pas confondre avec l'agent MIKI).
CREATE TABLE ecoutants (
    id          serial PRIMARY KEY,
    prenom      text NOT NULL,
    departement text NOT NULL REFERENCES departements (code),
    langues     text[] NOT NULL,          -- codes ISO 639-1 : 'fr', 'en', 'ar'…
    actif       boolean NOT NULL DEFAULT true
);

-- ---------------------------------------------------------------------------
-- Personnes et appels
-- ---------------------------------------------------------------------------

-- Le numéro n'est jamais stocké : seulement son HMAC (secret en variable d'environnement).
-- tel_hash n'est pas unique : un téléphone peut être prêté ou partagé.
CREATE TABLE appelants (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tel_hash       bytea,
    cle_version    smallint NOT NULL DEFAULT 1,   -- version du secret HMAC
    prenom         text,
    nom            text,
    age            int CHECK (age BETWEEN 0 AND 120),
    age_declare_le date,                          -- pour comparer l'âge d'un appel à l'autre
    sexe           text CHECK (sexe IN ('homme', 'femme', 'autre')),
    cree_le        timestamptz NOT NULL DEFAULT now(),
    maj            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX appelants_tel_hash ON appelants (tel_hash);

-- Quand une info change, l'ancienne valeur est gardée ici ; appelants porte la plus récente.
CREATE TABLE appelant_historique (
    id              bigserial PRIMARY KEY,
    appelant_id     uuid NOT NULL REFERENCES appelants (id) ON DELETE CASCADE,
    champ           text NOT NULL,
    ancienne_valeur text,
    nouvelle_valeur text,
    appel_id        uuid,
    change_le       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE appels (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    appelant_id     uuid REFERENCES appelants (id),   -- vide tant que non reconnu
    tel_hash        bytea,
    debut           timestamptz NOT NULL DEFAULT now(),
    fin             timestamptz,
    departement     text REFERENCES departements (code),
    langue          text,
    langue_couverte boolean,
    ecoutant_id     int REFERENCES ecoutants (id),
    statut          text NOT NULL DEFAULT 'en_cours'
                    CHECK (statut IN ('en_cours', 'termine', 'abandonne')),
    issue           text CHECK (issue IN ('place_attribuee', 'en_attente', 'aucune_place_orientation',
                                          'orientation_urgence', 'hors_zone', 'abandon')),
    source          text NOT NULL DEFAULT 'direct' CHECK (source IN ('direct', 'simule'))
);
CREATE INDEX appels_en_cours ON appels (departement) WHERE statut = 'en_cours';

CREATE TABLE fiches (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    appel_id       uuid NOT NULL UNIQUE REFERENCES appels (id) ON DELETE CASCADE,
    appelant_id    uuid REFERENCES appelants (id),
    departement    text REFERENCES departements (code),
    localisation   text,
    profil         text REFERENCES profils (code),
    criteres       jsonb NOT NULL DEFAULT '{}',
    cause          text REFERENCES causes (code),
    niveau_urgence smallint CHECK (niveau_urgence BETWEEN 1 AND 5),  -- grille 1 à 5 de l'agent
    securite       boolean NOT NULL DEFAULT false,
    aucune_place   boolean NOT NULL DEFAULT false,   -- « aucune place, orientation alternative »
    rang           int,                              -- rang dans la file du département
    en_file        boolean NOT NULL DEFAULT true,    -- false quand un écoutant a traité la fiche
    complet        boolean NOT NULL DEFAULT false,
    contenu        jsonb NOT NULL DEFAULT '{}',      -- fiche complète produite par l'agent
    transcription  text,
    creee_le       timestamptz NOT NULL DEFAULT now(),
    maj            timestamptz NOT NULL DEFAULT now(),
    delai_fiche_s  int                               -- du décroché à la fiche complète
);
CREATE INDEX fiches_file ON fiches (departement, rang) WHERE en_file;

-- Explicabilité : chaque décision de classement, avec la règle appliquée.
CREATE TABLE decisions_priorite (
    id             bigserial PRIMARY KEY,
    fiche_id       uuid NOT NULL REFERENCES fiches (id) ON DELETE CASCADE,
    departement    text NOT NULL,
    regle_version  int  NOT NULL,
    profil         text,
    ordre_effectif text[] NOT NULL,
    profils_sautes text[] NOT NULL,
    rang           int  NOT NULL,
    securite       boolean NOT NULL,
    aucune_place   boolean NOT NULL,
    explication    text NOT NULL,
    decidee_le     timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (departement, regle_version) REFERENCES regles_departement (departement, version)
);
CREATE INDEX decisions_fiche ON decisions_priorite (fiche_id, decidee_le DESC);

-- ---------------------------------------------------------------------------
-- Statistiques anonymes
-- ---------------------------------------------------------------------------

-- Aucun nom, aucun numéro, aucune clé vers appels ou appelants, pas d'heure.
-- L'id est aléatoire pour que l'ordre d'insertion ne trahisse pas l'ordre des appels.
CREATE TABLE stats_calls (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    jour            date NOT NULL,
    departement     text NOT NULL REFERENCES departements (code),
    tranche_age     text NOT NULL CHECK (tranche_age IN ('moins_18', '18_24', '25_34', '35_49',
                                                         '50_64', '65_plus', 'inconnu')),
    sexe            text NOT NULL CHECK (sexe IN ('homme', 'femme', 'autre', 'inconnu')),
    cause           text REFERENCES causes (code),
    profil          text REFERENCES profils (code),
    langue          text,
    langue_couverte boolean,
    niveau_urgence  smallint,
    issue           text,
    delai_fiche_s   int,
    source          text NOT NULL CHECK (source IN ('direct', 'simule'))
);
CREATE INDEX stats_jour ON stats_calls (jour, departement);
