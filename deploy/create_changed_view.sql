-- Run this once on marketplace_mercur_uat to create the change-detection view.
--
-- Usage (from the Ubuntu server):
--   psql -h my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com \
--        -U szekiat -d marketplace_mercur_uat \
--        -f deploy/create_changed_view.sql

CREATE OR REPLACE VIEW vw_changed_variants_ai_chatbot AS

-- product core (title, status, handle …)
SELECT DISTINCT p.id AS src_product_id, pv.id AS src_variant_id
FROM product p
JOIN product_variant pv ON pv.product_id = p.id
WHERE p.created_at >= NOW() - INTERVAL '12 hours'
   OR p.updated_at >= NOW() - INTERVAL '12 hours'
   OR p.deleted_at >= NOW() - INTERVAL '12 hours'

UNION

-- variant attributes (manage_inventory, allow_backorder …)
SELECT p.id, pv.id
FROM product_variant pv
JOIN product p ON p.id = pv.product_id
WHERE pv.created_at >= NOW() - INTERVAL '12 hours'
   OR pv.updated_at >= NOW() - INTERVAL '12 hours'
   OR pv.deleted_at >= NOW() - INTERVAL '12 hours'

UNION

-- product type (affects product_type column)
SELECT p.id, pv.id
FROM product_type pt
JOIN product p  ON p.type_id = pt.id
JOIN product_variant pv ON pv.product_id = p.id
WHERE pt.created_at >= NOW() - INTERVAL '12 hours'
   OR pt.updated_at >= NOW() - INTERVAL '12 hours'
   OR pt.deleted_at >= NOW() - INTERVAL '12 hours'

UNION

-- option values (color, spec, condition, brand …)
SELECT p.id, pv.id
FROM product_option_value pov
JOIN product_variant_option pvo ON pvo.option_value_id = pov.id
JOIN product_variant pv ON pv.id = pvo.variant_id
JOIN product p ON p.id = pv.product_id
WHERE pov.created_at >= NOW() - INTERVAL '12 hours'
   OR pov.updated_at >= NOW() - INTERVAL '12 hours'
   OR pov.deleted_at >= NOW() - INTERVAL '12 hours'

UNION

-- price changes
SELECT p.id, pv.id
FROM price pr
JOIN product_variant_price_set pvps ON pvps.price_set_id = pr.price_set_id
JOIN product_variant pv ON pv.id = pvps.variant_id
JOIN product p ON p.id = pv.product_id
WHERE pr.created_at >= NOW() - INTERVAL '12 hours'
   OR pr.updated_at >= NOW() - INTERVAL '12 hours'
   OR pr.deleted_at >= NOW() - INTERVAL '12 hours'

UNION

-- inventory level changes (stock / reserved qty)
SELECT p.id, pv.id
FROM inventory_level il
JOIN product_variant_inventory_item pvii
    ON pvii.inventory_item_id = il.inventory_item_id
JOIN product_variant pv ON pv.id = pvii.variant_id
JOIN product p ON p.id = pv.product_id
WHERE il.created_at >= NOW() - INTERVAL '12 hours'
   OR il.updated_at >= NOW() - INTERVAL '12 hours'
   OR il.deleted_at >= NOW() - INTERVAL '12 hours'
;

-- Verify
SELECT COUNT(*) AS changed_variants_last_12h FROM vw_changed_variants_ai_chatbot;
