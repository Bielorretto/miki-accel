-- Référentiel synthétique : départements, communes, profils, causes, règles, places, écoutants.
-- Les places, règles et écoutants sont fictifs. Les communes sont un extrait :
-- en production, charger la base officielle des codes postaux (La Poste / INSEE).

INSERT INTO departements (code, nom, siao, couvert, orientation) VALUES
    ('75',  'Paris',              'SIAO Paris',               true,  NULL),
    ('93',  'Seine-Saint-Denis',  'SIAO 93 (Interlogement)',  true,  NULL),
    ('13',  'Bouches-du-Rhône',   'SIAO 13',                  true,  NULL),
    ('69',  'Rhône',              'Maison de la Veille Sociale', true, NULL),
    ('59',  'Nord',               'SIAO Lille Métropole',     true,  NULL),
    ('33',  'Gironde',            'SIAO 33',                  true,  NULL),
    ('31',  'Haute-Garonne',      'SIAO 31',                  true,  NULL),
    ('44',  'Loire-Atlantique',   'SIAO 44',                  true,  NULL),
    ('67',  'Bas-Rhin',           'SIAO 67',                  true,  NULL),
    ('06',  'Alpes-Maritimes',    'SIAO 06',                  true,  NULL),
    ('974', 'La Réunion',         'SIAO 974',                 false,
     'MIKI ne couvre pas encore La Réunion. Appelez directement le 115 depuis votre téléphone : vous serez mis en relation avec le 115 de La Réunion.');

INSERT INTO communes (code_insee, code_postal, nom, departement) VALUES
    ('75056', '75010', 'Paris', '75'),
    ('75056', '75018', 'Paris', '75'),
    ('75056', '75019', 'Paris', '75'),
    ('93066', '93200', 'Saint-Denis', '93'),
    ('93048', '93100', 'Montreuil', '93'),
    ('93001', '93300', 'Aubervilliers', '93'),
    ('93008', '93000', 'Bobigny', '93'),
    ('13055', '13001', 'Marseille', '13'),
    ('13055', '13003', 'Marseille', '13'),
    ('13001', '13100', 'Aix-en-Provence', '13'),
    ('69123', '69001', 'Lyon', '69'),
    ('69123', '69007', 'Lyon', '69'),
    ('69266', '69100', 'Villeurbanne', '69'),
    ('59350', '59000', 'Lille', '59'),
    ('59512', '59100', 'Roubaix', '59'),
    ('59599', '59200', 'Tourcoing', '59'),
    ('33063', '33000', 'Bordeaux', '33'),
    ('33281', '33700', 'Mérignac', '33'),
    ('31555', '31000', 'Toulouse', '31'),
    ('44109', '44000', 'Nantes', '44'),
    ('44184', '44600', 'Saint-Nazaire', '44'),
    ('67482', '67000', 'Strasbourg', '67'),
    ('06088', '06000', 'Nice', '06'),
    ('06029', '06400', 'Cannes', '06'),
    -- homonyme volontaire : Saint-Denis existe aussi à La Réunion (non couvert)
    ('97411', '97400', 'Saint-Denis', '974');

INSERT INTO profils (code, libelle) VALUES
    ('famille_enfants', 'Famille avec enfant(s)'),
    ('femme_enceinte',  'Femme enceinte'),
    ('personne_agee',   'Personne de 65 ans ou plus'),
    ('femme_seule',     'Femme seule'),
    ('couple',          'Couple sans enfant'),
    ('homme_seul',      'Homme seul');

INSERT INTO causes (code, libelle) VALUES
    ('expulsion_locative',    'Expulsion locative'),
    ('rupture_familiale',     'Rupture familiale'),
    ('violences_conjugales',  'Violences conjugales ou intrafamiliales'),
    ('separation',            'Séparation'),
    ('sortie_institution',    'Sortie d''institution (hôpital, prison, ASE)'),
    ('perte_ressources',      'Perte d''emploi ou de ressources'),
    ('arrivee_migratoire',    'Arrivée récente en France sans solution'),
    ('logement_insalubre',    'Logement insalubre ou sinistré'),
    ('autre',                 'Autre'),
    ('inconnue',              'Non précisée');

