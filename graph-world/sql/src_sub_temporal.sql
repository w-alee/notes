    WITH
        base as (

            SELECT DISTINCT
                    kws.sub_id,MIN(kws.dt_wo) as dt_wo_min
            FROM 
                    KEY_WO_SUB kws
                
            where 1=1
                AND YEAR(kws.dt_wo) = 2026
                -- AND DATE_TRUNC('month',kws.dt_wo) = '2026-07-01'
            GROUP BY ALL
            )
    SELECT
            kst.KEY_SUB_T,
            kst.key_sub,
            kst.key_acct,
            kst.acct_id,
            kst.ban,
            kst.sub_id,
            row_number() OVER (PARTITION BY kst.sub_id ORDER BY kst.dt_eff) AS rnk_asc,
            row_number() OVER (PARTITION BY kst.sub_id ORDER BY kst.dt_eff DESC) AS rnk_desc,
            kst.dt_eff,
            kst.dt_end,
    FROM
            base b
        INNER JOIN
            KEY_SUB_T kst ON b.sub_id = kst.sub_id
    WHERE   1=1
