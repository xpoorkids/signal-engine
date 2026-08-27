"""Offline historical research tools for Signal Engine.

This package is intentionally separate from the production worker. Imports here
must not be required by live ingestion, Discord routing, or execution paths.
"""

