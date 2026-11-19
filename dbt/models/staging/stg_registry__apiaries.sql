-- One row per apiary. Geometry is left out: lat/lon are enough here (WGS84).
select
    cast(apiary_id as varchar)              as apiary_id,
    cast(beekeeper_id as varchar)           as beekeeper_id,
    cast(id_parcel as varchar)              as id_parcel,
    cast(crop_variety as varchar)           as crop_variety,
    cast(crop_variety_source as varchar)    as crop_variety_source,   -- rpg_code | imputed
    cast(parcel_ha as double)               as parcel_ha,
    cast(n_hives as integer)                as n_hives,
    cast(insee_code as varchar)             as insee_code,
    cast(commune_name as varchar)           as commune_name,
    cast(lat as double)                     as lat,
    cast(lon as double)                     as lon
from {{ source('registry', 'apiaries') }}