-- Ordre par défaut, puis variantes locales pour montrer que chaque 115 a ses règles.
INSERT INTO regles_departement (departement, version, ordre_profils, criteres_securite)
SELECT code, 1,
       ARRAY['famille_enfants', 'femme_enceinte', 'personne_agee', 'femme_seule', 'couple', 'homme_seul'],
       ARRAY['danger_vital', 'violences', 'mineur_seul']
FROM departements WHERE couvert;

UPDATE regles_departement   -- le 93 fait passer les femmes enceintes avant les familles
SET ordre_profils = ARRAY['femme_enceinte', 'famille_enfants', 'femme_seule', 'personne_agee', 'couple', 'homme_seul']
WHERE departement = '93';

UPDATE regles_departement   -- Paris donne la priorité aux personnes âgées après les familles
SET ordre_profils = ARRAY['famille_enfants', 'personne_agee', 'femme_enceinte', 'femme_seule', 'couple', 'homme_seul']
WHERE departement = '75';

UPDATE regles_departement   -- le 13 ajoute les problèmes de santé aux règles de sécurité
SET criteres_securite = ARRAY['danger_vital', 'violences', 'mineur_seul', 'probleme_sante']
WHERE departement = '13';

-- Places : un total par profil, puis des disponibilités volontairement contrastées.
INSERT INTO places (departement, profil, total, disponibles)
SELECT d.code, p.code, t.total, t.total / 3
FROM departements d
CROSS JOIN profils p
JOIN (VALUES ('famille_enfants', 12), ('femme_enceinte', 6), ('personne_agee', 6),
             ('femme_seule', 10), ('couple', 6), ('homme_seul', 20)) AS t (profil, total)
  ON t.profil = p.code
WHERE d.couvert;

-- Scénario « famille complète » : plus aucune place famille dans le 93.
UPDATE places SET disponibles = 0 WHERE departement = '93' AND profil = 'famille_enfants';
UPDATE places SET disponibles = 0 WHERE departement = '75' AND profil = 'couple';
UPDATE places SET disponibles = 0 WHERE departement = '13' AND profil = 'homme_seul';
UPDATE places SET disponibles = 1 WHERE departement = '93' AND profil = 'femme_enceinte';

-- Aucun écoutant ne parle tigrinya ('ti') : cas « langue non couverte ».
INSERT INTO ecoutants (prenom, departement, langues) VALUES
    ('Claire',  '75', ARRAY['fr', 'en']),
    ('Karim',   '75', ARRAY['fr', 'ar']),
    ('Sofia',   '75', ARRAY['fr', 'es', 'pt']),
    ('Nadia',   '93', ARRAY['fr', 'ar', 'en']),
    ('Ioana',   '93', ARRAY['fr', 'ro']),
    ('Moussa',  '93', ARRAY['fr', 'wo', 'en']),
    ('Julien',  '13', ARRAY['fr', 'en']),
    ('Leila',   '13', ARRAY['fr', 'ar']),
    ('Thomas',  '69', ARRAY['fr', 'en']),
    ('Olena',   '69', ARRAY['fr', 'uk', 'ru']),
    ('Camille', '59', ARRAY['fr', 'en', 'nl']),
    ('Yassine', '59', ARRAY['fr', 'ar']),
    ('Marie',   '33', ARRAY['fr', 'es']),
    ('Hugo',    '33', ARRAY['fr', 'en']),
    ('Ahmad',   '31', ARRAY['fr', 'ps', 'fa']),
    ('Lucie',   '31', ARRAY['fr', 'en']),
    ('Pauline', '44', ARRAY['fr', 'en']),
    ('Bilal',   '44', ARRAY['fr', 'ar']),
    ('Anna',    '67', ARRAY['fr', 'de', 'en']),
    ('Emre',    '67', ARRAY['fr', 'tr']),
    ('Giulia',  '06', ARRAY['fr', 'it', 'en']),
    ('Romain',  '06', ARRAY['fr', 'ru']);
