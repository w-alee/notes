    WITH
        base_sub as (

            SELECT DISTINCT
                    kws.sub_id,MIN(kws.dt_wo) as dt_wo_min
            FROM 
                    KEY_WO_SUB kws                
            where 1=1
                AND YEAR(kws.dt_wo) = 2026
                -- AND DATE_TRUNC('month',kws.dt_wo) = '2026-07-01'
            GROUP BY ALL
            ),
        base_acct as (
            SELECT DISTINCT 
                    ks.KEY_ACCT,
            FROM
                    base_sub bs
                INNER JOIN
                    KEY_SUB ks ON bs.sub_id = ks.sub_id                    

            WHERE 1=1)
SELECT
        ka.KEY_ACCT,
        ka.acct_id,
        ka.ban,
        aw.DT_WO,
        aw.DT_WO_LIST,
        aw.CNT_SUBS,
        aw.CNT_SUBS_EQUIP,
        aw.CNT_SUBS_SERV,
        aw.NET_WO,
        aw.NET_EQUIP,
        aw.NET_SERV,
        aw.NET_TAX,
        aw.GROSS_WO,
        aw.GROSS_WO_RVSL,
FROM
        base_acct ba
    INNER JOIN
        key_acct ka on ba.KEY_ACCT = ka.KEY_ACCT
    LEFT JOIN 
        ACCT_WO aw on ba.KEY_ACCT = aw.KEY_ACCT
WHERE 1=1
