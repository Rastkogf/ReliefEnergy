# coding=utf-8
"""Lifecycle tests for processing provider: initGui -> unload."""

from qgis.core import QgsApplication


def test_init_gui_registers_provider(qgis_app):
    """initGui registers the processing provider."""
    from relief_energy import ReliefEnergyPlugin
    plugin = ReliefEnergyPlugin()
    plugin.initGui()
    provider_ids = [p.id() for p in QgsApplication.processingRegistry().providers()]
    assert any("Relief energy".lower() in pid for pid in provider_ids)
    plugin.unload()


def test_unload_removes_provider(qgis_app):
    """unload removes the provider from the processing registry."""
    from relief_energy import ReliefEnergyPlugin
    plugin = ReliefEnergyPlugin()
    plugin.initGui()
    count_before = len(QgsApplication.processingRegistry().providers())
    plugin.unload()
    count_after = len(QgsApplication.processingRegistry().providers())
    assert count_after < count_before
