-- Les 101 départements. Ceux déjà présents (et La Réunion, non couverte pour la démo) sont gardés.
INSERT INTO departements (code, nom, siao) VALUES
    ('01','Ain','SIAO Ain'), ('02','Aisne','SIAO Aisne'), ('03','Allier','SIAO Allier'),
    ('04','Alpes-de-Haute-Provence','SIAO Alpes-de-Haute-Provence'), ('05','Hautes-Alpes','SIAO Hautes-Alpes'),
    ('06','Alpes-Maritimes','SIAO 06'), ('07','Ardèche','SIAO Ardèche'), ('08','Ardennes','SIAO Ardennes'),
    ('09','Ariège','SIAO Ariège'), ('10','Aube','SIAO Aube'), ('11','Aude','SIAO Aude'),
    ('12','Aveyron','SIAO Aveyron'), ('13','Bouches-du-Rhône','SIAO 13'), ('14','Calvados','SIAO Calvados'),
    ('15','Cantal','SIAO Cantal'), ('16','Charente','SIAO Charente'), ('17','Charente-Maritime','SIAO Charente-Maritime'),
    ('18','Cher','SIAO Cher'), ('19','Corrèze','SIAO Corrèze'), ('2A','Corse-du-Sud','SIAO Corse-du-Sud'),
    ('2B','Haute-Corse','SIAO Haute-Corse'), ('21','Côte-d''Or','SIAO Côte-d''Or'),
    ('22','Côtes-d''Armor','SIAO Côtes-d''Armor'), ('23','Creuse','SIAO Creuse'), ('24','Dordogne','SIAO Dordogne'),
    ('25','Doubs','SIAO Doubs'), ('26','Drôme','SIAO Drôme'), ('27','Eure','SIAO Eure'),
    ('28','Eure-et-Loir','SIAO Eure-et-Loir'), ('29','Finistère','SIAO Finistère'), ('30','Gard','SIAO Gard'),
    ('31','Haute-Garonne','SIAO 31'), ('32','Gers','SIAO Gers'), ('33','Gironde','SIAO 33'),
    ('34','Hérault','SIAO Hérault'), ('35','Ille-et-Vilaine','SIAO Ille-et-Vilaine'), ('36','Indre','SIAO Indre'),
    ('37','Indre-et-Loire','SIAO Indre-et-Loire'), ('38','Isère','SIAO Isère'), ('39','Jura','SIAO Jura'),
    ('40','Landes','SIAO Landes'), ('41','Loir-et-Cher','SIAO Loir-et-Cher'), ('42','Loire','SIAO Loire'),
    ('43','Haute-Loire','SIAO Haute-Loire'), ('44','Loire-Atlantique','SIAO 44'), ('45','Loiret','SIAO Loiret'),
    ('46','Lot','SIAO Lot'), ('47','Lot-et-Garonne','SIAO Lot-et-Garonne'), ('48','Lozère','SIAO Lozère'),
    ('49','Maine-et-Loire','SIAO Maine-et-Loire'), ('50','Manche','SIAO Manche'), ('51','Marne','SIAO Marne'),
    ('52','Haute-Marne','SIAO Haute-Marne'), ('53','Mayenne','SIAO Mayenne'), ('54','Meurthe-et-Moselle','SIAO Meurthe-et-Moselle'),
    ('55','Meuse','SIAO Meuse'), ('56','Morbihan','SIAO Morbihan'), ('57','Moselle','SIAO Moselle'),
    ('58','Nièvre','SIAO Nièvre'), ('59','Nord','SIAO Lille Métropole'), ('60','Oise','SIAO Oise'),
    ('61','Orne','SIAO Orne'), ('62','Pas-de-Calais','SIAO Pas-de-Calais'), ('63','Puy-de-Dôme','SIAO Puy-de-Dôme'),
    ('64','Pyrénées-Atlantiques','SIAO Pyrénées-Atlantiques'), ('65','Hautes-Pyrénées','SIAO Hautes-Pyrénées'),
    ('66','Pyrénées-Orientales','SIAO Pyrénées-Orientales'), ('67','Bas-Rhin','SIAO 67'), ('68','Haut-Rhin','SIAO Haut-Rhin'),
    ('69','Rhône','Maison de la Veille Sociale'), ('70','Haute-Saône','SIAO Haute-Saône'),
    ('71','Saône-et-Loire','SIAO Saône-et-Loire'), ('72','Sarthe','SIAO Sarthe'), ('73','Savoie','SIAO Savoie'),
    ('74','Haute-Savoie','SIAO Haute-Savoie'), ('75','Paris','SIAO Paris'), ('76','Seine-Maritime','SIAO Seine-Maritime'),
    ('77','Seine-et-Marne','SIAO Seine-et-Marne'), ('78','Yvelines','SIAO Yvelines'), ('79','Deux-Sèvres','SIAO Deux-Sèvres'),
    ('80','Somme','SIAO Somme'), ('81','Tarn','SIAO Tarn'), ('82','Tarn-et-Garonne','SIAO Tarn-et-Garonne'),
    ('83','Var','SIAO Var'), ('84','Vaucluse','SIAO Vaucluse'), ('85','Vendée','SIAO Vendée'),
    ('86','Vienne','SIAO Vienne'), ('87','Haute-Vienne','SIAO Haute-Vienne'), ('88','Vosges','SIAO Vosges'),
    ('89','Yonne','SIAO Yonne'), ('90','Territoire de Belfort','SIAO Territoire de Belfort'),
    ('91','Essonne','SIAO Essonne'), ('92','Hauts-de-Seine','SIAO Hauts-de-Seine'),
    ('93','Seine-Saint-Denis','SIAO 93 (Interlogement)'), ('94','Val-de-Marne','SIAO Val-de-Marne'),
    ('95','Val-d''Oise','SIAO Val-d''Oise'), ('971','Guadeloupe','SIAO Guadeloupe'), ('972','Martinique','SIAO Martinique'),
    ('973','Guyane','SIAO Guyane'), ('974','La Réunion','SIAO 974'), ('976','Mayotte','SIAO Mayotte')
