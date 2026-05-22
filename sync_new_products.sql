-- =============================================================================
-- sync_new_products.sql
-- Source: marketplace_mercur_uat @ localhost:5421 (Medusa.js marketplace)
-- Target: ai-grading-uat @ localhost:5431 (FAISS chatbot DB)
-- =============================================================================
-- Rules:
--   * All tables: deleted_at IS NULL filter applied everywhere
--   * Products:   status = 'published' only
--   * Availability:
--       available_qty = stocked_quantity - reserved_quantity  (can be negative)
--       is_available:
--         - manage_inventory = FALSE  → always available (bypass stock check)
--         - manage_inventory = TRUE AND allow_backorder = TRUE → available even if qty <= 0
--         - manage_inventory = TRUE AND allow_backorder = FALSE → available only if qty > 0
-- =============================================================================


-- =============================================================================
-- STEP 1 — Preview extraction from NEW DB (run on marketplace_mercur_uat)
-- =============================================================================

SELECT
    -- Deterministic BIGINT hash of string UUIDs
    -- Range ~10^18, safe from collision with Shopify int IDs (~10^13 range)
    abs(('x' || substr(md5(p.id), 1, 15))::bit(60)::bigint)    AS product_id,
    abs(('x' || substr(md5(pv.id), 1, 15))::bit(60)::bigint)   AS variant_id,

    p.id                                                         AS src_product_id,
    pv.id                                                        AS src_variant_id,

    p.handle,
    p.title                                                      AS product_title,

    -- Vendor: prefer Brand option, fall back to first word of product title
    COALESCE(
        MAX(CASE WHEN po.title = 'Brand' THEN pov.value END),
        split_part(p.title, ' ', 1)
    )                                                            AS vendor,

    -- Product type
    COALESCE(pt.value, 'Unknown')                                AS product_type,

    -- Color
    MAX(CASE
        WHEN po.title IN ('Color', 'Colour')     THEN pov.value
    END)                                                         AS color,

    -- Spec / Capacity / Storage
    MAX(CASE
        WHEN po.title IN ('Capacity', 'RAM & Storage', 'Size', 'Storage')
        THEN pov.value
    END)                                                         AS spec,

    -- Condition / Grade
    MAX(CASE
        WHEN po.title IN ('Cosmetic Grading', 'Cosmetic Grade',
                          'Device Grading', 'Grade', 'Condition')
        THEN pov.value
    END)                                                         AS condition,

    -- Base selling price in MYR (lowest price, no promo list)
    MIN(pr.amount)                                               AS price,

    -- Tenure / Monthly installment option
    MAX(CASE
        WHEN po.title IN ('Tenure', 'Month')     THEN pov.value
    END)                                                         AS tenure,

    -- Raw stock values (used by availability logic below)
    pv.manage_inventory,
    pv.allow_backorder,
    COALESCE(SUM(il.stocked_quantity - il.reserved_quantity), 0) AS available_qty,

    -- Derived availability flag:
    --   not manage_inventory          → always available
    --   manage_inventory + backorder  → always available
    --   manage_inventory + no backord → only if stock > 0
    CASE
        WHEN pv.manage_inventory = FALSE
            THEN TRUE
        WHEN pv.manage_inventory = TRUE AND pv.allow_backorder = TRUE
            THEN TRUE
        WHEN pv.manage_inventory = TRUE AND pv.allow_backorder = FALSE
             AND COALESCE(SUM(il.stocked_quantity - il.reserved_quantity), 0) > 0
            THEN TRUE
        ELSE FALSE
    END                                                          AS is_available

FROM product p

JOIN product_variant pv
    ON pv.product_id = p.id
    AND pv.deleted_at IS NULL

LEFT JOIN product_type pt
    ON pt.id = p.type_id
    AND pt.deleted_at IS NULL

LEFT JOIN product_variant_option pvo
    ON pvo.variant_id = pv.id

LEFT JOIN product_option_value pov
    ON pov.id = pvo.option_value_id
    AND pov.deleted_at IS NULL

LEFT JOIN product_option po
    ON po.id = pov.option_id
    AND po.deleted_at IS NULL

LEFT JOIN product_variant_price_set pvps
    ON pvps.variant_id = pv.id
    AND pvps.deleted_at IS NULL

LEFT JOIN price pr
    ON pr.price_set_id = pvps.price_set_id
    AND pr.deleted_at IS NULL
    AND pr.currency_code = 'myr'
    AND pr.price_list_id IS NULL  -- base price only, exclude promo price lists

LEFT JOIN product_variant_inventory_item pvii
    ON pvii.variant_id = pv.id
    AND pvii.deleted_at IS NULL

LEFT JOIN inventory_level il
    ON il.inventory_item_id = pvii.inventory_item_id
    AND il.deleted_at IS NULL

WHERE p.deleted_at IS NULL
  AND p.status    = 'published'

GROUP BY
    p.id, pv.id, p.handle, p.title,
    pt.value,
    pv.manage_inventory, pv.allow_backorder

ORDER BY p.title, pv.id;


-- =============================================================================
-- STEP 2 — Create table on FAISS DB (ai-grading-uat) — run once
-- =============================================================================

CREATE TABLE IF NOT EXISTS marketplace_variant (
    product_id      BIGINT NOT NULL,
    variant_id      BIGINT NOT NULL,
    src_product_id  TEXT,
    src_variant_id  TEXT,
    handle          TEXT,
    vendor          TEXT,
    product_type    TEXT,
    color           TEXT,
    spec            TEXT,
    condition       TEXT,
    price           NUMERIC(12,2),
    tenure          TEXT,
    available_qty   INTEGER  DEFAULT 0,
    is_available    BOOLEAN  DEFAULT FALSE,
    synced_at       TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (product_id, variant_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS marketplace_variant_src_idx
    ON marketplace_variant (src_variant_id)
    WHERE src_variant_id IS NOT NULL;


-- =============================================================================
-- STEP 3 — UPSERT (automated by sync_new_products.py)
-- =============================================================================

INSERT INTO marketplace_variant (
    product_id, variant_id,
    src_product_id, src_variant_id,
    handle, vendor, product_type,
    color, spec, condition, price, tenure,
    available_qty, is_available
)
VALUES %s
ON CONFLICT (product_id, variant_id) DO UPDATE SET
    src_product_id  = EXCLUDED.src_product_id,
    src_variant_id  = EXCLUDED.src_variant_id,
    handle          = EXCLUDED.handle,
    vendor          = EXCLUDED.vendor,
    product_type    = EXCLUDED.product_type,
    color           = EXCLUDED.color,
    spec            = EXCLUDED.spec,
    condition       = EXCLUDED.condition,
    price           = EXCLUDED.price,
    tenure          = EXCLUDED.tenure,
    available_qty   = EXCLUDED.available_qty,
    is_available    = EXCLUDED.is_available,
    synced_at       = now();
