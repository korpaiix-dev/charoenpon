-- Receiver name_keyword multi-language support (2026-06-03)
-- Bug fix: Slip2Go rejected when ITMX returned English name only
-- See task #125
UPDATE receiver_accounts
SET name_keyword = "ชาคริต,CHAKHRIT,Chakhrit"
WHERE id=1;

UPDATE receiver_accounts
SET name_keyword = "ณธกฤต,NATHAKRIT,Nathakrit,VONGWONGKARN,Vongwongkarn"
WHERE id=2;
