-- Dossier en attente faute de place : date à laquelle une place s'est libérée pour lui.
-- Les écoutants sont alertés en direct et la fiche garde la mention jusqu'à son traitement.
ALTER TABLE fiches ADD COLUMN place_liberee_le timestamptz;
