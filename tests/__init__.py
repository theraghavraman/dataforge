"""Isolated test package: never inherit live application integration settings.

This affects only test processes. Application modules and the production CLI do
not import this package. Individual tests explicitly provision temporary settings
or monkeypatch their synthetic provider configuration.
"""
import os

for _name in tuple(os.environ):
    if _name.startswith('DATAFORGE_'):
        os.environ.pop(_name, None)
del _name
