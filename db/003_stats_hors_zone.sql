-- Un appel dont le lieu n'a pas pu être déterminé compte aussi dans les statistiques.
ALTER TABLE stats_calls ALTER COLUMN departement DROP NOT NULL;
