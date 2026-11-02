def test_geo_stack_imports():
    import geopandas
    import pydantic
    import rasterio

    import hive_platform

    assert pydantic.VERSION.startswith("2")
    assert geopandas.__version__
    assert rasterio.__gdal_version__
    assert hive_platform.__name__ == "hive_platform"
    