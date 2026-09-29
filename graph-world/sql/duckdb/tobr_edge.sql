CREATE OR REPLACE TABLE tobr_edge AS 
    SELECT 
            f.ban as from_ban,
        	t.ban as to_ban,
            f.sub_id as sub_id,
            t.dt_eff as dt_tobr,
    FROM 
            src_sub_temporal f 
    	inner JOIN
    		src_sub_temporal t on f.sub_id = t.sub_id
    						AND f.rnk_asc = t.rnk_asc + 1
    where 1=1
