def test_geo_stack_imports():
    import geopandas
    import rasterio
    import pydantic
    import hive_platform

    assert pydantic.VERSION.startswith("2")
    assert geopandas.__version__
    assert rasterio.__gdal_version__
    