ON CONFLICT (code) DO NOTHING;

-- Règles par défaut pour chaque département couvert qui n'en a pas encore.
INSERT INTO regles_departement (departement, version, ordre_profils, criteres_securite)
SELECT d.code, 1,
       ARRAY['famille_enfants', 'femme_enceinte', 'personne_agee', 'femme_seule', 'couple', 'homme_seul'],
       ARRAY['danger_vital', 'violences', 'mineur_seul']
FROM departements d
WHERE d.couvert AND NOT EXISTS (SELECT 1 FROM regles_departement r WHERE r.departement = d.code);

-- Places : un total proportionné (fictif), un tiers disponible ; quelques profils à 0 pour varier.
INSERT INTO places (departement, profil, total, disponibles)
SELECT d.code, t.profil, t.total, CASE WHEN (ascii(right(d.code, 1)) + length(t.profil)) % 7 = 0 THEN 0 ELSE t.total / 3 END
FROM departements d
CROSS JOIN (VALUES ('famille_enfants', 9), ('femme_enceinte', 3), ('personne_agee', 3),
                   ('femme_seule', 6), ('couple', 3), ('homme_seul', 12)) AS t (profil, total)
WHERE d.couvert AND NOT EXISTS (SELECT 1 FROM places p WHERE p.departement = d.code);

-- Deux écoutants fictifs par nouveau département : français + une seconde langue qui varie.
INSERT INTO ecoutants (prenom, departement, langues)
SELECT (ARRAY['Alice','Bruno','Chloé','David','Emma','Farid','Gaëlle','Hamid','Inès','Jules','Kenza','Louis',
              'Maya','Nicolas','Océane','Pierre','Rania','Samuel','Tania','Victor'])[1 + (n.i + row_number() OVER ()) % 20],
       d.code,
       ARRAY['fr', (ARRAY['en','ar','es','pt','ro','uk','tr','ru','it','de'])[1 + (n.i * 3 + ascii(right(d.code, 1))) % 10]]
FROM departements d CROSS JOIN (VALUES (1), (2)) AS n (i)
WHERE d.couvert AND NOT EXISTS (SELECT 1 FROM ecoutants e WHERE e.departement = d.code);
