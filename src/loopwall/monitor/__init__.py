"""CLI monitor: read-only terminal visibility for whoever is running the show.

See PLAN.md §3.6. Staged: Tier 0 (this package's current contents) renders
capture-ring health with no driver/core dependency; Tier 1 (core/timeline,
core/tracks, and the cli driver) adds transport + per-track state on top of
the same `MonitorState` shape, without a rewrite of the render layer.
"""
