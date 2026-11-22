-- One row per apiary, with its crop and how many hives it carries.
select
    a.apiary_id,
    a.beekeeper_id,
    a.commune_name,
    a.insee_code,
    a.crop_variety,
    a.crop_variety_source,      -- rpg_code | imputed: is the crop a fact or an assumption?
    a.parcel_ha,
    a.n_hives,
    a.lat,
    a.lon
from {{ ref('stg_registry__apiaries') }} as a
