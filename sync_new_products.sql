-- =============================================================================
-- sync_new_products.sql
-- Source: marketplace_mercur_uat @ localhost:5421 (Medusa.js marketplace)
-- Target: ai-grading-uat @ localhost:5431 (FAISS chatbot DB)
-- =============================================================================
-- Rules:
--   * All tables: deleted_at IS NULL filter applied everywhere
--   * Products:   status = 'published' only
--   * Availability:
--       available_qty = stocked_quantity - reserved_quantity
--       is_available:
--         - manage_inventory = TRUE + qty > 0                 → available
--         - allow_backorder  = TRUE + qty <= 0                → available (backorder)
--         - all other cases                                   → not available
--   * Price:  latest price per variant (most recent created_at), any currency/list
--   * Tags → columns:
--       Color, Colour                                   → color
--       Capacity, RAM & Storage, Storage, Size,
--         Phone model, Phone Model, Model,
--         Tenure, Month, Design                         → spec  (concatenated " | ")
--       Cosmetic Grading, Cosmetic Grade,
--         Device Grading, Grade, Condition              → condition
--       Brand                                           → vendor
--       tenure column in target                         → NULL (merged into spec)
-- =============================================================================


-- =============================================================================
-- STEP 1 — Extraction query (run on marketplace_mercur_uat)
-- =============================================================================

WITH latest_price AS (
    SELECT
        pvps.variant_id,
        pr.amount,
        pr.currency_code,
        ROW_NUMBER() OVER (
            PARTITION BY pvps.variant_id
            ORDER BY pr.created_at DESC
        ) AS rn
    FROM product_variant_price_set pvps
    INNER JOIN price pr
        ON pr.price_set_id = pvps.price_set_id
       AND pr.deleted_at   IS NULL
    WHERE pvps.deleted_at IS NULL
)
SELECT
    abs(('x' || substr(md5(p.id),  1, 15))::bit(60)::bigint)   AS product_id,
    abs(('x' || substr(md5(pv.id), 1, 15))::bit(60)::bigint)   AS variant_id,

    p.id                                                         AS src_product_id,
    pv.id                                                        AS src_variant_id,
    p.handle,

    COALESCE(
        MAX(CASE WHEN po.title = 'Brand' THEN pov.value END),
        split_part(p.title, ' ', 1)
    )                                                            AS vendor,

    COALESCE(pt.value, 'Unknown')                                AS product_type,

    -- color
    MAX(CASE
        WHEN po.title IN ('Color', 'Colour') THEN pov.value
    END)                                                         AS color,

    -- spec: capacity / size / phone model / tenure / design all merged here
    NULLIF(STRING_AGG(
        CASE
            WHEN po.title IN (
                'Capacity', 'RAM & Storage', 'Storage', 'Size',
                'Phone model', 'Phone Model', 'Model',
                'Tenure', 'Month', 'Design'
            ) THEN pov.value
        END,
        ' | '
        ORDER BY po.title
    ), '')                                                       AS spec,

    -- condition / grade
    MAX(CASE
        WHEN po.title IN (
            'Cosmetic Grading', 'Cosmetic Grade',
            'Device Grading', 'Grade', 'Condition'
        ) THEN pov.value
    END)                                                         AS condition,

    -- latest price (most recent created_at, any currency / price list)
    lp.amount                                                    AS price,

    COALESCE(SUM(il.stocked_quantity - il.reserved_quantity), 0) AS available_qty,

    CASE
        WHEN pv.manage_inventory = TRUE
             AND COALESCE(SUM(il.stocked_quantity - il.reserved_quantity), 0) > 0
            THEN TRUE
        WHEN pv.allow_backorder = TRUE
             AND COALESCE(SUM(il.stocked_quantity - il.reserved_quantity), 0) <= 0
            THEN TRUE
        ELSE FALSE
    END                                                          AS is_available

FROM product p

JOIN product_variant pv
    ON pv.product_id  = p.id
   AND pv.deleted_at  IS NULL

LEFT JOIN product_type pt
    ON pt.id          = p.type_id
   AND pt.deleted_at  IS NULL

LEFT JOIN product_variant_option pvo
    ON pvo.variant_id = pv.id

LEFT JOIN product_option_value pov
    ON pov.id         = pvo.option_value_id
   AND pov.deleted_at IS NULL

LEFT JOIN product_option po
    ON po.id          = pov.option_id
   AND po.deleted_at  IS NULL

LEFT JOIN product_variant_inventory_item pvii
    ON pvii.variant_id = pv.id
   AND pvii.deleted_at IS NULL

LEFT JOIN inventory_level il
    ON il.inventory_item_id = pvii.inventory_item_id
   AND il.deleted_at        IS NULL

INNER JOIN latest_price lp
    ON lp.variant_id = pv.id
   AND lp.rn         = 1

WHERE p.deleted_at IS NULL
  AND p.status      = 'published'
  AND (
      (
          pv.manage_inventory = TRUE
          AND COALESCE(il.stocked_quantity - il.reserved_quantity, 0) > 0
      )
      OR (
          pv.allow_backorder = TRUE
          AND COALESCE(il.stocked_quantity - il.reserved_quantity, 0) <= 0
      )
  )

GROUP BY
    p.id, pv.id, p.handle, pt.value,
    pv.manage_inventory, pv.allow_backorder,
    lp.amount

ORDER BY p.handle, pv.id;


-- =============================================================================
-- STEP 2 — Create table on FAISS DB (ai-grading-uat) — run once
-- =============================================================================
-- Structure mirrors shopify_variant_new so build_vectors.py can UNION ALL both.
-- The tenure column is retained for schema compatibility but is always NULL
-- for marketplace data (tenure values are stored in spec instead).

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
    tenure          TEXT,               -- always NULL; tenure value lives in spec
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
