CREATE OR REPLACE TABLE sub_temporal AS 
  SELECT DISTINCT
          st.sub_id,
          st.ban,
          st.dt_eff,
          st.dt_end,    
  FROM 
          src_sub_temporal st
